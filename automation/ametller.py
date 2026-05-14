"""Ametller Origen add-to-cart handler.

Ametller Origen runs on **VTEX**. Its product page has a numeric stepper plus
an "Añadir" button: unlike Mercadona, you set the quantity *first* and then add
in one operation. The header badge counts **distinct products** (3 units of one
product still shows as "1"), so the only reliable quantity check is to open the
minicart drawer and read the product's line — which is exactly what this
handler does for verification.

Two VTEX modals can interrupt the flow; both are handled:

* **Cart-restore modal** ("Ya tienes una cesta en curso") — appears at session
  start. We always click "MANTENER CESTA ANTERIOR" to keep the existing cart.
  Clicking it reloads the page.
* **Delivery postal-code modal** — appears the first time something is added
  until a delivery option is saved into the profile. We fill the postal code
  from ``config.json`` (``automation.ametller_postal_code``), let the delivery
  options render, and click "GUARDAR OPCIÓN DE ENTREGA" (Standard delivery is
  pre-selected). Once saved it persists in the Chrome profile.

The handler is **idempotent**: it reads the current minicart quantity first and
only adds the missing units, so a re-run does not double-add. It never reduces
a line that already has more than the shopping list asks for.

**Resilience (issue #8).** The handler hardens the add path several ways:

* It closes any stray minicart drawer / modal overlay before acting
  (:func:`_dismiss_overlays`) and verifies the drawer actually opened/closed —
  an overlay left open silently swallows the next "Añadir" click.
* It fires "Añadir" up to :data:`_MAX_ADD_ATTEMPTS` times, reloading the
  product page between attempts and re-reading the minicart — so a transient
  miss is retried and an add that *did* land despite a missing UI signal is
  still recognised (the delta is recomputed from the live cart each attempt,
  so a re-fire never double-adds).
* Two failure shapes are reported as :class:`ProductUnavailableError` — an
  end-of-run *alert* to fix on the data side, not a hard error — because no
  click path can recover them: a page that renders an **empty shell** (no
  title/button/stepper), and a page **frozen on the "AGREGADO" label** where
  the button, the stepper and a reload all no-op while the cart stays empty.
  Both mean a stale or discontinued buy URL in the inventory sheet.

All selectors live in :data:`SELECTORS`. Verified live on 2026-05-14.
"""

from __future__ import annotations

import logging
import re
import sys
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
# Selector map — verified live 2026-05-14 against www.ametllerorigen.com (VTEX).
# ─────────────────────────────────────────────────────────────────────────────
SELECTORS = {
    # "MANTENER CESTA ANTERIOR" link in the cart-restore modal.
    "cart_restore_keep": (
        "a.ametllerorigen-ametller-store-user-session-component-0-x-recoverCartButton"
    ),
    # Product title — matches the name shown on each minicart line.
    "product_name": "h1.vtex-store-components-3-x-productNameContainer",
    # Product-page quantity stepper (one per product page).
    "stepper_input": ".vtex-numeric-stepper-container .vtex-numeric-stepper__input",
    "stepper_plus": ".vtex-numeric-stepper-container .vtex-numeric-stepper__plus-button",
    "stepper_minus": ".vtex-numeric-stepper-container .vtex-numeric-stepper__minus-button",
    # Main "Añadir" button — scoped by its inner text node so the related-product
    # carousel buttons (.x-result-add-to-cart) and the minicart checkout button
    # are not matched.
    "add_button": "button:has(.ametllerorigen-add-to-cart-button-1-x-buttonText)",
    # The text node inside the add button — "AGREGAR" when addable, "AGREGADO"
    # when the page considers the item added (see _ADDED_BUTTON_TEXTS).
    "add_button_text": ".ametllerorigen-add-to-cart-button-1-x-buttonText",
    # Delivery postal-code modal.
    "zip_input": "input#zipcode",
    "zip_save": ".vtex-modal__modal button:has-text('GUARDAR')",
    # Minicart drawer.
    "minicart_open": ".ametllerorigen-minicart-2-x-openIconContainer",
    "minicart_close": ".ametllerorigen-minicart-2-x-closeIconButton",
    "minicart_drawer": ".ametllerorigen-minicart-2-x-drawer",
    "minicart_badge": ".ametllerorigen-minicart-2-x-minicartQuantityBadge",
    # Any VTEX modal overlay — left open it silently intercepts every click.
    "modal_overlay": ".vtex-modal__overlay",
}

# VTEX hydrates slowly — these waits are deliberately generous.
_NAV_SETW = (3.0, 4.0)
_ACTION_SETW = (2.5, 3.5)

