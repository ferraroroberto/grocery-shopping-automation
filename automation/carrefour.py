"""Carrefour add-to-cart handler (issue #150).

Carrefour's storefront (``www.carrefour.es/supermercado``) sits behind
Cloudflare: plain HTTP is blocked, but a real Chrome session works and the
page's own ``/cloud-api/*`` endpoints answer an in-page ``fetch`` that rides
the session cookies. The logged-in account carries the delivery address and
sale point, so no postcode has to be set per session.

Product page (``…/R-<product_id>/p``) add control, verified live 2026-09-28:

* **Not in cart** — a single "Añadir" button
  (``button.add-to-cart-button__full-button``).
* **In cart** — a stepper: a bin/minus button, a ``type=tel`` input holding
  the unit count, and a plus button.

Carrefour counts **units** (packs) — a variable-weight product such as
"pechuga 1 kg aprox" is still one unit per pack. The authoritative cart state
is the mini-cart's own JSON (``checkout-papi/v1/cart``), read after every
click, so a silently no-op click is caught and retried rather than trusted.

Session check: the header API's ``user.email`` is empty for an anonymous
session. Anonymous browsing still renders product pages (for a default Madrid
sale point), so a URL-based login check alone would not catch an expired
session.

All selectors and endpoints live in module constants so a site change is a
one-line fix.
"""

from __future__ import annotations

import json
import logging
import re
import time

from playwright.sync_api import Page

from automation.browser import SessionExpiredError, goto_with_login_check, human_delay
from automation.errors import AddToCartFailed, OutOfStockError, ProductUnavailableError
from automation.models import CartItem

logger = logging.getLogger(__name__)

STORE = "carrefour"
BASE_URL = "https://www.carrefour.es"
# Storefront home — a stable page for the run-level cart snapshot / clear.
HOME_URL = f"{BASE_URL}/supermercado"

# Session-scoped JSON endpoints the storefront itself calls (verified 2026-09-28).
_HEADER_API = f"{BASE_URL}/cloud-api/header/v1?cart_info=true"
_CART_API = f"{BASE_URL}/cloud-api/checkout-papi/v1/cart?reprice=true"
_LINE_API = f"{BASE_URL}/cloud-api/one-cart-api/v1/carts/current/items/{{sku}}?site=food"

SELECTORS = {
    "add_button": "button.add-to-cart-button__full-button",
    "increase": ".add-to-cart-button__more-unit-button",
    "stepper_input": ".add-to-cart-button input",
}

_FETCH_JS = """async ([url, method]) => {
  const r = await fetch(url, {method, credentials: 'include', headers: {'Accept': 'application/json'}});
  return {status: r.status, body: await r.text()};
}"""

# Page settle after a navigation, and the cart-poll schedule after a click.
_NAV_SETTLE = (2.5, 4.0)
_CART_POLL_COUNT = 6
_CART_POLL_INTERVAL_S = 1.0
_MAX_CLICK_ATTEMPTS = 3


def product_id_from_url(url: str) -> str:
    """Return the product id (``VC4AECOMM-081271``) from a Carrefour product URL.

    Product pages live at ``/supermercado/<slug>/R-<product_id>/p``. Returns an
    empty string when the URL is not a product page — e.g. the category page
    Carrefour redirects to when a product is not sold at the account's store.
    """
    match = re.search(r"/R-([A-Za-z0-9-]+)/p(?:[/?#]|$)", str(url or ""))
    return match.group(1) if match else ""


def cart_units(cart: dict) -> dict[str, dict]:
    """Map product id → ``{"sku": …, "units": …, "name": …}`` from the cart JSON.

    Reads ``food.items[*].products[*]`` of the ``checkout-papi`` cart. An empty
    cart (``items: []``) maps to ``{}``.
    """
    lines: dict[str, dict] = {}
    for group in (cart.get("food") or {}).get("items") or []:
        for product in group.get("products") or []:
            pid = str(product.get("product_id") or "")
            if not pid:
                continue
            entry = lines.setdefault(
                pid, {"sku": str(product.get("sku_id") or ""), "units": 0,
                      "name": str(product.get("name") or "")},
            )
            entry["units"] += int(product.get("units") or 0)
    return lines


def _fetch_json(page: Page, url: str, method: str = "GET") -> tuple[int, object]:
    """In-page ``fetch`` riding the session cookies → ``(status, parsed body)``."""
    res = page.evaluate(_FETCH_JS, [url, method])
    status = int(res.get("status") or 0)
    body = res.get("body") or ""
    try:
        return status, json.loads(body) if body.strip() else None
    except ValueError:
        return status, None


def _require_session(page: Page) -> None:
    """Raise :class:`SessionExpiredError` unless the account is logged in."""
    status, header = _fetch_json(page, _HEADER_API)
    user = (header or {}).get("user") if isinstance(header, dict) else None
    if status != 200 or not isinstance(user, dict) or not user.get("email"):
        logger.warning("⚠️ [carrefour] header API status %s — no logged-in user", status)
        raise SessionExpiredError(STORE)


def _read_cart(page: Page) -> dict[str, dict]:
    """The whole cart as :func:`cart_units` lines. Raises when it can't be read."""
    status, cart = _fetch_json(page, _CART_API)
    if status in (401, 403, 404):
        # Anonymous sessions get a 404 here — the account session has lapsed.
        raise SessionExpiredError(STORE)
    if status != 200 or not isinstance(cart, dict):
        raise RuntimeError(f"Carrefour cart API returned {status}")
    return cart_units(cart)


