"""Ametller Origen add-to-cart handler.

Ametller Origen runs on **VTEX**. Its product page has a numeric stepper plus
an "Añadir" button: unlike Mercadona, you set the quantity *first* and then add
in one operation. The header badge counts **distinct products** (3 units of one
product still shows as "1"), so the only reliable quantity check is to open the
minicart drawer and read the product's line — which is exactly what this
handler does for verification.

Three VTEX modals can interrupt the flow; all are handled:

* **Cart-restore modal** ("Ya tienes una cesta en curso") — appears at session
  start. We always click "MANTENER CESTA ANTERIOR" to keep the existing cart.
  Clicking it reloads the page.
* **Delivery postal-code modal** — appears the first time something is added
  until a delivery option is saved into the profile. We fill the postal code
  from ``config.json`` (``automation.ametller_postal_code``), let the delivery
  options render, and click "GUARDAR OPCIÓN DE ENTREGA" (Standard delivery is
  pre-selected). Once saved it persists in the Chrome profile.
* The handler is **idempotent**: it reads the current minicart quantity first
  and only adds the missing units, so a re-run does not double-add. It never
  reduces a line that already has more than the shopping list asks for.

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
from automation.errors import AddToCartFailed, OutOfStockError  # noqa: E402
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
    # Delivery postal-code modal.
    "zip_input": "input#zipcode",
    "zip_save": ".vtex-modal__modal button:has-text('GUARDAR')",
    # Minicart drawer.
    "minicart_open": ".ametllerorigen-minicart-2-x-openIconContainer",
    "minicart_close": ".ametllerorigen-minicart-2-x-closeIconButton",
    "minicart_drawer": ".ametllerorigen-minicart-2-x-drawer",
    "minicart_badge": ".ametllerorigen-minicart-2-x-minicartQuantityBadge",
}

# VTEX hydrates slowly — these waits are deliberately generous.
_NAV_SETW = (3.0, 4.0)
_ACTION_SETW = (2.5, 3.5)

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


def _open_minicart(page: Page) -> None:
    """Open the minicart drawer (no-op if already open)."""
    drawer = page.locator(SELECTORS["minicart_drawer"])
    try:
        cls = drawer.first.get_attribute("class") if drawer.count() else ""
    except Exception:  # noqa: BLE001
        cls = ""
    if cls and "opened" in cls:
        return
    page.locator(SELECTORS["minicart_open"]).first.click()
    human_delay(*_ACTION_SETW)


def _close_minicart(page: Page) -> None:
    """Close the minicart drawer if it is open."""
    try:
        close = page.locator(SELECTORS["minicart_close"])
        if close.count() > 0 and close.first.is_visible():
            close.first.click()
            human_delay(1.5, 2.5)
    except Exception:  # noqa: BLE001
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

    Raises:
        SessionExpiredError: navigation was redirected to the login page.
        OutOfStockError: the product page shows no add control.
        AddToCartFailed: a postal code was needed but not configured, an
            expected control was missing, or the minicart line did not reach
            the wanted quantity.
    """
    logger.info("🛒 [ametller] %s ×%d", item.comida, item.comprar)
    goto_with_login_check(page, "ametller", item.buscador)
    human_delay(*_NAV_SETW)
    _handle_cart_restore_modal(page)

    name = _read_product_name(page)
    if not name:
        raise AddToCartFailed(item, "product title not found — page layout unexpected")
    if page.locator(SELECTORS["add_button"]).count() == 0:
        raise OutOfStockError(item)

    qty_before = _minicart_qty(page, name)
    target = item.comprar
    if qty_before >= target:
        logger.info(
            "✅ [ametller] %s — already %d in cart (≥ %d wanted), leaving as is",
            item.comida, qty_before, target,
        )
        return

    delta = target - qty_before
    _set_stepper(page, delta, item)
    page.locator(SELECTORS["add_button"]).first.click()
    human_delay(*_NAV_SETW)
    _handle_zip_modal(page, item)
    human_delay(*_ACTION_SETW)

    qty_now = _minicart_qty(page, name)
    if qty_now == qty_before:
        # The first click was likely consumed by the postal-code modal — retry.
        logger.warning("⚠️ [ametller] %s — add did not register, retrying", item.comida)
        _set_stepper(page, delta, item)
        page.locator(SELECTORS["add_button"]).first.click()
        human_delay(*_NAV_SETW)
        _handle_zip_modal(page, item)
        human_delay(*_ACTION_SETW)
        qty_now = _minicart_qty(page, name)

    if qty_now != target:
        raise AddToCartFailed(
            item,
            f"minicart line shows {qty_now}, expected {target} "
            f"(was {qty_before}, tried to add {delta})",
        )

    logger.info(
        "✅ [ametller] %s — %d in cart (was %d, added %d)",
        item.comida, target, qty_before, delta,
    )