# How many times to fire "Añadir" before giving up. Between attempts the
# product page is reloaded — the operator's manual workaround: a stuck overlay
# or open drawer (which silently swallows the click) is cleared by a fresh
# load, and the reloaded page often shows the item actually did land.
_MAX_ADD_ATTEMPTS = 3

# Add-button label (normalised) when the page considers the item already added.
# Issue #8: some discontinued SKUs render a product page frozen on this label —
# the button, the stepper and a reload all no-op and the item never reaches the
# cart. That stuck state is reported as ProductUnavailableError (an alert), not
# AddToCartFailed, because no click path can recover it; the buy URL is stale.
_ADDED_BUTTON_TEXTS = {"agregado", "añadido", "anadido", "afegit"}

# JS that pairs each minicart line's product name with its quantity-input value.
_MINICART_LINES_JS = """() => {
  const names = [...document.querySelectorAll('a.ametllerorigen-product-list-0-x-productName')];
  return names.map(a => {
    let el = a;
    for (let i = 0; i < 14 && el; i++) {
      el = el.parentElement;
      if (el && el.querySelector('.ametllerorigen-ametller-components-1-x-quantityInputContainer input')) break;
    }
    const qi = el ? el.querySelector('.ametllerorigen-ametller-components-1-x-quantityInputContainer input') : null;
    return { name: (a.innerText || '').trim(), qty: qi ? parseInt(qi.value || '0', 10) : null };
  });
}"""


def _int(text: object) -> int:
    """First integer in `text`, or 0."""
    match = re.search(r"\d+", str(text or ""))
    return int(match.group()) if match else 0


def _norm(name: str) -> str:
    """Normalise a product name for matching (lowercase, collapsed whitespace)."""
    return re.sub(r"\s+", " ", (name or "").strip().lower())


def _postal_code() -> str:
    """Delivery postal code from config.json (``automation.ametller_postal_code``)."""
    return str(CONFIG.get("automation", {}).get("ametller_postal_code", "")).strip()


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


def _drawer_open(page: Page) -> bool:
    """True when the minicart drawer is currently open."""
    drawer = page.locator(SELECTORS["minicart_drawer"])
    try:
        cls = drawer.first.get_attribute("class") if drawer.count() else ""
    except Exception:  # noqa: BLE001
        cls = ""
    return bool(cls) and "opened" in cls


def _open_minicart(page: Page) -> None:
    """Open the minicart drawer (no-op if already open), verifying it opened."""
    for _ in range(3):
        if _drawer_open(page):
            return
        try:
            page.locator(SELECTORS["minicart_open"]).first.click()
        except Exception:  # noqa: BLE001
            pass
        human_delay(*_ACTION_SETW)
    logger.warning("⚠️ [ametller] minicart drawer would not open")


def _close_minicart(page: Page) -> None:
    """Close the minicart drawer if open, verifying it actually closed.

    A drawer left open silently intercepts every later click — issue #8's root
    cause. So this does not just fire-and-forget: it confirms the drawer is
    closed and escalates to the Escape key if the close button does not take.
    """
    for _ in range(3):
        if not _drawer_open(page):
            return
        try:
            close = page.locator(SELECTORS["minicart_close"])
            if close.count() > 0 and close.first.is_visible():
                close.first.click()
            else:
                page.keyboard.press("Escape")
        except Exception:  # noqa: BLE001
            try:
                page.keyboard.press("Escape")
            except Exception:  # noqa: BLE001
                pass
        human_delay(1.5, 2.5)
    logger.warning("⚠️ [ametller] minicart drawer would not close")


def _dismiss_overlays(page: Page) -> None:
    """Clear a stray-open drawer / modal overlay before driving the page.

    Defensive cleanup: a minicart drawer or VTEX modal overlay left over from a
    previous item sits on top of the page and swallows clicks meant for
    "Añadir". Closing it up front is what stops one stuck item from poisoning
    every item after it. Best-effort and a safe no-op when nothing is open. The
    delivery postal-code modal is deliberately left alone — it must be *filled*,
    not dismissed.
    """
    _close_minicart(page)
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


def _minicart_qty(page: Page, product_name: str) -> int:
    """Open the minicart, return how many units of `product_name` it holds, close it."""
    _open_minicart(page)
    try:
        lines = page.evaluate(_MINICART_LINES_JS)
    except Exception:  # noqa: BLE001
        lines = []
    _close_minicart(page)

    target = _norm(product_name)
    for line in lines:
        if _norm(line.get("name", "")) == target:
            return int(line.get("qty") or 0)
    return 0


def _read_product_name(page: Page) -> str:
    """Return the product title shown on the page (empty string if missing)."""
    loc = page.locator(SELECTORS["product_name"])
    if loc.count() == 0:
        return ""
    return loc.first.inner_text().strip()


