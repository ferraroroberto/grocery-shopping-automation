"""Ametller Origen add-to-cart handler.

Ametller Origen runs on **VTEX**. Its product page has a numeric stepper plus
an "Añadir" button: unlike Mercadona, you set the quantity *first* and then add
in one operation.

Two VTEX modals can interrupt the flow; both are handled:

* **Cart-restore modal** ("Ya tienes una cesta en curso") — appears at session
  start. We always click "MANTENER CESTA ANTERIOR" to keep the existing cart.
  Clicking it reloads the page.
* **Delivery postal-code modal** — appears the first time something is added
  until a delivery option is saved into the profile. We fill the postal code
  from ``config.json`` (``automation.ametller_postal_code``), let the delivery
  options render, and click "GUARDAR OPCIÓN DE ENTREGA" (Standard delivery is
  pre-selected). Once saved it persists in the Chrome profile.

The handler is **idempotent**: it reads the current cart quantity first and
only adds the missing units, so a re-run does not double-add. It never reduces
a line that already has more than the shopping list asks for.

**Verification (issue #10).** The cart is read via VTEX's
`/api/checkout/pub/orderForm` JSON API — the authoritative source the storefront
itself uses. The minicart drawer DOM is *not* a reliable view: in #8's
investigation it silently omitted real lines, leading the handler to retry adds
that had actually succeeded and accumulate inflated quantities. The orderForm
endpoint returns every line with its true quantity for the live session, so
both the idempotency check ("already have N") and post-add verification ("add
landed") are accurate.

Lines are matched by VTEX **`productId`** — read from the product page's
``<meta property="product:retailer_part_no">`` tag and compared against
``items[].productId`` in the orderForm response. This is language-independent:
the product page can render the title in Spanish while the orderForm line
comes back in Catalan ("Zanahoria" vs "Pastanaga") — matching by name silently
fails and would trigger a double-add on retry. After firing "Añadir" we poll
the orderForm a few times with a short delay, returning as soon as the target
quantity is observed, so a brief propagation lag is absorbed and a retry only
fires when the add genuinely did not land.

A product page that renders an empty shell (no title, add button or stepper) is
reported as :class:`ProductUnavailableError` — an end-of-run *alert* to fix on
the data side, not a hard error. That single failure shape is the only one we
can't drive past from the client.

All selectors live in :data:`SELECTORS`. Verified live on 2026-05-15.
"""

from __future__ import annotations

import logging
import re
import sys
import time
from pathlib import Path

from playwright.sync_api import Page

# src/ is a sibling package at the repo root — needed for the config lookup.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data import CONFIG  # noqa: E402

from automation.browser import goto_with_login_check, human_delay  # noqa: E402
from automation.errors import (  # noqa: E402
    AddToCartFailed,
    OutOfStockError,
    ProductUnavailableError,
)
from automation.models import CartItem  # noqa: E402

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Selector map — verified live 2026-05-15 against www.ametllerorigen.com (VTEX).
# ─────────────────────────────────────────────────────────────────────────────
SELECTORS = {
    # "MANTENER CESTA ANTERIOR" link in the cart-restore modal.
    "cart_restore_keep": (
        "a.ametllerorigen-ametller-store-user-session-component-0-x-recoverCartButton"
    ),
    # Product title — matches the name shown on each cart line.
    "product_name": "h1.vtex-store-components-3-x-productNameContainer",
    # Product-page quantity stepper (one per product page).
    "stepper_input": ".vtex-numeric-stepper-container .vtex-numeric-stepper__input",
    "stepper_plus": ".vtex-numeric-stepper-container .vtex-numeric-stepper__plus-button",
    "stepper_minus": ".vtex-numeric-stepper-container .vtex-numeric-stepper__minus-button",
    # Main "Añadir" button — scoped by its inner text node so the related-product
    # carousel buttons and the minicart checkout button are not matched.
    "add_button": "button:has(.ametllerorigen-add-to-cart-button-1-x-buttonText)",
    # Delivery postal-code modal.
    "zip_input": "input#zipcode",
    "zip_save": ".vtex-modal__modal button:has-text('GUARDAR')",
    # Any VTEX modal overlay — sometimes left over after a previous action.
    "modal_overlay": ".vtex-modal__overlay",
}

# VTEX hydrates slowly — these waits are deliberately generous.
_NAV_SETW = (3.0, 4.0)
_ACTION_SETW = (2.5, 3.5)

