"""CLI entry point: report each store's saved login on the shared Chrome profile.

Usage:
    python -m automation.check_logins [--store STORE] [--json] [--headless]

Read-only (issue #217): opens the shared profile once (waiting with the
profile backoff if a sibling job holds it) and, for every store handler that
exposes ``check_login(page)``, loads the storefront and asks the store's own
session signal. It never reads or writes a cart, and never logs in or out.

Each store reports ``logged_in``, ``logged_out`` or ``unknown`` with a detail
line. ``unknown`` means the signal could not be read (a bot challenge, an
unexpected API answer, a timeout) or the store has no read-only check wired.
``--json`` prints ``{store: {"state", "detail"}}`` to stdout for the app; the
log lines go to stderr. Exits 0 when every checked store is logged in, else 1.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from types import ModuleType
from typing import Optional

from playwright.sync_api import Page

from automation.browser import NotLoggedInError, SessionExpiredError, launch_context
from automation.run_automation import HANDLERS

logger = logging.getLogger("automation.check_logins")

LOGGED_IN = "logged_in"
LOGGED_OUT = "logged_out"
UNKNOWN = "unknown"


def check_store(handler: ModuleType, page: Page) -> dict[str, str]:
    """Classify one store's login → ``{"state", "detail"}``. Never raises."""
    check = getattr(handler, "check_login", None)
    if check is None:
        return {"state": UNKNOWN, "detail": "no read-only login check for this store"}
    try:
        check(page)
    except (NotLoggedInError, SessionExpiredError) as err:
        return {"state": LOGGED_OUT, "detail": str(err)}
    except Exception as err:  # noqa: BLE001 — any other failure means "can't tell"
        return {"state": UNKNOWN, "detail": f"{type(err).__name__}: {err}"}
    return {"state": LOGGED_IN, "detail": ""}


def check_all(stores: list[str], *, headless: bool = False) -> dict[str, dict[str, str]]:
    """Check ``stores`` over one Chrome context on the shared profile."""
    playwright, context, page = launch_context(headless=headless, wait_for_profile=True)
    try:
        results = {}
        for store in stores:
            results[store] = check_store(HANDLERS[store], page)
            logger.info("🔐 [%s] %s %s", store, results[store]["state"], results[store]["detail"])
        return results
    finally:
        context.close()
        playwright.stop()


def main(argv: Optional[list[str]] = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(description="Report each store's saved login (read-only).")
    parser.add_argument("--store", choices=sorted(HANDLERS), help="Only check this store.")
    parser.add_argument("--json", action="store_true", help="Print the result as JSON on stdout.")
    parser.add_argument("--headless", action="store_true", help="Run Chrome headless (default: headed).")
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.INFO, stream=sys.stderr,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    results = check_all([args.store] if args.store else list(HANDLERS), headless=args.headless)
    if args.json:
        print(json.dumps(results, ensure_ascii=False))
    else:
        for store, result in results.items():
            print(f"{store:<10} {result['state']:<10} {result['detail']}")
    return 0 if all(r["state"] == LOGGED_IN for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
