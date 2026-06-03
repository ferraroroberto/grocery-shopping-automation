"""Mercadona add-to-cart handler.

Mercadona's product page (``tienda.mercadona.es/product/...``) is a React app
with stable ``data-testid`` attributes. The add control has two states:

* **Not in cart** — a single "Añadir al carro" button is shown; the +/-
  picker is rendered but hidden.
* **In cart** — the +/- picker (``button-picker``) is shown with the current
  unit count; the "Añadir al carro" button is hidden.

Mercadona counts **units**: 3 of the same product means the header cart badge
goes up by 3. Every mutating click is verified by re-reading the on-page unit
count *and* the header badge — the operator reports that picker clicks
sometimes silently no-op, so clicks are retried until the count actually moves.

All selectors live in :data:`SELECTORS` so a DOM change is a one-line fix.
Selectors verified live on 2026-05-14.
"""

from __future__ import annotations

import logging
import re
import time

from playwright.sync_api import Page

from automation.browser import goto_with_login_check, human_delay
from automation.errors import AddToCartFailed, OutOfStockError
from automation.models import CartItem

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Selector map — verified live 2026-05-14 against tienda.mercadona.es.
# Prefer data-testid; these are stable across product categories.
# ─────────────────────────────────────────────────────────────────────────────
SELECTORS = {
    # Right-hand info column of the main product (scopes us away from the
    # "related products" carousel, which reuses the same testids).
    "product_info": "[data-testid='private-product-detail-info']",
    # "Añadir al carro" — shown only when the item is not yet in the cart.
    "add_button": (
        "[data-testid='private-product-detail-info'] "
        "button[data-testid='product-quantity-button']"
    ),
    # +/- picker buttons — shown only when the item is already in the cart.
    "increase": (
        "[data-testid='private-product-detail-info'] "
        "button[data-testid='button-picker-increase']"
    ),
    "decrease": (
        "[data-testid='private-product-detail-info'] "
        "button[data-testid='button-picker-decrease']"
    ),
    # On-page "N ud." count for this product.
    "product_qty": (
        "[data-testid='private-product-detail-info'] "
        "[data-testid='product-feedback__quantity']"
    ),
    # Header cart badge — total units across the whole cart. Absent when empty.
    "cart_badge": "[data-testid='cart-button-quantity']",
    # "Tienes cambios sin guardar en tu pedido" modal — "Más tarde" dismisses
    # it without touching the cart (keeps the existing order intact).
    "unsaved_modal_dismiss": ".ui-modal button:has-text('Más tarde')",
    # ── Cart-panel selectors (clean mode, issue #19) ─────────────────────────
    # The cart contents are not enumerable from a product page, so emptying the
    # cart means opening the side cart and driving each line down to zero.
    # `cart_button` opens the side cart drawer. The decrease control MUST be
    # scoped to `cart-product-cell`: the home page also renders ~50 hidden
    # carousel/product-detail pickers reusing `button-picker-decrease`, and an
    # unscoped `.first` resolves to one of those (display:none → click never
    # becomes actionable). Verified live 2026-06-03 (issue #19).
    "cart_button": "button[data-testid='cart-button']",
    "cart_line_decrease": (
        "[data-testid='cart-product-cell'] "
        "button[data-testid='button-picker-decrease']"
    ),
}

# Mercadona storefront home — a stable page that always renders the header cart
# badge, used to snapshot the whole-cart total before/after a run.
HOME_URL = "https://tienda.mercadona.es/"

# How long to wait for a click to visibly take effect before retrying it.
_CLICK_EFFECT_TIMEOUT_S = 4.0
_CLICK_POLL_S = 0.4
_CLICK_ATTEMPTS = 3


def _read_int(text: str) -> int:
    """Return the first integer found in `text`, or 0 if there is none."""
    match = re.search(r"\d+", text or "")
    return int(match.group()) if match else 0


def _read_product_qty(page: Page) -> int:
    """Return how many units of this product are currently in the cart."""
    loc = page.locator(SELECTORS["product_qty"])
    if loc.count() == 0:
        return 0
    return _read_int(loc.first.inner_text())


def _read_cart_badge(page: Page) -> int:
    """Return the header cart badge total (units across the whole cart)."""
    loc = page.locator(SELECTORS["cart_badge"])
    if loc.count() == 0:
        return 0
    return _read_int(loc.first.inner_text())


def _dismiss_unsaved_modal(page: Page) -> None:
    """Dismiss the "unsaved changes" modal if present, keeping the cart intact."""
    loc = page.locator(SELECTORS["unsaved_modal_dismiss"])
    try:
        if loc.count() > 0 and loc.first.is_visible():
            loc.first.click()
            logger.debug("dismissed 'unsaved changes' modal via 'Más tarde'")
            human_delay(0.5, 1.0)
    except Exception:  # noqa: BLE001 — modal handling is strictly best-effort
        pass


def _click_until(page: Page, selector: str, moved, item: CartItem, *, what: str) -> None:
    """Click `selector` and wait until `moved()` is true, retrying if it no-ops.

    The operator reports that Mercadona's picker clicks sometimes register and
    sometimes don't; this retries the click until the count actually moves.

    Raises:
        AddToCartFailed: the action never took effect after all attempts.
    """
    loc = page.locator(selector).first
    for attempt in range(1, _CLICK_ATTEMPTS + 1):
        loc.click()
        deadline = time.time() + _CLICK_EFFECT_TIMEOUT_S
        while time.time() < deadline:
            if moved():
                return
            time.sleep(_CLICK_POLL_S)
        logger.warning(
            "⚠️ '%s' click did not register (attempt %d/%d) for '%s'",
            what, attempt, _CLICK_ATTEMPTS, item.comida,
        )
        human_delay(0.6, 1.2)
    raise AddToCartFailed(item, f"'{what}' did not take effect after {_CLICK_ATTEMPTS} attempts")


