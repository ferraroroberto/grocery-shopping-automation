"""Ametller Origen add-to-cart handler.

Ametller Origen runs on **Salesforce Commerce Cloud** (the Chakra-UI
"Composable Storefront" / PWA Kit). It migrated off VTEX in May 2026 — see
issue #12. Its product page has a numeric quantity stepper plus an "AÑADIR"
button: you set the quantity *first* and then add in one operation.

The legacy ``/p`` product URLs stored in the inventory sheet still work — the
new site **301-redirects** each to an ID-based URL (``/{productId}.html``). The
handler navigates to the stored URL and reads the numeric ``productId`` back
from the redirected URL.

The handler is **idempotent**: it reads the current cart quantity first and
only adds the missing units, so a re-run does not double-add. It never reduces
a line that already has more than the shopping list asks for.

**Verification (issue #12).** The cart is read via Salesforce Commerce Cloud's
**SCAPI Shopper Baskets** API — the authoritative source the storefront itself
uses. The minicart/drawer DOM is not consulted. The basket endpoint returns
every line with its true quantity for the live session, so both the idempotency
check ("already have N") and the post-add verification ("add landed") are
accurate.

The SCAPI call rides the SLAS shopper token the storefront stashes in
``localStorage`` (``access_token_ametller`` / ``customer_id_ametller``). The
same store also tells us whether the session is still a *registered* shopper —
if it has dropped to a guest the saved Chrome profile login has expired and a
:class:`~automation.browser.SessionExpiredError` is raised.

Lines are matched by the numeric **productId** — read from the redirected
product URL and compared against ``productItems[].productId`` in the basket
response. After firing "AÑADIR" we poll the basket a few times with a short
delay, returning as soon as the target quantity is observed, so a brief
propagation lag is absorbed and a retry only fires when the add genuinely did
not land.

A product page that renders an empty shell (no title and no productId) is
reported as :class:`ProductUnavailableError` — an end-of-run *alert* to fix on
the data side, not a hard error.

All selectors live in :data:`SELECTORS`. The site is built with Chakra UI:
selectors stick to stable Chakra component classes, ARIA labels and visible
button text — never the Emotion ``css-*`` hashes, which change on every deploy.
Verified live on 2026-05-20.
"""

from __future__ import annotations

import logging
import re
import time

from playwright.sync_api import Page

from automation.browser import (
    SessionExpiredError,
    goto_with_login_check,
    human_delay,
)
from automation.errors import (
    AddToCartFailed,
    OutOfStockError,
    ProductUnavailableError,
)
from automation.models import CartItem

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Selector map — verified live 2026-05-20 against www.ametllerorigen.com (SFCC).
# Chakra component classes and ARIA labels only; no Emotion css-* hashes.
# ─────────────────────────────────────────────────────────────────────────────
SELECTORS = {
    # Product title — the page <h1>.
    "product_name": "h1.chakra-heading",
    # Product-page quantity stepper (exactly one per product page).
    "stepper_input": "input.chakra-numberinput__field",
    "stepper_plus": "button[aria-label='quantity-selector.add']",
    "stepper_minus": "button[aria-label='quantity-selector.remove']",
}

# The main "AÑADIR" button is the first one in DOM order — the product-detail
# section renders before the "También te puede interesar" recommendation
# carousel, whose cards each carry their own "AÑADIR" button.
_ADD_BUTTON_NAME = "AÑADIR"

# SFCC hydrates slowly — these waits are deliberately generous.
_NAV_SETW = (3.0, 4.0)

# How many times to fire "AÑADIR" before giving up. With basket-API
# verification this almost never fires beyond 1; the retry is kept as a safety
# net for a genuinely transient miss. Each retry reloads the product page.
_MAX_ADD_ATTEMPTS = 2

# Salesforce Commerce Cloud SCAPI — Shopper Baskets via the storefront's own
# /mobify proxy. The org id and siteId are fixed for the Ametller storefront.
_SCAPI_BASE = "https://www.ametllerorigen.com/mobify/proxy/api"
_SCAPI_ORG = "f_ecom_blzv_prd"
_SCAPI_SITE = "ametller"

# Storefront home — a stable page that hydrates the SLAS shopper session into
# localStorage, used to read auth before a run-level cart snapshot / clear.
HOME_URL = "https://www.ametllerorigen.com/"