# How many times to fire "Añadir" before giving up. With productId-matched
# orderForm verification this almost never fires beyond 1; the retry is kept
# as a safety net for genuinely transient misses (e.g. the postal-code modal
# eating the first click). Each retry reloads the product page to clear stuck
# UI state.
_MAX_ADD_ATTEMPTS = 2

# VTEX's authoritative cart-state JSON endpoint. Uses the current session
# cookies, returns every line in the cart with its true quantity. See module
# docstring (issue #10) for why this replaced the minicart-drawer DOM reading.
_ORDERFORM_URL = "https://www.ametllerorigen.com/api/checkout/pub/orderForm"

# After Añadir, VTEX needs a moment to refresh the orderForm. Poll up to
# this many times with this gap, returning as soon as the target qty appears —
# absorbs the propagation lag without making us wait the full duration when
# the add lands fast.
_CART_POLL_COUNT = 6
_CART_POLL_INTERVAL_S = 1.0


def _int(text: object) -> int:
    """First integer in `text`, or 0."""
    match = re.search(r"\d+", str(text or ""))
    return int(match.group()) if match else 0


def _postal_code() -> str:
    """Delivery postal code from config.json (``automation.ametller_postal_code``)."""
    return str(CONFIG.get("automation", {}).get("ametller_postal_code", "")).strip()


# ─────────────────────────────────────────────────────────────────────────────
# Cart-state reading via VTEX orderForm (issue #10 — the reliable source).
# ─────────────────────────────────────────────────────────────────────────────


def _cart_items_via_orderform(page: Page) -> list[dict]:
    """Fetch every cart line via ``/api/checkout/pub/orderForm``.

    Uses ``page.request`` so the call rides the live session cookies. Returns
    a list of ``{"productId", "refId", "name", "qty"}`` dicts, empty on any
    network/JSON error (a transient miss should not crash the run; the caller's
    quantity check simply finds 0 and triggers the add). The endpoint needs a
    warmed-up session — visiting any storefront page first (which the handler
    already does for the product itself) sets the orderForm cookie.
    """
    try:
        resp = page.request.get(_ORDERFORM_URL, timeout=15000)
        if not resp.ok:
            logger.debug("orderForm GET %d — treating cart as empty", resp.status)
            return []
        data = resp.json()
    except Exception as err:  # noqa: BLE001 — best-effort, fall back to empty
        logger.debug("orderForm fetch raised %s — treating cart as empty", err)
        return []
    out: list[dict] = []
    for it in data.get("items", []) or []:
        out.append({
            "productId": str(it.get("productId") or ""),
            "refId": str(it.get("refId") or ""),
            "name": str(it.get("name") or ""),
            "qty": _int(it.get("quantity")),
        })
    return out


def _cart_qty_by_id(page: Page, product_id: str) -> int:
    """Return the total cart units for VTEX `product_id` (0 when absent).

    VTEX can split a single product across multiple lines (different sellers or
    unit packs), so all matches are summed. `productId` is the canonical match —
    `name` falls apart across Spanish/Catalan storefronts (issue #10).
    """
    if not product_id:
        return 0
    total = 0
    for line in _cart_items_via_orderform(page):
        if line["productId"] == str(product_id):
            total += line["qty"]
    return total


def _cart_qty_settled(
    page: Page, product_id: str, *, target: int
) -> int:
    """Poll the orderForm until `target` is reached, or up to the poll budget.

    VTEX's orderForm refresh after an add is fast but not instant; a single
    read can briefly return the pre-add quantity. Polling returns as soon as
    ``qty >= target``, so a successful add is recognised quickly and a genuine
    miss still gets the full wait before we conclude the add did not land.
    """
    qty = _cart_qty_by_id(page, product_id)
    if qty >= target:
        return qty
    for _ in range(_CART_POLL_COUNT):
        time.sleep(_CART_POLL_INTERVAL_S)
        qty = _cart_qty_by_id(page, product_id)
        if qty >= target:
            return qty
    return qty


# ─────────────────────────────────────────────────────────────────────────────
# Modals + defensive cleanup.
# ─────────────────────────────────────────────────────────────────────────────