def _set_quantity(page: Page, item: CartItem, target: int) -> None:
    """Drive the add button / +/- picker until this product's cart qty == target."""
    qty = _read_product_qty(page)

    if qty == 0 and target > 0:
        _click_until(
            page, SELECTORS["add_button"],
            lambda: _read_product_qty(page) >= 1, item, what="add",
        )
        qty = _read_product_qty(page)
        human_delay()

    guard = 0
    while qty < target:
        before = qty
        _click_until(
            page, SELECTORS["increase"],
            lambda b=before: _read_product_qty(page) > b, item, what="increase",
        )
        qty = _read_product_qty(page)
        human_delay()
        guard += 1
        if guard > target + 5:
            raise AddToCartFailed(item, "increment loop stalled")

    guard = 0
    while qty > target:
        before = qty
        _click_until(
            page, SELECTORS["decrease"],
            lambda b=before: _read_product_qty(page) < b, item, what="decrease",
        )
        qty = _read_product_qty(page)
        human_delay()
        guard += 1
        if guard > before + 5:
            raise AddToCartFailed(item, "decrement loop stalled")


def add_to_cart(page: Page, item: CartItem) -> None:
    """Add `item` to the Mercadona cart at quantity `item.comprar`.

    Idempotent: sets this product's cart line to exactly ``item.comprar`` units,
    so re-running a partially-completed run is safe. Other items already in the
    cart are left untouched.

    Raises:
        SessionExpiredError: navigation was redirected to the login page.
        OutOfStockError: the product page shows no add control.
        AddToCartFailed: a click never registered, or the cart counter did not
            move by the expected amount.
    """
    logger.info("🛒 [mercadona] %s ×%d", item.comida, item.comprar)
    goto_with_login_check(page, "mercadona", item.buscador)
    human_delay(1.5, 2.5)
    _dismiss_unsaved_modal(page)

    if page.locator(SELECTORS["product_info"]).count() == 0:
        raise AddToCartFailed(item, "product info column not found — page layout unexpected")

    has_add = page.locator(SELECTORS["add_button"]).count() > 0
    has_picker = page.locator(SELECTORS["increase"]).count() > 0
    if not has_add and not has_picker:
        raise OutOfStockError(item)

    cart_before = _read_cart_badge(page)
    qty_before = _read_product_qty(page)
    target = item.comprar

    _set_quantity(page, item, target)

    # Give the header badge a moment to catch up, then verify both counters.
    human_delay(1.5, 2.5)
    qty_after = _read_product_qty(page)
    cart_after = _read_cart_badge(page)
    expected_cart = cart_before + (target - qty_before)

    if qty_after != target:
        raise AddToCartFailed(
            item, f"product shows {qty_after} ud. in cart, expected {target}"
        )
    if cart_after != expected_cart:
        raise AddToCartFailed(
            item,
            f"header cart badge is {cart_after}, expected {expected_cart} "
            f"(was {cart_before}, product delta {target - qty_before})",
        )

    logger.info(
        "✅ [mercadona] %s — %d ud. in cart (badge %d → %d)",
        item.comida, target, cart_before, cart_after,
    )


# ─────────────────────────────────────────────────────────────────────────────
# Whole-cart helpers (run-level before/after snapshot + clean mode, issue #19).
# ─────────────────────────────────────────────────────────────────────────────

# How many decrement clicks to attempt before giving up on emptying the cart —
# a safety net so a stuck line can never spin forever. Sized well above any
# realistic cart total.
_CLEAR_MAX_CLICKS = 200


def read_cart_total(page: Page) -> int:
    """Return the total units across the whole Mercadona cart.

    Navigates to the storefront home (where the header badge always renders)
    and reads the badge. Used for the run-level before/after snapshot.

    Raises:
        SessionExpiredError: navigation was redirected to the login page.
    """
    goto_with_login_check(page, "mercadona", HOME_URL)
    human_delay(1.5, 2.5)
    _dismiss_unsaved_modal(page)
    return _read_cart_badge(page)


def clear_cart(page: Page) -> int:
    """Empty the Mercadona cart completely, returning the unit count removed.

    Opens the side cart and drives every line's +/- picker down until the
    header badge reaches zero. Idempotent: a no-op (returns 0) on an already
    empty cart.

    Raises:
        SessionExpiredError: navigation was redirected to the login page.
        AddToCartFailed: the badge never reached zero within the click budget.
    """
    goto_with_login_check(page, "mercadona", HOME_URL)
    human_delay(1.5, 2.5)
    _dismiss_unsaved_modal(page)

    before = _read_cart_badge(page)
    if before == 0:
        logger.info("🛒 [mercadona] cart already empty — nothing to clear")
        return 0

    logger.info("🧹 [mercadona] clearing cart — %d unit(s) to remove", before)
    page.locator(SELECTORS["cart_button"]).first.click()
    human_delay(1.0, 1.8)

    clicks = 0
    while _read_cart_badge(page) > 0:
        decrease = page.locator(SELECTORS["cart_line_decrease"]).first
        if decrease.count() == 0:
            raise AddToCartFailed(
                CartItem("mercadona", "(clear cart)", 0, ""),
                "no cart-line decrease control found while clearing the cart",
            )
        decrease.click()
        human_delay(0.4, 0.8)
        clicks += 1
        if clicks > _CLEAR_MAX_CLICKS:
            raise AddToCartFailed(
                CartItem("mercadona", "(clear cart)", 0, ""),
                f"cart still not empty after {_CLEAR_MAX_CLICKS} decrement clicks",
            )

    logger.info("✅ [mercadona] cart cleared (%d unit(s) removed)", before)
    return before