# After AÑADIR, SCAPI needs a moment to refresh the basket. Poll up to this
# many times with this gap, returning as soon as the target qty appears.
_CART_POLL_COUNT = 6
_CART_POLL_INTERVAL_S = 1.0


def _int(text: object) -> int:
    """First integer in `text`, or 0."""
    match = re.search(r"\d+", str(text or ""))
    return int(match.group()) if match else 0


def _product_id_from_url(url: str) -> str:
    """Return the numeric productId from a redirected product URL.

    The new site serves product pages at ``…/{slug}/{productId}.html``. Returns
    an empty string when the URL has no such id (a stale/unredirected link).
    """
    match = re.search(r"/(\d+)\.html", str(url or ""))
    return match.group(1) if match else ""


# ─────────────────────────────────────────────────────────────────────────────
# Session auth + cart-state reading via the SCAPI Shopper Baskets API.
# ─────────────────────────────────────────────────────────────────────────────


def _read_auth(page: Page) -> dict:
    """Read the SLAS shopper token + customer id the storefront stores locally.

    Returns ``{"token", "customer_id", "customer_type"}``. ``customer_type`` is
    ``"registered"`` for a logged-in shopper; anything else means the saved
    profile session has lapsed to a guest.
    """
    try:
        store = page.evaluate(
            "() => ({"
            " token: localStorage.getItem('access_token_ametller') || '',"
            " customer_id: localStorage.getItem('customer_id_ametller') || '',"
            " customer_type: localStorage.getItem('customer_type_ametller') || ''"
            "})"
        )
    except Exception:  # noqa: BLE001 — a failed read is treated as no session
        return {"token": "", "customer_id": "", "customer_type": ""}
    return {
        "token": str(store.get("token") or ""),
        "customer_id": str(store.get("customer_id") or ""),
        "customer_type": str(store.get("customer_type") or ""),
    }


def _basket_items(page: Page, auth: dict) -> list[dict]:
    """Fetch every basket line via SCAPI ``shopper-customers/.../baskets``.

    Uses ``page.request`` with the SLAS bearer token so the call rides the live
    session. Returns a list of ``{"basketId", "itemId", "productId", "qty"}``
    dicts, empty on any network/JSON error (a transient miss should not crash
    the run; the caller's quantity check then finds 0 and triggers the add).

    ``basketId`` / ``itemId`` are the handles the Shopper Baskets *delete*
    endpoint needs to remove a line (clean mode, issue #19).
    """
    token = auth.get("token") or ""
    customer_id = auth.get("customer_id") or ""
    if not token or not customer_id:
        return []
    url = (
        f"{_SCAPI_BASE}/customer/shopper-customers/v1/organizations/{_SCAPI_ORG}"
        f"/customers/{customer_id}/baskets?siteId={_SCAPI_SITE}"
    )
    try:
        resp = page.request.get(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=15000
        )
        if not resp.ok:
            logger.debug("basket GET %d — treating cart as empty", resp.status)
            return []
        data = resp.json()
    except Exception as err:  # noqa: BLE001 — best-effort, fall back to empty
        logger.debug("basket fetch raised %s — treating cart as empty", err)
        return []
    out: list[dict] = []
    for basket in data.get("baskets", []) or []:
        basket_id = str(basket.get("basketId") or "")
        for it in basket.get("productItems", []) or []:
            out.append({
                "basketId": basket_id,
                "itemId": str(it.get("itemId") or ""),
                "productId": str(it.get("productId") or ""),
                "qty": _int(it.get("quantity")),
            })
    return out


def _basket_lines(page: Page, auth: dict) -> list[dict]:
    """Return every basket line projected to ``{"productId", "qty"}``.

    Thin projection over :func:`_basket_items` for the quantity checks that do
    not care about the basket/line handles.
    """
    return [
        {"productId": it["productId"], "qty": it["qty"]}
        for it in _basket_items(page, auth)
    ]


def _cart_qty_by_id(page: Page, auth: dict, product_id: str) -> int:
    """Return the total cart units for `product_id` (0 when absent).

    SFCC can split a product across multiple basket lines, so all matches are
    summed. `productId` is the canonical match — it is language-independent.
    """
    if not product_id:
        return 0
    return sum(
        line["qty"]
        for line in _basket_lines(page, auth)
        if line["productId"] == str(product_id)
    )