def _add_button_label(page: Page) -> str:
    """Return the add-button's text (e.g. ``"AGREGAR"`` / ``"AGREGADO"``)."""
    loc = page.locator(SELECTORS["add_button_text"])
    if loc.count() == 0:
        return ""
    try:
        return loc.first.inner_text().strip()
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


def add_to_cart(page: Page, item: CartItem) -> None:
    """Add `item` to the Ametller Origen cart so its line totals `item.comprar`.

    Idempotent: reads the current minicart quantity and only adds the missing
    units. Never reduces a line that already holds more than ``item.comprar``.

    The "Añadir" click is fired up to :data:`_MAX_ADD_ATTEMPTS` times; between
    attempts the product page is reloaded. A reload clears any stuck overlay or
    open drawer that was silently swallowing the click (issue #8's root cause),
    and re-reading the minicart afterwards both recovers items that *did* land
    despite a missing UI signal and keeps the add idempotent (the delta is
    recomputed from the live cart each attempt, so a re-fire never double-adds).

    Raises:
        SessionExpiredError: navigation was redirected to the login page.
        ProductUnavailableError: the page rendered no product at all, or it
            rendered but is frozen on an "added" label while the cart stays
            empty — both are stale/discontinued URLs. Surfaced as an end-of-run
            alert to fix on the data side, not as a hard error.
        OutOfStockError: the product page renders but shows no add control.
        AddToCartFailed: a postal code was needed but not configured, an
            expected control was missing, or the minicart line never reached
            the wanted quantity after every attempt.
    """
    logger.info("🛒 [ametller] %s ×%d", item.comida, item.comprar)
    goto_with_login_check(page, "ametller", item.buscador)
    human_delay(*_NAV_SETW)
    _handle_cart_restore_modal(page)
    # Clear anything a previous item left on top of the page before we look at it.
    _dismiss_overlays(page)

    name = _read_product_name(page)
    has_add = page.locator(SELECTORS["add_button"]).count() > 0
    has_stepper = page.locator(SELECTORS["stepper_input"]).count() > 0
    if not name and not has_add and not has_stepper:
        # Mode A: the /p URL served an empty shell — nothing to act on.
        raise ProductUnavailableError(
            item,
            "product page rendered no title, add button or stepper — "
            "the buy URL is likely stale or the product discontinued",
        )
    if not name:
        raise AddToCartFailed(item, "product title not found — page layout unexpected")
    if not has_add:
        raise OutOfStockError(item)

    target = item.comprar
    qty = _minicart_qty(page, name)
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
        human_delay(*_ACTION_SETW)

        qty = _minicart_qty(page, name)
        if qty >= target:
            logger.info(
                "✅ [ametller] %s — %d in cart (attempt %d/%d)",
                item.comida, qty, attempt, _MAX_ADD_ATTEMPTS,
            )
            return

        if attempt < _MAX_ADD_ATTEMPTS:
            # The add did not register. Reload the product page: this clears a
            # stuck overlay/drawer and the reloaded minicart often shows the
            # item actually landed (the operator's manual workaround).
            logger.warning(
                "⚠️ [ametller] %s — add did not register (attempt %d/%d), "
                "reloading to clear state and recheck",
                item.comida, attempt, _MAX_ADD_ATTEMPTS,
            )
            try:
                page.reload(wait_until="domcontentloaded")
            except Exception:  # noqa: BLE001 — reload timing is best-effort
                pass
            human_delay(*_NAV_SETW)
            _handle_cart_restore_modal(page)
            _dismiss_overlays(page)
            qty = _minicart_qty(page, name)
            if qty >= target:
                logger.info(
                    "✅ [ametller] %s — %d in cart after reload (add had landed)",
                    item.comida, qty,
                )
                return

    # Every attempt (click, reload, recheck) failed. If the add button is frozen
    # on its "added" label while the cart stays empty, this is the issue-#8 stuck
    # state — a discontinued SKU no click path can recover. Report it as an
    # alert (ProductUnavailableError), not a hard error, so a stale URL on the
    # data side never fails the run; the operator just refreshes the buy URL.
    label = _add_button_label(page)
    if _norm(label) in _ADDED_BUTTON_TEXTS:
        raise ProductUnavailableError(
            item,
            f"product page is frozen on '{label}' but the item never reaches "
            f"the cart after {_MAX_ADD_ATTEMPTS} attempts (click, stepper and "
            f"reload all no-op) — the product is likely discontinued; "
            f"refresh the buy URL",
        )
    raise AddToCartFailed(
        item,
        f"minicart line shows {qty}, expected {target} after "
        f"{_MAX_ADD_ATTEMPTS} attempts (overlay/drawer may be stuck)",
    )