def _handle_cart_restore_modal(page: Page) -> None:
    """Keep the existing cart if the cart-restore modal is showing. May reload."""
    keep = page.locator(SELECTORS["cart_restore_keep"])
    try:
        if keep.count() > 0 and keep.first.is_visible():
            logger.info("ℹ️ [ametller] cart-restore modal — keeping the previous cart")
            keep.first.click()
            try:
                page.wait_for_load_state("domcontentloaded", timeout=15000)
            except Exception:  # noqa: BLE001 — reload timing is best-effort
                pass
            human_delay(*_NAV_SETW)
    except Exception:  # noqa: BLE001 — modal handling is strictly best-effort
        pass


def _handle_zip_modal(page: Page, item: CartItem) -> None:
    """Fill the delivery postal-code modal and save the (pre-selected) option.

    No-op when the modal is not present. Raises if a postal code is needed but
    none is configured.
    """
    zip_input = page.locator(SELECTORS["zip_input"])
    try:
        present = zip_input.count() > 0 and zip_input.first.is_visible()
    except Exception:  # noqa: BLE001
        present = False
    if not present:
        return

    postal = _postal_code()
    if not postal:
        raise AddToCartFailed(
            item,
            "Ametller asked for a delivery postal code but "
            "'automation.ametller_postal_code' is not set in src/config.json",
        )
    logger.info("ℹ️ [ametller] delivery postal-code modal — entering %s", postal)
    zip_input.first.click()
    zip_input.first.fill("")
    zip_input.first.type(postal, delay=120)
    # Delivery options render after a valid code; Standard is pre-selected.
    human_delay(3.5, 4.5)
    save = page.locator(SELECTORS["zip_save"])
    if save.count() == 0:
        raise AddToCartFailed(item, "postal-code modal: 'GUARDAR' button not found")
    save.first.click()
    human_delay(*_NAV_SETW)


def _dismiss_overlays(page: Page) -> None:
    """Best-effort: dismiss any stray VTEX modal overlay covering the page.

    The minicart drawer is no longer touched by this handler (issue #10), but
    other overlays (carousels, marketing modals) can still appear after the
    cart-restore reload and intercept clicks. Pressing Escape closes them
    cleanly; the postal-code modal is deliberately left alone — it must be
    *filled*, not dismissed.
    """
    try:
        overlay = page.locator(SELECTORS["modal_overlay"])
        zip_input = page.locator(SELECTORS["zip_input"])
        if (
            overlay.count() > 0
            and overlay.first.is_visible()
            and zip_input.count() == 0
        ):
            logger.info("ℹ️ [ametller] dismissing a stray modal overlay")
            page.keyboard.press("Escape")
            human_delay(0.6, 1.0)
    except Exception:  # noqa: BLE001 — overlay cleanup is strictly best-effort
        pass


# ─────────────────────────────────────────────────────────────────────────────
# Product-page helpers.
# ─────────────────────────────────────────────────────────────────────────────


def _read_product_name(page: Page) -> str:
    """Return the product title shown on the page (empty string if missing)."""
    loc = page.locator(SELECTORS["product_name"])
    if loc.count() == 0:
        return ""
    return loc.first.inner_text().strip()


def _read_product_id(page: Page) -> str:
    """Return the VTEX productId from the product page (empty if not present).

    Reads ``<meta property="product:retailer_part_no">`` — the same numeric
    string that appears in ``items[].productId`` in the orderForm. This is the
    language-independent join key between the page and the cart (issue #10).
    """
    try:
        return (
            page.evaluate(
                "() => { const m = document.querySelector('meta[property=\"product:retailer_part_no\"]');"
                " return m ? m.getAttribute('content') : ''; }"
            )
            or ""
        ).strip()
    except Exception:  # noqa: BLE001
        return ""


def _set_stepper(page: Page, value: int, item: CartItem) -> None:
    """Set the product-page quantity stepper to `value`."""
    inp = page.locator(SELECTORS["stepper_input"]).first
    inp.scroll_into_view_if_needed()
    try:
        inp.fill(str(value))
        inp.press("Tab")
        human_delay(0.8, 1.2)
    except Exception:  # noqa: BLE001 — fall through to the +/- buttons
        pass

    current = _int(inp.input_value())
    guard = 0
    while current < value:
        page.locator(SELECTORS["stepper_plus"]).first.click()
        human_delay(0.4, 0.8)
        current = _int(inp.input_value())
        guard += 1
        if guard > value + 10:
            raise AddToCartFailed(item, f"could not set quantity stepper to {value}")
    while current > value:
        page.locator(SELECTORS["stepper_minus"]).first.click()
        human_delay(0.4, 0.8)
        current = _int(inp.input_value())
        guard += 1
        if guard > value + 20:
            raise AddToCartFailed(item, f"could not set quantity stepper to {value}")


