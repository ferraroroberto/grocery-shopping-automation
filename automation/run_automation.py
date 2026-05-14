"""CLI entry point: add every pending grocery-list item to its store's cart.

Usage:
    python -m automation.run_automation [--store STORE] [--dry-run]
                                        [--limit N] [--headless] [--keep-open]

Reads the inventory via :func:`automation.grocery_reader.read_cart_items`,
groups the items by store, and dispatches each one to its store handler over a
single shared Chrome context per store. Prints a ✅/⚠️/❌ summary and exits
non-zero if anything failed.
"""

from __future__ import annotations

import argparse
import logging
import sys
from types import ModuleType
from typing import Optional

from automation import ametller, mercadona
from automation.browser import (
    ProfileNotInitializedError,
    SessionExpiredError,
    human_delay,
    launch_context,
)
from automation.errors import AddToCartFailed, OutOfStockError
from automation.grocery_reader import read_cart_items
from automation.report import RunReport

logger = logging.getLogger("automation.run_automation")

# Store key → handler module. Each handler exposes `add_to_cart(page, item)`.
HANDLERS: dict[str, ModuleType] = {
    "mercadona": mercadona,
    "ametller": ametller,
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


def _process_store_dry_run(store: str, items: list, report: RunReport) -> None:
    """Record what would happen for `store` without launching a browser."""
    for item in items:
        if not item.buscador:
            report.skipped_no_url.append(item)
            logger.info("⚠️  [%s] %s — no URL, would skip", store, item.comida)
            continue
        report.added.append(item)
        logger.info("🔎 [%s] DRY RUN would add %s ×%d", store, item.comida, item.comprar)


def _process_store_live(
    store: str,
    items: list,
    report: RunReport,
    *,
    headless: bool,
    keep_open: bool = False,
) -> None:
    """Open a Chrome context for `store` and run its handler over `items`.

    When `keep_open` is set, the browser is left on screen after the last item
    and the run blocks on Enter — so the operator can review the cart and pay
    before the context closes and the next store starts.
    """
    handler = HANDLERS[store]
    try:
        playwright, context, page = launch_context(headless=headless)
    except ProfileNotInitializedError:
        raise
    try:
        for item in items:
            if not item.buscador:
                report.skipped_no_url.append(item)
                logger.info("⚠️  [%s] %s — no URL, skipping", store, item.comida)
                continue
            try:
                handler.add_to_cart(page, item)
                report.added.append(item)
            except OutOfStockError:
                logger.warning("⚠️  [%s] %s — out of stock", store, item.comida)
                report.errors.append((item, "out of stock"))
            except (AddToCartFailed, SessionExpiredError) as err:
                logger.error("❌ [%s] %s — %s", store, item.comida, err)
                report.errors.append((item, str(err)))
            except Exception as err:  # noqa: BLE001 — keep the run going
                logger.exception("❌ [%s] %s — unexpected error", store, item.comida)
                report.errors.append((item, f"{type(err).__name__}: {err}"))
            human_delay()
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


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

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

    report = RunReport()
    for store, group in groups.items():
        if store not in HANDLERS:
            logger.warning(
                "⚠️  No handler for store '%s' — skipping %d item(s)", store, len(group)
            )
            for item in group:
                report.errors.append((item, f"no handler for store '{store}'"))
            continue

        logger.info("── %s: %d item(s) ──", store, len(group))
        if args.dry_run:
            _process_store_dry_run(store, group, report)
        else:
            try:
                _process_store_live(
                    store, group, report,
                    headless=args.headless, keep_open=args.keep_open,
                )
            except ProfileNotInitializedError as err:
                logger.error("❌ %s", err)
                return 2

    report.print_summary()
    return 1 if report.errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