def _cart_qty(page: Page, product_id: str) -> int:
    return _read_cart(page).get(product_id, {}).get("units", 0)


def _cart_qty_settled(page: Page, product_id: str, *, target: int) -> int:
    """Poll the cart until the product reaches ``target`` (or polling runs out)."""
    qty = 0
    for _ in range(_CART_POLL_COUNT):
        qty = _cart_qty(page, product_id)
        if qty >= target:
            return qty
        time.sleep(_CART_POLL_INTERVAL_S)
    return qty


def _click_once(page: Page, selector: str) -> bool:
    """Click ``selector`` if it is visible; report whether a click happened."""
    loc = page.locator(selector).first
    if loc.count() == 0 or not loc.is_visible():
        return False
    loc.click()
    return True


def add_to_cart(page: Page, item: CartItem) -> None:
    """Add ``item`` to the Carrefour cart so its line totals ``item.comprar`` units.

    Idempotent: reads the current cart quantity from the cart API and only adds
    the missing units, one "Añadir"/"+" click at a time, each verified against
    the cart API. Never reduces a line that already holds more than wanted.

    Raises:
        SessionExpiredError: the saved profile is no longer logged in.
        ProductUnavailableError: the URL redirected away from a product page —
            the product is discontinued or not sold at the account's store.
        OutOfStockError: the product page renders but offers no add control.
        AddToCartFailed: the cart never reached the wanted quantity.
    """
    logger.info("🛒 [carrefour] %s ×%d", item.comida, item.comprar)
    wanted_id = product_id_from_url(item.buscador)
    goto_with_login_check(page, STORE, item.buscador)
    human_delay(*_NAV_SETTLE)
    _require_session(page)

    landed_id = product_id_from_url(page.url)
    if not landed_id or (wanted_id and landed_id != wanted_id):
        raise ProductUnavailableError(
            item,
            f"product URL redirected to {page.url} — the product is discontinued "
            "or not sold at this account's Carrefour store",
        )

    target = item.comprar
    qty = _cart_qty(page, landed_id)
    if qty >= target:
        logger.info(
            "✅ [carrefour] %s — already %d in cart (≥ %d wanted), leaving as is",
            item.comida, qty, target,
        )
        return

    attempts = 0
    while qty < target:
        if qty == 0:
            clicked = _click_once(page, SELECTORS["add_button"]) or _click_once(
                page, SELECTORS["increase"]
            )
            if not clicked:
                raise OutOfStockError(item)
        elif not _click_once(page, SELECTORS["increase"]):
            raise AddToCartFailed(item, "the + control is missing on the product page")
        human_delay(0.8, 1.6)
        new_qty = _cart_qty_settled(page, landed_id, target=qty + 1)
        if new_qty > qty:
            qty = new_qty
            attempts = 0
            continue
        attempts += 1
        logger.warning(
            "⚠️ [carrefour] %s — click did not register (cart still %d), retry %d/%d",
            item.comida, qty, attempts, _MAX_CLICK_ATTEMPTS,
        )
        if attempts >= _MAX_CLICK_ATTEMPTS:
            raise AddToCartFailed(
                item, f"cart line stuck at {qty}, expected {target} after "
                f"{_MAX_CLICK_ATTEMPTS} attempts",
            )
        page.reload(wait_until="domcontentloaded")
        human_delay(*_NAV_SETTLE)
        qty = _cart_qty(page, landed_id)

    logger.info("✅ [carrefour] %s — %d in cart", item.comida, qty)


def read_cart_total(page: Page) -> int:
    """Return the total units across the whole Carrefour food cart.

    Raises:
        SessionExpiredError: the saved profile is no longer logged in.
    """
    goto_with_login_check(page, STORE, HOME_URL)
    human_delay(*_NAV_SETTLE)
    _require_session(page)
    return sum(line["units"] for line in _read_cart(page).values())


def clear_cart(page: Page) -> int:
    """Empty the Carrefour food cart, returning the unit count removed.

    Enumerates every line via the cart API and deletes each through the same
    ``one-cart-api`` DELETE the product page's bin button issues. A no-op on an
    empty cart.

    Raises:
        SessionExpiredError: the saved profile is no longer logged in.
        AddToCartFailed: the cart still held units after deleting every line.
    """
    goto_with_login_check(page, STORE, HOME_URL)
    human_delay(*_NAV_SETTLE)
    _require_session(page)
    lines = _read_cart(page)
    if not lines:
        logger.info("🛒 [carrefour] cart already empty — nothing to clear")
        return 0

    removed = 0
    for pid, line in lines.items():
        status, _ = _fetch_json(page, _LINE_API.format(sku=line["sku"]), "DELETE")
        if status == 200:
            removed += line["units"]
        else:
            logger.warning("⚠️ [carrefour] DELETE %s for %s (%s)", status, pid, line["name"])
        human_delay(0.4, 0.9)

    remaining = sum(line["units"] for line in _read_cart(page).values())
    if remaining > 0:
        raise AddToCartFailed(
            CartItem(STORE, "(clear cart)", 0, ""),
            f"cart still holds {remaining} unit(s) after clearing",
        )
    logger.info("✅ [carrefour] cart cleared (%d unit(s) removed)", removed)
    return removed
