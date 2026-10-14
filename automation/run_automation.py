"""CLI entry point: add every pending grocery-list item to its store's cart.

Usage:
    python -m automation.run_automation [--store STORE] [--dry-run]
                                        [--cart-mode {keep,clean}]
                                        [--limit N] [--headless] [--keep-open]

Reads the inventory via :func:`automation.grocery_reader.read_cart_items`,
groups the items by store, and dispatches each one to its store handler over a
single shared Chrome context per store. ``--cart-mode keep`` (default) adds the
list on top of the existing cart; ``clean`` empties the cart first. Snapshots
each store's whole-cart total before and after for a run-level delta.

The cart guard (issue #247): a transient add failure is retried once at the end
of the store's pass; then the store's own final cart is read and every intended
item reconciled against it — that, not the handler's "added", decides success.
Unavailable/out-of-stock items get a suggested alternative from the store's
search. The run ends on a summary of verified items and every miss with its
reason, sends it through the app's notifier, keeps a per-run record, and exits
non-zero when anything is not in the cart.

Fallbacks (issue #283): a row may declare a fallback product (``fallback_super``
+ ``fallback_url``). When the final cart shows the primary short — partly or
completely — the shortfall goes to the fallback: in the same store's session
when the fallback is in that store, else in a cross-store pass after every
store's session, which reopens Chrome once per store holding pending fallbacks.
An item still short after its fallback reads "could add only N of M".
"""

from __future__ import annotations

import argparse
import logging
import math
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Callable, Optional

