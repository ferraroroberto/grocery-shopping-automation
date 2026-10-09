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
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Optional

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
from automation.errors import AddToCartFailed, OutOfStockError, ProductUnavailableError
from automation.grocery_reader import read_cart_items
from automation.models import CartItem
from automation.purchase_log import write_purchase_logs, write_run_records
from automation.report import (
    ADDED_NOT_IN_CART,
    ADDED_UNVERIFIED,
    ADDED_VERIFIED,
    ERROR,
    NO_URL,
    OUT_OF_STOCK,
    UNAVAILABLE,
    WOULD_ADD,
    ItemOutcome,
    RunReport,
    miss_line,
)

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data import CONFIG, REPO_ROOT  # noqa: E402
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
            "pay. Not for unattended runs (it blocks on stdin)."
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
    clear "can't be bought here" (out of stock, unavailable) or a lost login.
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

    With the cart read, an "added" item short of its quantity becomes
    ``ADDED_NOT_IN_CART``, and a failed add the cart nonetheless holds in full
    becomes ``ADDED_VERIFIED``. With no cart read, adds stay ``ADDED_UNVERIFIED``.
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
        elif outcome.status == ADDED_UNVERIFIED:
            outcome.status = ADDED_NOT_IN_CART
            outcome.message = f"the final cart holds {qty:g} of {item.comprar}"
            logger.error("❌ [%s] %s — added, but %s", store, item.comida, outcome.message)


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

    When `keep_open` is set, the browser is left on screen after the last item
    and the run blocks on Enter — so the operator can review the cart and pay
    before the context closes and the next store starts.
    """
    handler = HANDLERS[store]

    def on_wait(delay_s: int, attempt: int, total: int) -> None:
        logger.info(
            "⏳ [%s] waiting for the browser — another job is using it (retry %d/%d in %ds)",
            store, attempt, total, delay_s,
        )

    playwright, context, page = launch_context(headless=headless, wait_for_profile=True, on_wait=on_wait)
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

        outcomes: list[ItemOutcome] = []
        retry: list[ItemOutcome] = []
        for item in items:
            outcome = report.record(item, ERROR)
            outcomes.append(outcome)
            if not item.buscador:
                outcome.status, outcome.message = NO_URL, "the list row has no product URL"
                logger.info("⚠️  [%s] %s — no URL, skipping", store, item.comida)
                continue
            if _add_one(handler, page, store, outcome):
                retry.append(outcome)
            human_delay()

        t0 = time.monotonic()
        for outcome in retry:
            logger.info("🔁 [%s] retrying %s once", store, outcome.item.comida)
            outcome.retries = 1
            _add_one(handler, page, store, outcome)
            human_delay()
        t_retry = time.monotonic() - t0

        t0 = time.monotonic()
        lines = _read_cart_lines_safe(handler, page, store)
        report.cart_lines[store] = lines
        _verify_against_cart(handler, store, outcomes, lines)
        t_check = time.monotonic() - t0

        t0 = time.monotonic()
        for outcome in outcomes:
            if outcome.status in (UNAVAILABLE, OUT_OF_STOCK):
                _suggest(handler, page, store, outcome)
        t_suggest = time.monotonic() - t0
        logger.info("⏱️ [%s] cart guard: %d retry(ies) %.1fs, cart check %.1fs, suggestions %.1fs",
                    store, len(retry), t_retry, t_check, t_suggest)

        after = _read_cart_total_safe(handler, page, store)
        if after is not None:
            report.cart_after[store] = after

        if keep_open:
            logger.info(
                "🟢 [%s] cart filled — browser left open. Click the cart icon "
                "to review and pay, then press Enter here to close it.", store,
            )
            try:
                input(f"  ↳ press Enter to close the {store} browser… ")
            except EOFError:
                # No interactive stdin (e.g. spawned from the app) — don't block.
                logger.warning("⚠️  --keep-open: no interactive stdin, closing immediately")
    finally:
        context.close()
        playwright.stop()


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
    logs_dir = REPO_ROOT / CONFIG["automation"].get("purchase_logs_dir", "purchase_logs")
    return write_purchase_logs(report.added, logs_dir) + write_run_records(report, logs_dir, console_text)


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
            try:
                _process_store_live(
                    store, group, report,
                    headless=args.headless, keep_open=args.keep_open,
                    cart_mode=args.cart_mode,
                )
            except ProfileNotInitializedError as err:
                logger.error("❌ %s", err)
                return 2
            except ProfileBusyError as err:
                # Keep going: earlier stores' adds must still reach the summary
                # and the purchase log, and a later store may find the profile free.
                logger.error("❌ [%s] %s", store, err)
                for item in group:
                    report.record(item, ERROR, "Chrome profile busy (held by another job)")

    summary = report.print_summary()
    if not args.dry_run:
        _notify(report)
    _write_purchase_log_if_live(report, args.dry_run, "\n".join([*capture.lines, summary]))
    if report.missing:
        return 1
    return EXIT_UNCONFIRMED if report.unconfirmed else 0


if __name__ == "__main__":
    raise SystemExit(main())