# ─────────────────────────────────────────────────────────────────────────────
# The handler.
# ─────────────────────────────────────────────────────────────────────────────


def add_to_cart(page: Page, item: CartItem) -> None:
    """Add `item` to the Ametller Origen cart so its line totals `item.comprar`.

    Idempotent: reads the current cart quantity via orderForm and only adds the
    missing units. Never reduces a line that already holds more than
    ``item.comprar``.

    The "Añadir" click is fired up to :data:`_MAX_ADD_ATTEMPTS` times; between
    attempts the product page is reloaded. With orderForm verification this
    rarely fires beyond the first attempt — it is kept as a safety net for the
    one-off case where the postal-code modal swallows the initial click.

    Raises:
        SessionExpiredError: navigation was redirected to the login page.
        ProductUnavailableError: the page rendered no product at all — a likely
            stale or discontinued URL. Surfaced as an end-of-run alert to fix
            on the data side, not a hard error.
        OutOfStockError: the product page renders but shows no add control.
        AddToCartFailed: a postal code was needed but not configured, an
            expected control was missing, or the cart line never reached the
            wanted quantity after every attempt.
    """
    logger.info("🛒 [ametller] %s ×%d", item.comida, item.comprar)
    goto_with_login_check(page, "ametller", item.buscador)
    human_delay(*_NAV_SETW)
    _handle_cart_restore_modal(page)
    _dismiss_overlays(page)

    name = _read_product_name(page)
    product_id = _read_product_id(page)
    has_add = page.locator(SELECTORS["add_button"]).count() > 0
    has_stepper = page.locator(SELECTORS["stepper_input"]).count() > 0
    if not name and not has_add and not has_stepper:
        # The /p URL served an empty shell — nothing to act on, stale URL.
        raise ProductUnavailableError(
            item,
            "product page rendered no title, add button or stepper — "
            "the buy URL is likely stale or the product discontinued",
        )
    if not name:
        raise AddToCartFailed(item, "product title not found — page layout unexpected")
    if not product_id:
        raise AddToCartFailed(
            item,
            "could not read VTEX productId from the page (missing "
            "<meta property='product:retailer_part_no'>)",
        )
    if not has_add:
        raise OutOfStockError(item)

    target = item.comprar
    qty = _cart_qty_by_id(page, product_id)
    if qty >= target:
        logger.info(
            "✅ [ametller] %s — already %d in cart (≥ %d wanted), leaving as is",
            item.comida, qty, target,
        )
        return

    for attempt in range(1, _MAX_ADD_ATTEMPTS + 1):
        delta = target - qty
        _dismiss_overlays(page)
        _set_stepper(page, delta, item)
        page.locator(SELECTORS["add_button"]).first.click()
        human_delay(*_NAV_SETW)
        _handle_zip_modal(page, item)
        # Poll the orderForm until `target` shows up (early-return on success).
        qty = _cart_qty_settled(page, product_id, target=target)
        if qty >= target:
            logger.info(
                "✅ [ametller] %s — %d in cart (attempt %d/%d)",
                item.comida, qty, attempt, _MAX_ADD_ATTEMPTS,
            )
            return

        if attempt < _MAX_ADD_ATTEMPTS:
            logger.warning(
                "⚠️ [ametller] %s — cart shows %d (expected %d) after "
                "attempt %d/%d, reloading to retry",
                item.comida, qty, target, attempt, _MAX_ADD_ATTEMPTS,
            )
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception:  # noqa: BLE001 — reload timing is best-effort
                pass
            human_delay(*_NAV_SETW)
            _handle_cart_restore_modal(page)
            _dismiss_overlays(page)
            # Refresh `qty` from the post-reload orderForm so the next delta is
            # accurate — if the previous attempt's add actually landed despite
            # the verification miss, this avoids a double-add.
            qty = _cart_qty_by_id(page, product_id)
            if qty >= target:
                logger.info(
                    "✅ [ametller] %s — %d in cart after reload",
                    item.comida, qty,
                )
                return

    raise AddToCartFailed(
        item,
        f"cart line shows {qty}, expected {target} after {_MAX_ADD_ATTEMPTS} attempts",
    )