from automation import ametller, carrefour, mercadona, product_search
from automation.browser import (
    NotLoggedInError,
    ProfileBusyError,
    ProfileNotInitializedError,
    SessionExpiredError,
    StoreAccessError,
    human_delay,
    launch_context,
)
from automation.errors import (
    AddToCartFailed,
    OutOfStockError,
    PartialStockError,
    ProductUnavailableError,
)
from automation.grocery_reader import read_cart_items
from automation.models import CartItem
from automation.purchase_log import write_purchase_logs, write_run_records
from automation.report import (
    ADDED_NOT_IN_CART,
    ADDED_UNVERIFIED,
    ADDED_VERIFIED,
    ERROR,
    NO_URL,
    NOT_TRIED,
    OUT_OF_STOCK,
    PARTIAL_IN_CART,
    UNAVAILABLE,
    WOULD_ADD,
    FallbackOutcome,
    ItemOutcome,
    RunReport,
    miss_line,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data import purchase_logs_dir  # noqa: E402
from src.notify import NotifierError  # noqa: E402
from src.notify_config import build_notify_notifier  # noqa: E402

logger = logging.getLogger("automation.run_automation")


def _force_utf8_streams() -> None:
    """Force stdout/stderr to UTF-8 so the emoji summary survives capture.

    When stdout is not an interactive console (a pipe, a file redirect, or the
    app's ``subprocess.PIPE``), Python falls back to the locale encoding —
    ``cp1252`` on Windows — which cannot encode the emoji and box-drawing
    characters in :meth:`RunReport.print_summary`, so a bare ``print()`` raises
    ``UnicodeEncodeError``. Reconfiguring to UTF-8 up front makes the summary
    encode cleanly on every path. Guarded because streams replaced by a test
    harness (e.g. pytest capture, ``io.StringIO``) do not expose
    ``reconfigure``.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8")

# Store key → handler module. Each handler exposes `add_to_cart(page, item)`.
HANDLERS: dict[str, ModuleType] = {
    "mercadona": mercadona,
    "ametller": ametller,
    "carrefour": carrefour,
}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Add pending grocery-list items to their store carts."
    )
    parser.add_argument(
        "--store",
        default=None,
        help="Only process this store (e.g. 'mercadona'). Default: all stores.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="List what would be added without opening a browser.",
    )
    parser.add_argument(
        "--cart-mode",
        choices=("keep", "clean"),
        default="keep",
        help=(
            "keep (default): add the list on top of whatever is already in the "
            "cart. clean: empty the store cart first, then add the list from "
            "zero (this wipes any manually-added extras)."
        ),
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Process at most N items (after store filtering).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run Chrome headless (default: headed).",
    )
    parser.add_argument(
        "--keep-open",
        action="store_true",
        help=(
            "After a store's cart is filled, leave the browser open and wait "
            "for Enter before closing it / moving on — so you can review and "
            "pay. Not for unattended runs (it blocks on stdin). A store another "
            "store's items fall back to pauses after the cross-store fallback "
            "pass instead, so its cart is never reviewed before its last add."
        ),
    )
    parser.add_argument("--debug", action="store_true", help="Enable debug logging")
    return parser.parse_args(argv)


def _process_store_dry_run(
    store: str, items: list, report: RunReport, *, cart_mode: str = "keep"
) -> None:
    """Record what would happen for `store` without launching a browser."""
    if cart_mode == "clean":
        logger.info(
            "🧹 [%s] DRY RUN would empty the cart first (clean mode)", store
        )
    for item in items:
        if not item.buscador:
            report.record(item, NO_URL, "the list row has no product URL")
            logger.info("⚠️  [%s] %s — no URL, would skip", store, item.comida)
            continue
        report.record(item, WOULD_ADD)
        logger.info("🔎 [%s] DRY RUN would add %s ×%d", store, item.comida, item.comprar)
        if item.fallback is not None:
            logger.info("🔎 [%s] %s — fallback if short: %s %s",
                        store, item.comida, item.fallback.store, item.fallback.url)


def _read_cart_total_safe(handler: ModuleType, page, store: str) -> Optional[int]:
    """Read a store's whole-cart total, returning None (logged) on any failure.

    The before/after snapshot is informational — a failed read must never abort
    the run, so this swallows and logs rather than raising.
    """
    try:
        return handler.read_cart_total(page)
    except Exception as err:  # noqa: BLE001 — snapshot is best-effort
        logger.warning("⚠️  [%s] could not read cart total: %s", store, err)
        return None


def _add_one(handler: ModuleType, page, store: str, outcome: ItemOutcome) -> bool:
    """Run the handler's add for ``outcome.item`` and set its status from the result.

    A success is only provisional (``ADDED_UNVERIFIED``) until the final cart
    check. Returns True when a failure is worth one retry — anything but a
    clear "can't be bought here" (out of stock, a stock cap, unavailable) or a
    lost login.
    """
    item = outcome.item
    try:
        handler.add_to_cart(page, item)
        outcome.status, outcome.message = ADDED_UNVERIFIED, ""
        return False
    except OutOfStockError:
        logger.warning("⚠️  [%s] %s — out of stock", store, item.comida)
        outcome.status, outcome.message = OUT_OF_STOCK, "out of stock"
        return False
    except PartialStockError as err:
        logger.warning("⚠️  [%s] %s — %s", store, item.comida, err)
        # Provisional: the final cart read, when there is one, overwrites it.
        outcome.status, outcome.message, outcome.cart_qty = PARTIAL_IN_CART, "likely a stock cap", err.in_cart
        return False
    except ProductUnavailableError as err:
        reason = getattr(err, "reason", str(err))
        logger.warning("🔗 [%s] %s — %s", store, item.comida, reason)
        outcome.status, outcome.message = UNAVAILABLE, reason
        return False
    except (NotLoggedInError, SessionExpiredError) as err:
        logger.error("❌ [%s] %s — %s", store, item.comida, err)
        outcome.status, outcome.message = ERROR, str(err)
        return False
    except (AddToCartFailed, StoreAccessError) as err:
        logger.error("❌ [%s] %s — %s", store, item.comida, err)
        outcome.status, outcome.message = ERROR, str(err)
    except Exception as err:  # noqa: BLE001 — keep the run going
        logger.exception("❌ [%s] %s — unexpected error", store, item.comida)
        outcome.status, outcome.message = ERROR, f"{type(err).__name__}: {err}"
    return True


def _add_with_retry(handler: ModuleType, page, store: str, outcomes: list[ItemOutcome]) -> int:
    """Add every outcome's item in order, then retry each transient failure once.

    Returns how many adds were retried.
    """
    retry: list[ItemOutcome] = []
    for outcome in outcomes:
        if _add_one(handler, page, store, outcome):
            retry.append(outcome)
        human_delay()
    for outcome in retry:
        logger.info("🔁 [%s] retrying %s once", store, outcome.item.comida)
        outcome.retries = 1
        _add_one(handler, page, store, outcome)
        human_delay()
    return len(retry)


def _read_cart_lines_safe(handler: ModuleType, page, store: str) -> Optional[dict[str, float]]:
    """The store's final cart (cart key → units), or None when it can't be read."""
    reader = getattr(handler, "read_cart_lines", None)
    if reader is None:
        logger.warning("⚠️  [%s] no cart reader for this store — adds are not confirmed", store)
        return None
    try:
        return reader(page)
    except Exception as err:  # noqa: BLE001 — an unread cart is "not confirmed", never "missing"
        logger.warning("⚠️  [%s] could not read the final cart (%s: %s) — adds are not confirmed",
                       store, type(err).__name__, err)
        return None


def _verify_against_cart(
    handler: ModuleType, store: str, outcomes: list[ItemOutcome], lines: Optional[dict[str, float]]
) -> None:
    """Reconcile every outcome with the store's final cart — the deciding check.

    With the cart read, any item holding some but fewer units than wanted
    becomes ``PARTIAL_IN_CART`` whatever the add reported, an "added" item with
    none becomes ``ADDED_NOT_IN_CART``, and a failed add the cart nonetheless
    holds in full becomes ``ADDED_VERIFIED``. With no cart read, adds stay
    ``ADDED_UNVERIFIED``.
    """
    if lines is None:
        return
    for outcome in outcomes:
        if outcome.status == NO_URL:
            continue
        item = outcome.item
        qty = lines.get(handler.cart_key(item.buscador), 0)
        outcome.cart_qty = qty
        if qty >= item.comprar:
            if outcome.status != ADDED_UNVERIFIED:
                logger.info("✅ [%s] %s — the add reported %s but the cart holds %s",
                            store, item.comida, outcome.status, qty)
                outcome.message = f"the add reported {outcome.status}: {outcome.message}"
            outcome.status = ADDED_VERIFIED
        elif qty > 0:
            if outcome.status not in (ADDED_UNVERIFIED, PARTIAL_IN_CART):
                outcome.message = f"the add reported {outcome.status}: {outcome.message}"
            outcome.status = PARTIAL_IN_CART
            logger.error("❌ [%s] %s — the final cart holds only %g of %d",
                         store, item.comida, qty, item.comprar)
        elif outcome.status in (ADDED_UNVERIFIED, PARTIAL_IN_CART):
            outcome.status = ADDED_NOT_IN_CART
            outcome.message = f"the final cart holds {qty:g} of {item.comprar}"
            logger.error("❌ [%s] %s — added, but %s", store, item.comida, outcome.message)


def _cart_key_fn(handler: ModuleType) -> Callable[[str], str]:
    """The handler's cart key, or the URL itself for a store with no cart reader."""
    return getattr(handler, "cart_key", None) or (lambda url: url)


def _claim_fallbacks(store: str, outcomes: list[ItemOutcome], report: RunReport) -> list[ItemOutcome]:
    """Open a fallback for every short primary that declares one.

    Returns the ones whose fallback is in ``store`` (filled in this session);
    the others are queued on ``report.pending_fallbacks`` for the cross-store
    pass. A shortfall of unknown size (a failed add, the cart unreadable) is
    left alone, since a fallback on top of units already there would overbuy.
    """
    same: list[ItemOutcome] = []
    for outcome in outcomes:
        declared, wanted = outcome.item.fallback, outcome.shortfall
        if declared is None or wanted == 0:
            continue
        if wanted is None:
            logger.warning("⚠️  [%s] %s — shortfall unknown (cart unreadable), fallback not tried",
                           store, outcome.item.comida)
            continue
        outcome.fallback = FallbackOutcome(declared.store, declared.url, wanted)
        if declared.store == store:
            same.append(outcome)
        else:
            report.pending_fallbacks.append(outcome)
    return same


def _units_placed(report: RunReport, key_of: Callable[[str], str], store: str, key: str) -> float:
    """Units this run put on ``store``'s cart line ``key`` — the base when its cart is unreadable."""
    total = 0.0
    for o in report.outcomes:
        if o.item.super_name.lower() == store and o.item.buscador and key_of(o.item.buscador) == key:
            total += o.primary_units
        fb = o.fallback
        if fb is not None and fb.store == store and key_of(fb.url) == key:
            total += fb.units
    return total


def _settle(outcome: ItemOutcome) -> None:
    """Set an item's overall status from what its primary and fallback supplied together."""
    fb, item = outcome.fallback, outcome.item
    total = outcome.total_units
    if total >= item.comprar:
        outcome.status = ADDED_VERIFIED if fb.status == ADDED_VERIFIED else ADDED_UNVERIFIED
        outcome.message = f"{fb.units:g} filled at fallback {fb.store} {fb.url}"
        logger.info("✅ [%s] %s — %s", item.super_name, item.comida, outcome.message)
        return
    if total > 0:
        outcome.status = PARTIAL_IN_CART
    reason = fb.status + (f": {fb.message}" if fb.message else "")
    outcome.message = "; ".join(m for m in (outcome.message, f"fallback {fb.store} {reason}") if m)
    logger.error("❌ [%s] %s — could add only %g of %d (primary %g, fallback %g)",
                 item.super_name, item.comida, total, item.comprar, outcome.primary_units, fb.units)


def _fallback_failed(outcome: ItemOutcome, status: str, reason: str) -> None:
    """Record a fallback the run could not add; the item stays short and reported."""
    outcome.fallback.status, outcome.fallback.message = status, reason
    _settle(outcome)


def _fill_fallbacks(
    handler: ModuleType,
    page,
    store: str,
    outcomes: list[ItemOutcome],
    lines: Optional[dict[str, float]],
    report: RunReport,
) -> None:
    """Add each outcome's shortfall at its fallback in ``store``, then reconcile with the cart.

    ``lines`` is ``store``'s cart as just read (``None`` when unreadable).
    Handlers set a line to an absolute total, so each fallback product's
    target is what its line already holds plus every shortfall routed to it.
    The cart read afterwards decides the units each item got, in list order.
    """
    key_of = _cart_key_fn(handler)
    groups: dict[str, list[ItemOutcome]] = {}
    for outcome in outcomes:
        groups.setdefault(key_of(outcome.fallback.url), []).append(outcome)
    base = {
        key: lines.get(key, 0) if lines is not None else _units_placed(report, key_of, store, key)
        for key in groups
    }
    adds: dict[str, ItemOutcome] = {}
    for key, group in groups.items():
        target = math.ceil(base[key]) + sum(o.fallback.wanted for o in group)
        names = ", ".join(o.item.comida for o in group)
        logger.info("↪️ [%s] fallback for %s — setting %s to %d", store, names, group[0].fallback.url, target)
        adds[key] = ItemOutcome(CartItem(store, f"{names} (fallback)", target, group[0].fallback.url), ERROR)
    _add_with_retry(handler, page, store, list(adds.values()))

    after = _read_cart_lines_safe(handler, page, store)
    if after is not None:
        report.cart_lines[store] = after
    for key, group in groups.items():
        add = adds[key]
        if after is not None:
            landed = after.get(key, 0)
        elif add.status == ADDED_UNVERIFIED:
            landed = add.item.comprar
        elif add.status == PARTIAL_IN_CART:
            landed = add.cart_qty
        else:
            landed = base[key]
        gained = max(0.0, landed - base[key])
        for outcome in group:
            fb = outcome.fallback
            fb.units = min(gained, fb.wanted)
            gained -= fb.units
            fb.cart_qty = None if after is None else landed
            if fb.units >= fb.wanted:
                fb.status, fb.message = (ADDED_VERIFIED if after is not None else ADDED_UNVERIFIED), ""
            elif fb.units > 0:
                fb.status, fb.message = PARTIAL_IN_CART, add.message
            elif add.status in (ADDED_UNVERIFIED, PARTIAL_IN_CART):
                fb.status, fb.message = ADDED_NOT_IN_CART, f"the cart line holds {landed:g}"
            else:
                fb.status, fb.message = add.status, add.message
            _settle(outcome)


def _suggest(handler: ModuleType, page, store: str, outcome: ItemOutcome) -> None:
    """Fill ``outcome.suggestion``: the store's best other search hit + other stores set up."""
    item = outcome.item
    parts: list[str] = []
    search = product_search.SEARCHERS.get(store)
    if search is not None:
        try:
            own = handler.cart_key(item.buscador)
            for cand in search(page, item.comida, 4):
                if handler.cart_key(cand.product_url) != own:
                    parts.append(f"{cand.name} ({cand.price_text or 'no price'}) {cand.product_url}")
                    break
        except Exception as err:  # noqa: BLE001 — a suggestion is best-effort
            logger.warning("⚠️  [%s] no suggestion for %s: %s", store, item.comida, err)
    if item.alt_stores:
        parts.append(f"also set up at {', '.join(item.alt_stores)}")
    outcome.suggestion = "; ".join(parts)


def _process_store_live(
    store: str,
    items: list,
    report: RunReport,
    *,
    headless: bool,
    keep_open: bool = False,
    cart_mode: str = "keep",
) -> None:
    """Open a Chrome context for `store`, add `items`, then verify the final cart.

    Snapshots the whole-cart total before and after processing so the summary
    can report the run-level delta. In ``clean`` mode the cart is emptied after
    the before-snapshot and before any item is added. A transient add failure
    is retried once after the pass; the final cart read then decides each
    item's outcome, and unavailable/out-of-stock items get a suggestion.

    After the check, every short item whose fallback is in this store gets
    its shortfall added here and verified (issue #283); fallbacks at other
    stores are queued for the cross-store pass.

    When `keep_open` is set, the browser is left on screen after the last item
    and the run blocks on Enter — so the operator can review the cart and pay
    before the context closes and the next store starts.
    """
    handler = HANDLERS[store]
    playwright, context, page = launch_context(
        headless=headless, wait_for_profile=True, on_wait=_on_wait_logger(store),
    )
    try:
        before = _read_cart_total_safe(handler, page, store)
        if before is not None:
            report.cart_before[store] = before

        if cart_mode == "clean":
            try:
                removed = handler.clear_cart(page)
                logger.info("🧹 [%s] cleared %d unit(s) from the cart", store, removed)
            except Exception as err:  # noqa: BLE001 — surface, but keep the run going
                logger.exception("❌ [%s] failed to clear the cart", store)
                report.record(CartItem(store, "(clear cart)", 0, ""), ERROR, f"clear cart failed: {err}")

        outcomes = [report.record(item, ERROR) for item in items]
        for outcome in outcomes:
            if not outcome.item.buscador:
                outcome.status, outcome.message = NO_URL, "the list row has no product URL"
                logger.info("⚠️  [%s] %s — no URL, skipping", store, outcome.item.comida)
        retried = _add_with_retry(handler, page, store, [o for o in outcomes if o.status != NO_URL])

        t0 = time.monotonic()
        lines = _read_cart_lines_safe(handler, page, store)
        report.cart_lines[store] = lines
        _verify_against_cart(handler, store, outcomes, lines)
        t_check = time.monotonic() - t0

        t0 = time.monotonic()
        same_store = _claim_fallbacks(store, outcomes, report)
        if same_store:
            _fill_fallbacks(handler, page, store, same_store, lines, report)
        t_fallback = time.monotonic() - t0

        t0 = time.monotonic()
        for outcome in outcomes:
            if outcome.status in (UNAVAILABLE, OUT_OF_STOCK):
                _suggest(handler, page, store, outcome)
        t_suggest = time.monotonic() - t0
        logger.info("⏱️ [%s] cart guard: %d retry(ies), cart check %.1fs, %d same-store fallback(s) %.1fs, "
                    "suggestions %.1fs", store, retried, t_check, len(same_store), t_fallback, t_suggest)

        after = _read_cart_total_safe(handler, page, store)
        if after is not None:
            report.cart_after[store] = after

        if keep_open:
            _pause_for_review(store)
    finally:
        context.close()
        playwright.stop()


def _on_wait_logger(store: str) -> Callable[[int, int, int], None]:
    """The ``launch_context`` callback that logs each wait for a busy Chrome profile."""

    def on_wait(delay_s: int, attempt: int, total: int) -> None:
        logger.info(
            "⏳ [%s] waiting for the browser — another job is using it (retry %d/%d in %ds)",
            store, attempt, total, delay_s,
        )

    return on_wait


def _pause_for_review(store: str) -> None:
    """``--keep-open``: leave the browser on screen and block on Enter."""
    logger.info(
        "🟢 [%s] cart filled — browser left open. Click the cart icon "
        "to review and pay, then press Enter here to close it.", store,
    )
    try:
        input(f"  ↳ press Enter to close the {store} browser… ")
    except EOFError:
        # No interactive stdin (e.g. spawned from the app) — don't block.
        logger.warning("⚠️  --keep-open: no interactive stdin, closing immediately")


def _process_fallback_store(
    store: str,
    outcomes: list[ItemOutcome],
    report: RunReport,
    *,
    headless: bool,
    keep_open: bool = False,
) -> None:
    """Reopen Chrome on ``store`` and add the cross-store fallbacks routed to it.

    The same add-and-verify step as a store's own session, on a cart read
    fresh at the start. With no ``outcomes`` this only reopens for the
    ``--keep-open`` review the store's own session deferred.
    """
    handler = HANDLERS[store]
    playwright, context, page = launch_context(
        headless=headless, wait_for_profile=True, on_wait=_on_wait_logger(store),
    )
    try:
        if store not in report.cart_before:
            before = _read_cart_total_safe(handler, page, store)
            if before is not None:
                report.cart_before[store] = before
        if outcomes:
            t0 = time.monotonic()
            lines = _read_cart_lines_safe(handler, page, store)
            if lines is not None:
                report.cart_lines[store] = lines
            _fill_fallbacks(handler, page, store, outcomes, lines, report)
            logger.info("⏱️ [%s] fallback pass: %d item(s) %.1fs", store, len(outcomes), time.monotonic() - t0)
        after = _read_cart_total_safe(handler, page, store)
        if after is not None:
            report.cart_after[store] = after
        if keep_open:
            _pause_for_review(store)
    finally:
        context.close()
        playwright.stop()


def _cross_store_pass(
    report: RunReport,
    order: list[str],
    *,
    only_store: Optional[str],
    headless: bool,
    keep_open: bool,
    review: list[str],
) -> None:
    """Fill the fallbacks at other stores: reopen each store holding some, once, in run order.

    A store with nothing pending is not reopened, except one in ``review``
    (its ``--keep-open`` pause was deferred here). A single-store run
    (``only_store``) opens no other store: its cross-store fallbacks are
    reported as not tried, never dropped.
    """
    by_store: dict[str, list[ItemOutcome]] = {}
    for outcome in report.pending_fallbacks:
        target = outcome.fallback.store
        if only_store:
            _fallback_failed(outcome, NOT_TRIED, f"this run was limited to {only_store}")
        elif target not in HANDLERS:
            _fallback_failed(outcome, NOT_TRIED, f"no handler for store '{target}'")
        else:
            by_store.setdefault(target, []).append(outcome)
    report.pending_fallbacks.clear()

    rank = {store: i for i, store in enumerate(order)}
    for store in sorted({*by_store, *review}, key=lambda s: (rank.get(s, len(rank)), s)):
        pending = by_store.get(store, [])
        logger.info("── %s: fallback pass, %d item(s) ──", store, len(pending))
        try:
            _process_fallback_store(store, pending, report, headless=headless, keep_open=keep_open)
        except (ProfileNotInitializedError, ProfileBusyError) as err:
            logger.error("❌ [%s] %s", store, err)
            for outcome in pending:
                _fallback_failed(outcome, ERROR, str(err))


# Exit code for a run with nothing missing but adds the store cart could not
# confirm — distinct from 0 (all verified) and 1 (items not in the cart).
EXIT_UNCONFIRMED = 3

# Telegram caps a message at 4096 characters; stay well clear.
_NOTIFY_MAX_CHARS = 3500


def notification_text(report: RunReport) -> str:
    """The run notification: counts, then one line per item not in the cart."""
    missing, unconfirmed = report.missing, report.unconfirmed
    verified = len(report.added) - len(unconfirmed)
    head = f"🛒 Cart run: ✅ {verified} verified in cart"
    if unconfirmed:
        head += f", ❔ {len(unconfirmed)} not confirmed (cart unreadable)"
    head += f", ❌ {len(missing)} NOT in cart" if missing else ", nothing missing"
    text = "\n".join([head, *(f"❌ {miss_line(o)}" for o in missing)])
    return text if len(text) <= _NOTIFY_MAX_CHARS else text[: _NOTIFY_MAX_CHARS - 1] + "…"


def _notify(report: RunReport) -> None:
    """Send the run summary through the app's notifier (best-effort, never raises)."""
    try:
        notifier = build_notify_notifier()
        if notifier is None:
            logger.info("ℹ️ notifier not configured — run summary not sent")
            return
        notifier.send_text(notification_text(report))
        logger.info("✅ run summary sent")
    except (NotifierError, OSError, ValueError) as err:
        logger.warning("⚠️ could not send the run summary: %s", err)


def _write_purchase_log_if_live(report: RunReport, dry_run: bool, console_text: str = "") -> list[Path]:
    """Persist the purchase log and this run's record, skipping dry runs entirely."""
    if dry_run:
        return []
    logs_dir = purchase_logs_dir()
    return write_purchase_logs(report.purchased, logs_dir) + write_run_records(report, logs_dir, console_text)


class _ConsoleCapture(logging.Handler):
    """Keeps every formatted log line, so the run record carries the console output."""

    def __init__(self) -> None:
        super().__init__()
        self.lines: list[str] = []

    def emit(self, record: logging.LogRecord) -> None:
        self.lines.append(self.format(record))


def main(argv: Optional[list[str]] = None) -> int:
    _force_utf8_streams()
    args = parse_args(argv)
    log_format = "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    logging.basicConfig(level=logging.DEBUG if args.debug else logging.INFO, format=log_format)
    capture = _ConsoleCapture()
    capture.setFormatter(logging.Formatter(log_format))
    logging.getLogger().addHandler(capture)
    try:
        return _run(args, capture)
    finally:
        logging.getLogger().removeHandler(capture)


def _run(args: argparse.Namespace, capture: _ConsoleCapture) -> int:
    items = read_cart_items(args.store)
    if args.limit is not None:
        items = items[: args.limit]
    if not items:
        logger.info("Nothing to do — no pending items%s.",
                    f" for store '{args.store}'" if args.store else "")
        return 0

    # Group by store, preserving spreadsheet order within each group.
    groups: dict[str, list] = {}
    for item in items:
        groups.setdefault(item.super_name.lower(), []).append(item)

    # A store other stores' items fall back to may be reopened by the cross-store
    # pass, so its --keep-open review waits until then: never pay a cart that
    # may still get adds.
    incoming = set() if args.store else {
        item.fallback.store for item in items
        if item.fallback is not None and item.fallback.store != item.super_name.lower()
    }
    deferred: list[str] = []

    report = RunReport(mode=args.cart_mode, dry_run=args.dry_run)
    for store, group in groups.items():
        if store not in HANDLERS:
            logger.warning(
                "⚠️  No handler for store '%s' — skipping %d item(s)", store, len(group)
            )
            for item in group:
                report.record(item, ERROR, f"no handler for store '{store}'")
            continue

        logger.info("── %s: %d item(s) ──", store, len(group))
        if args.dry_run:
            _process_store_dry_run(store, group, report, cart_mode=args.cart_mode)
        else:
            defer = args.keep_open and store in incoming
            try:
                _process_store_live(
                    store, group, report,
                    headless=args.headless, keep_open=args.keep_open and not defer,
                    cart_mode=args.cart_mode,
                )
                if defer:
                    deferred.append(store)
            except ProfileNotInitializedError as err:
                logger.error("❌ %s", err)
                return 2
            except ProfileBusyError as err:
                # Keep going: earlier stores' adds must still reach the summary
                # and the purchase log, and a later store may find the profile free.
                logger.error("❌ [%s] %s", store, err)
                for item in group:
                    report.record(item, ERROR, "Chrome profile busy (held by another job)")

    if not args.dry_run:
        _cross_store_pass(
            report, list(groups), only_store=args.store, headless=args.headless,
            keep_open=args.keep_open, review=deferred,
        )

    summary = report.print_summary()
    if not args.dry_run:
        _notify(report)
    _write_purchase_log_if_live(report, args.dry_run, "\n".join([*capture.lines, summary]))
    if report.missing:
        return 1
    return EXIT_UNCONFIRMED if report.unconfirmed else 0


if __name__ == "__main__":
    raise SystemExit(main())