def _cart_qty_settled(
    page: Page, auth: dict, product_id: str, *, target: int
) -> int:
    """Poll the basket until `target` is reached, or up to the poll budget.

    SCAPI's basket refresh after an add is fast but not instant; a single read
    can briefly return the pre-add quantity. Polling returns as soon as
    ``qty >= target``, so a successful add is recognised quickly and a genuine
    miss still gets the full wait before we conclude the add did not land.
    """
    qty = _cart_qty_by_id(page, auth, product_id)
    if qty >= target:
        return qty
    for _ in range(_CART_POLL_COUNT):
        time.sleep(_CART_POLL_INTERVAL_S)
        qty = _cart_qty_by_id(page, auth, product_id)
        if qty >= target:
            return qty
    return qty


# ─────────────────────────────────────────────────────────────────────────────
# Product-page helpers.
# ─────────────────────────────────────────────────────────────────────────────


def _read_product_name(page: Page) -> str:
    """Return the product title shown on the page (empty string if missing)."""
    loc = page.locator(SELECTORS["product_name"])
    if loc.count() == 0:
        return ""
    try:
        return loc.first.inner_text().strip()
    except Exception:  # noqa: BLE001
        return ""


def _set_stepper(page: Page, value: int, item: CartItem) -> None:
    """Set the product-page quantity stepper to `value` (>= 1)."""
    inp = page.locator(SELECTORS["stepper_input"]).first
    inp.scroll_into_view_if_needed()
    try:
        inp.fill(str(value))
        inp.press("Tab")
        human_delay(0.6, 1.0)
    except Exception:  # noqa: BLE001 — fall through to the +/- buttons
        pass

    current = _int(inp.input_value())
    guard = 0
    while current < value:
        page.locator(SELECTORS["stepper_plus"]).first.click()
        human_delay(0.3, 0.6)
        current = _int(inp.input_value())
        guard += 1
        if guard > value + 10:
            raise AddToCartFailed(item, f"could not set quantity stepper to {value}")
    while current > value:
        page.locator(SELECTORS["stepper_minus"]).first.click()
        human_delay(0.3, 0.6)
        current = _int(inp.input_value())
        guard += 1
        if guard > value + 20:
            raise AddToCartFailed(item, f"could not set quantity stepper to {value}")


# ─────────────────────────────────────────────────────────────────────────────
# The handler.
# ─────────────────────────────────────────────────────────────────────────────


def add_to_cart(page: Page, item: CartItem) -> None:
    """Add `item` to the Ametller Origen cart so its line totals `item.comprar`.

    Idempotent: reads the current cart quantity via the SCAPI basket API and
    only adds the missing units. Never reduces a line that already holds more
    than ``item.comprar``.

    The "AÑADIR" click is fired up to :data:`_MAX_ADD_ATTEMPTS` times; between
    attempts the product page is reloaded.

    Raises:
        SessionExpiredError: the saved profile session has lapsed to a guest.
        ProductUnavailableError: the page rendered no product at all — a likely
            stale or discontinued URL. Surfaced as an end-of-run alert.
        OutOfStockError: the product page renders but shows no add control.
        AddToCartFailed: an expected control was missing, or the cart line never
            reached the wanted quantity after every attempt.
    """
    logger.info("🛒 [ametller] %s ×%d", item.comida, item.comprar)
    goto_with_login_check(page, "ametller", item.buscador)
    human_delay(*_NAV_SETW)

    auth = _read_auth(page)
    if auth["customer_type"] != "registered":
        # The new site dropped us to a guest — the profile login has expired.
        raise SessionExpiredError("ametller")

    product_id = _product_id_from_url(page.url)
    name = _read_product_name(page)
    add_button = page.get_by_role("button", name=_ADD_BUTTON_NAME)
    has_add = add_button.count() > 0
    has_stepper = page.locator(SELECTORS["stepper_input"]).count() > 0

    if not name and not product_id:
        # The URL did not resolve to a real product page — stale/discontinued.
        raise ProductUnavailableError(
            item,
            "product page rendered no title and no product id — "
            "the buy URL is likely stale or the product discontinued",
        )
    if not product_id:
        raise AddToCartFailed(
            item, "could not read the numeric productId from the page URL"
        )
    if not name:
        raise AddToCartFailed(item, "product title not found — page layout unexpected")
    if not has_add or not has_stepper:
        raise OutOfStockError(item)

    target = item.comprar
    qty = _cart_qty_by_id(page, auth, product_id)
    if qty >= target:
        logger.info(
            "✅ [ametller] %s — already %d in cart (≥ %d wanted), leaving as is",
            item.comida, qty, target,
        )
        return

    for attempt in range(1, _MAX_ADD_ATTEMPTS + 1):
        delta = target - qty
        _set_stepper(page, delta, item)
        page.get_by_role("button", name=_ADD_BUTTON_NAME).first.click()
        human_delay(*_NAV_SETW)
        # Poll the basket until `target` shows up (early-return on success).
        auth = _read_auth(page)
        qty = _cart_qty_settled(page, auth, product_id, target=target)
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
            # Refresh `qty` from the post-reload basket so the next delta is
            # accurate — if the previous attempt's add actually landed despite
            # the verification miss, this avoids a double-add.
            auth = _read_auth(page)
            qty = _cart_qty_by_id(page, auth, product_id)
            if qty >= target:
                logger.info(
                    "✅ [ametller] %s — %d in cart after reload", item.comida, qty,
                )
                return

    raise AddToCartFailed(
        item,
        f"cart line shows {qty}, expected {target} after {_MAX_ADD_ATTEMPTS} attempts",
    )


