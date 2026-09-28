"""Fetch a URL through real Chrome for stores that block plain HTTP (issue #145).

Some supermarket sites (Carrefour, Dia, …) answer ``curl``/``requests`` with a
bot-manager 403. This CLI loads the URL in real Chrome with the repo's stealth
launch config (:func:`automation.browser._open_context`) and prints the result,
so a store-research agent can keep working over "HTTP" semantics.

Each store gets its **own** throwaway profile under
``benchmark_runs/_state/chrome/<store>/`` — never the shared store-login
profile — so parallel agents (one per store) don't collide and cookies a store
sets on first visit (postal code, consent) persist across calls.

    python -m benchmark.browser_fetch --store carrefour --url "https://…"            # page text
    python -m benchmark.browser_fetch --store carrefour --url "https://…/api?q=x" --mode api   # JSON body
    python -m benchmark.browser_fetch --store dia --url "https://…" --mode html --out page.html

``--mode api`` first opens ``--home`` (default: the URL's origin) so the
bot-manager cookies exist, then issues the request from inside the page
(``fetch`` with the page's cookies) and prints the response body.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation.browser import _open_context  # noqa: E402

logger = logging.getLogger("benchmark.browser_fetch")

PROFILES_DIR = _REPO_ROOT / "benchmark_runs" / "_state" / "chrome"
_SETTLE_S = 3.0

_FETCH_JS = """async (url) => {
  const r = await fetch(url, {credentials: 'include', headers: {'Accept': 'application/json, text/plain, */*'}});
  return {status: r.status, body: await r.text()};
}"""


def fetch(store: str, url: str, mode: str = "text", home: Optional[str] = None,
          headless: bool = False, settle_s: float = _SETTLE_S) -> tuple[int, str]:
    """Load ``url`` in real Chrome on the store's own profile.

    Returns ``(status, body)``: the page text (``text``), full HTML (``html``)
    or the in-page ``fetch`` response body (``api``).
    """
    profile = PROFILES_DIR / store
    profile.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as pw:
        context, page = _open_context(pw, headless=headless, user_data_dir=profile)
        try:
            if mode == "api":
                origin = home or "{0.scheme}://{0.netloc}/".format(urlsplit(url))
                page.goto(origin, wait_until="domcontentloaded", timeout=45000)
                time.sleep(settle_s)
                result = page.evaluate(_FETCH_JS, url)
                return int(result["status"]), str(result["body"])
            resp = page.goto(url, wait_until="domcontentloaded", timeout=45000)
            time.sleep(settle_s)
            status = resp.status if resp else 0
            body = page.content() if mode == "html" else page.inner_text("body")
            return status, body
        finally:
            context.close()


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--store", required=True, help="Store key (selects the profile dir).")
    parser.add_argument("--url", required=True)
    parser.add_argument("--mode", choices=("text", "html", "api"), default="text")
    parser.add_argument("--home", help="Page to open before an --mode api fetch (default: URL origin).")
    parser.add_argument("--headless", action="store_true", help="Headless (more often blocked).")
    parser.add_argument("--settle", type=float, default=_SETTLE_S, help="Seconds to wait after load.")
    parser.add_argument("--out", help="Write the body to this file instead of stdout.")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    status, body = fetch(args.store, args.url, args.mode, args.home, args.headless, args.settle)
    logger.info("ℹ️ %s → HTTP %d, %d chars", args.url, status, len(body))
    if args.out:
        Path(args.out).write_text(body, encoding="utf-8")
    else:
        print(json.dumps({"status": status, "body": body}, ensure_ascii=False) if args.mode == "api" else body)
    return 0 if 200 <= status < 400 else 1


if __name__ == "__main__":
    raise SystemExit(main())
