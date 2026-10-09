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
session. The header API is the *only* login signal: the cart API answers a
logged-in account that has no cart yet (e.g. right after an order) with
``404 {"type": "no_cart"}`` — the storefront's own page load gets the same 404
(verified live 2026-10-06, issue #217) — so that 404 reads as an empty cart,
never as a lapsed session. Every other non-OK answer raises the
:class:`~automation.browser.StoreAccessError` subclass that names its cause.

Cut picker (issue #176, verified live 2026-09-28): fresh-fish pages such as
"Dorada de ración" show "Selecciona el tipo de corte" — one
``li.cut-selector__item`` per cut, titled with its label (Entero · Entero
limpio · Rodajas · …), the chosen one carrying ``selected``; the page opens on
"Entero". Picking a cut changes neither the URL nor the product id and fires
no request: the "Añadir" POST carries it (``…/items/<sku>?…&cut_id=Entero+limpio``)
and the cart line returns it as ``cut_type``. The cut to pick comes from
``config/product_options.json`` (:mod:`automation.product_options`); it is
selected before the first "Añadir" and verified on the cart line after every
click.

All selectors and endpoints live in module constants so a site change is a
one-line fix.
"""

from __future__ import annotations

import json
import logging
import re
import time
from typing import Optional

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation import product_options
from automation.browser import (
    BotChallengeError,
    NotLoggedInError,
    StoreAccessError,
    StoreApiError,
    goto_with_login_check,
    human_delay,
)
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
# Error ``type`` of the cart API's 404 for a logged-in account with no cart yet.
_NO_CART = "no_cart"
# Statuses a Cloudflare/bot interstitial answers with in place of the API's JSON,
# and text that marks such an interstitial page.
_CHALLENGE_STATUSES = (403, 429, 503)
_CHALLENGE_MARKERS = ("just a moment", "cf-chl", "challenge-platform", "cf-mitigated")

SELECTORS = {
    "add_button": "button.add-to-cart-button__full-button",
    "increase": ".add-to-cart-button__more-unit-button",
    "stepper_input": ".add-to-cart-button input",
    # Cut picker on fresh-fish pages: one item per cut, titled with its label.
    "cut_item": ".cut-selector__item",
}
# Class the cut picker puts on the chosen cut.
_CUT_SELECTED_CLASS = "selected"
# How long to wait for the cut picker to render before calling it absent.
_CUT_PICKER_TIMEOUT_MS = 10000

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
    """Map product id → ``{"sku", "units", "name", "cuts"}`` from the cart JSON.

    Reads ``food.items[*].products[*]`` of the ``checkout-papi`` cart. ``cuts``
    lists the distinct ``cut_type`` values of the product's lines (``[]`` for a
    product sold without a cut). An empty cart (``items: []``) maps to ``{}``.
    """
    lines: dict[str, dict] = {}
    for group in (cart.get("food") or {}).get("items") or []:
        for product in group.get("products") or []:
            pid = str(product.get("product_id") or "")
            if not pid:
                continue
            entry = lines.setdefault(
                pid, {"sku": str(product.get("sku_id") or ""), "units": 0,
                      "name": str(product.get("name") or ""), "cuts": []},
            )
            entry["units"] += int(product.get("units") or 0)
            cut = str(product.get("cut_type") or "").strip()
            if cut and cut not in entry["cuts"]:
                entry["cuts"].append(cut)
    return lines


def wrong_cuts(line: Optional[dict], cut: str) -> list[str]:
    """The cuts a cart line holds other than ``cut`` (``[]`` when it is right).

    A line with units but no recorded cut counts as wrong — the add was not
    made with ``cut``. An absent or empty line holds nothing, so nothing is
    wrong with it.
    """
    if not line or not line.get("units"):
        return []
    cuts = line.get("cuts") or []
    if not cuts:
        return ["(no cut)"]
    return [c for c in cuts if c != cut]


def _fetch_json(page: Page, url: str, method: str = "GET") -> tuple[int, object]:
    """In-page ``fetch`` riding the session cookies → ``(status, body)``.

    ``body`` is the parsed JSON, the raw text when the answer is not JSON (a
    challenge page, say), or ``None`` when it is empty.
    """
    res = page.evaluate(_FETCH_JS, [url, method])
    status = int(res.get("status") or 0)
    body = res.get("body") or ""
    try:
        return status, json.loads(body) if body.strip() else None
    except ValueError:
        return status, body


def _api_error(api: str, status: int, body: object) -> StoreAccessError:
    """The distinct, logged error for an API answer the handler can't use."""
    if isinstance(body, str) and (
        status in _CHALLENGE_STATUSES or any(m in body.lower() for m in _CHALLENGE_MARKERS)
    ):
        err: StoreAccessError = BotChallengeError(STORE, f"{api} returned {status} with an HTML page")
    elif status == 401:
        err = NotLoggedInError(STORE, f"{api} returned 401")
    else:
        kind = f", type '{str(body.get('type'))[:40]}'" if isinstance(body, dict) and body.get("type") else ""
        shape = " (not JSON)" if isinstance(body, str) else ""
        err = StoreApiError(STORE, f"{api} returned an unexpected {status}{kind}{shape}")
    logger.warning("⚠️ [carrefour] %s", err)
    return err


def _require_session(page: Page) -> None:
    """Return when the header API reports a logged-in account; else raise.

    Raises:
        NotLoggedInError: the header answers with no account (empty ``user.email``)
            or a 401.
        BotChallengeError: a Cloudflare/bot challenge came back instead of JSON.
        StoreApiError: any other status, or a 200 without a ``user`` block.
    """
    status, header = _fetch_json(page, _HEADER_API)
    if status != 200 or not isinstance(header, dict):
        raise _api_error("header API", status, header)
    if not isinstance(header.get("user"), dict):
        raise _api_error("header API", status, {"type": "no user block"})
    if not header["user"].get("email"):
        logger.warning("⚠️ [carrefour] header API 200 with an empty user.email — not logged in")
        raise NotLoggedInError(STORE, "header API returned 200 with an empty user.email")


def _read_cart(page: Page) -> dict[str, dict]:
    """The whole cart as :func:`cart_units` lines (``{}`` when there is no cart).

    Call :func:`_require_session` first: the cart API's ``no_cart`` 404 does not
    tell a logged-in account with no cart from an anonymous one.

    Raises:
        StoreAccessError: the matching subclass from :func:`_api_error`.
    """
    status, cart = _fetch_json(page, _CART_API)
    if status == 200 and isinstance(cart, dict):
        return cart_units(cart)
    if status == 404 and isinstance(cart, dict) and cart.get("type") == _NO_CART:
        logger.info("🛒 [carrefour] cart API 404 no_cart — the account has no cart yet, reading it as empty")
        return {}
    raise _api_error("cart API", status, cart)


def check_login(page: Page) -> None:
    """Read-only login check (issue #217): load the home page, ask the header API.

    Touches no cart. Returns when logged in.

    Raises:
        StoreAccessError: the matching subclass — see :func:`_require_session`.
    """
    goto_with_login_check(page, STORE, HOME_URL)
    human_delay(*_NAV_SETTLE)
    _require_session(page)


def _cart_line(page: Page, product_id: str) -> dict:
    """The product's :func:`cart_units` line, or ``{}`` when it is not in the cart."""
    return _read_cart(page).get(product_id, {})


def _cart_line_settled(page: Page, product_id: str, *, target: int) -> dict:
    """Poll the cart until the product reaches ``target`` units (or polling runs out)."""
    line: dict = {}
    for _ in range(_CART_POLL_COUNT):
        line = _cart_line(page, product_id)
        if line.get("units", 0) >= target:
            return line
        time.sleep(_CART_POLL_INTERVAL_S)
    return line


def _offered_cuts(page: Page) -> list[str]:
    """The cut labels the product page offers, waiting for the picker to render."""
    items = page.locator(SELECTORS["cut_item"])
    try:
        items.first.wait_for(state="visible", timeout=_CUT_PICKER_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        return []
    return [str(t or "").strip() for t in items.evaluate_all("els => els.map(e => e.title)")]


def _select_cut(page: Page, item: CartItem, cut: str) -> None:
    """Pick ``cut`` in the product page's cut picker, or raise before any add.

    Raises:
        AddToCartFailed: the page offers no such cut, or it did not stay
            selected — the item is never added in the page's default cut.
    """
    offered = _offered_cuts(page)
    if cut not in offered:
        raise AddToCartFailed(
            item, f"cut '{cut}' is not offered on the product page "
            f"(offers: {', '.join(offered) or 'no cut picker'}) — not added",
        )
    option = page.locator(f"{SELECTORS['cut_item']}[title={json.dumps(cut)}]").first
    option.click()
    human_delay(0.5, 1.0)
    if _CUT_SELECTED_CLASS not in (option.get_attribute("class") or "").split():
        raise AddToCartFailed(item, f"cut '{cut}' did not stay selected on the product page — not added")
    logger.info("✂️ [carrefour] %s — cut '%s' selected", item.comida, cut)


def _remove_line(page: Page, line: dict) -> int:
    """Delete a cart line the way the page's bin button does → HTTP status."""
    status, _ = _fetch_json(page, _LINE_API.format(sku=line["sku"]), "DELETE")
    return status


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
        StoreAccessError: the saved profile is not logged in, or the store
            answered with a challenge or an unexpected response.
        ProductUnavailableError: the URL redirected away from a product page —
            the product is discontinued or not sold at the account's store.
        OutOfStockError: the product page renders but offers no add control.
        AddToCartFailed: the cart never reached the wanted quantity; or, for a
            product with a preferred cut (:mod:`automation.product_options`),
            the cut could not be selected, the cart already holds the product
            in another cut, or the add came back in another cut.
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

    cut = product_options.preferred_cut(STORE, landed_id)
    target = item.comprar
    line = _cart_line(page, landed_id)
    qty = line.get("units", 0)
    start_qty = qty
    if cut:
        held = wrong_cuts(line, cut)
        if held:
            # Never top up a line in another cut — the order would mix cuts.
            raise AddToCartFailed(
                item, f"the cart already holds {qty} as {', '.join(held)}, not cut "
                f"'{cut}' — fix that line in the Carrefour cart by hand",
            )
    elif page.locator(SELECTORS["cut_item"]).count():
        logger.warning(
            "⚠️ [carrefour] %s — the page offers cuts but config/product_options.json "
            "names none for product %s; adding the page's default cut",
            item.comida, landed_id,
        )
    if qty >= target:
        logger.info(
            "✅ [carrefour] %s — already %d in cart (≥ %d wanted), leaving as is",
            item.comida, qty, target,
        )
        return

    attempts = 0
    while qty < target:
        if qty == 0:
            if cut:
                _select_cut(page, item, cut)
            clicked = _click_once(page, SELECTORS["add_button"]) or _click_once(
                page, SELECTORS["increase"]
            )
            if not clicked:
                raise OutOfStockError(item)
        elif not _click_once(page, SELECTORS["increase"]):
            raise AddToCartFailed(item, "the + control is missing on the product page")
        human_delay(0.8, 1.6)
        line = _cart_line_settled(page, landed_id, target=qty + 1)
        new_qty = line.get("units", 0)
        held = wrong_cuts(line, cut) if cut else []
        if held:
            # A line this call created from nothing is entirely ours to undo.
            removed = start_qty == 0 and _remove_line(page, line) == 200
            raise AddToCartFailed(
                item, f"the cart line came back as {', '.join(held)}, not cut '{cut}'"
                + (" — line removed" if removed else " — fix it in the Carrefour cart by hand"),
            )
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
        qty = _cart_line(page, landed_id).get("units", 0)

    logger.info(
        "✅ [carrefour] %s — %d in cart%s", item.comida, qty, f" (cut '{cut}')" if cut else "",
    )


def read_cart_total(page: Page) -> int:
    """Return the total units across the whole Carrefour food cart.

    Raises:
        StoreAccessError: the saved profile is not logged in, or the store
            answered with a challenge or an unexpected response.
    """
    goto_with_login_check(page, STORE, HOME_URL)
    human_delay(*_NAV_SETTLE)
    _require_session(page)
    return sum(line["units"] for line in _read_cart(page).values())


def cart_key(url: str) -> str:
    """The key :func:`read_cart_lines` files ``url``'s product under."""
    return product_id_from_url(url)


def read_cart_lines(page: Page) -> dict[str, float]:
    """The whole food cart as product id → units, for the run's final check (#247).

    Read-only: one page load and the cart API GET.

    Raises:
        StoreAccessError: the saved profile is not logged in, or the store
            answered with a challenge or an unexpected response.
    """
    goto_with_login_check(page, STORE, HOME_URL)
    human_delay(*_NAV_SETTLE)
    _require_session(page)
    return {pid: line["units"] for pid, line in _read_cart(page).items()}


def clear_cart(page: Page) -> int:
    """Empty the Carrefour food cart, returning the unit count removed.

    Enumerates every line via the cart API and deletes each through the same
    ``one-cart-api`` DELETE the product page's bin button issues. A no-op on an
    empty cart.

    Raises:
        StoreAccessError: the saved profile is not logged in, or the store
            answered with a challenge or an unexpected response.
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
        status = _remove_line(page, line)
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