# ─────────────────────────────────────────────────────────────────────────────
# Whole-cart helpers (run-level before/after snapshot + clean mode, issue #19).
# ─────────────────────────────────────────────────────────────────────────────


def _require_registered_auth(page: Page) -> dict:
    """Navigate to the storefront home and return a registered-shopper auth.

    Raises:
        SessionExpiredError: the saved profile session has lapsed to a guest.
    """
    goto_with_login_check(page, "ametller", HOME_URL)
    human_delay(*_NAV_SETW)
    auth = _read_auth(page)
    if auth["customer_type"] != "registered":
        raise SessionExpiredError("ametller")
    return auth


def read_cart_total(page: Page) -> int:
    """Return the total units across the whole Ametller cart (all lines summed).

    Reads the authoritative SCAPI basket. Used for the run-level before/after
    snapshot.

    Raises:
        SessionExpiredError: the saved profile session has lapsed to a guest.
    """
    auth = _require_registered_auth(page)
    return sum(line["qty"] for line in _basket_lines(page, auth))


def _delete_basket_item(
    page: Page, auth: dict, basket_id: str, item_id: str
) -> bool:
    """Remove one basket line via SCAPI Shopper Baskets ``DELETE …/items/…``.

    Returns True when the delete succeeded. Best-effort: a failed call logs and
    returns False so clearing continues with the remaining lines.
    """
    token = auth.get("token") or ""
    if not token or not basket_id or not item_id:
        return False
    url = (
        f"{_SCAPI_BASE}/checkout/shopper-baskets/v1/organizations/{_SCAPI_ORG}"
        f"/baskets/{basket_id}/items/{item_id}?siteId={_SCAPI_SITE}"
    )
    try:
        resp = page.request.delete(
            url, headers={"Authorization": f"Bearer {token}"}, timeout=15000
        )
        if not resp.ok:
            logger.warning(
                "⚠️ [ametller] basket DELETE %d for item %s", resp.status, item_id
            )
            return False
        return True
    except Exception as err:  # noqa: BLE001 — keep clearing the other lines
        logger.warning("⚠️ [ametller] basket DELETE raised %s for item %s", err, item_id)
        return False


def clear_cart(page: Page) -> int:
    """Empty the Ametller cart completely, returning the unit count removed.

    Enumerates every basket line via SCAPI and deletes each. Idempotent: a
    no-op (returns 0) on an already empty cart.

    Raises:
        SessionExpiredError: the saved profile session has lapsed to a guest.
        AddToCartFailed: the basket still held lines after deleting every one.
    """
    auth = _require_registered_auth(page)
    items = _basket_items(page, auth)
    if not items:
        logger.info("🛒 [ametller] cart already empty — nothing to clear")
        return 0

    removed = 0
    for it in items:
        if _delete_basket_item(page, auth, it["basketId"], it["itemId"]):
            removed += it["qty"]
    human_delay(*_NAV_SETW)

    remaining = sum(line["qty"] for line in _basket_lines(page, auth))
    if remaining > 0:
        raise AddToCartFailed(
            CartItem("ametller", "(clear cart)", 0, ""),
            f"cart still holds {remaining} unit(s) after clearing",
        )
    logger.info("✅ [ametller] cart cleared (%d unit(s) removed)", removed)
    return removed
