"""Search the supermarket sites for a spoken product term (issue #87).

Given a Spanish free-text term (e.g. "sandia"), search both supported stores and
return candidate products — name, price, product URL, thumbnail — so the app can
show them as cards the user validates. **No automated decision**: this module
only *finds and ranks for display*; a human picks which candidate fills
``buscador``.

Both search mechanisms were verified live on 2026-07-15 and ride the logged-in
shared Chrome profile (``automation/browser.py``):

* **Mercadona** — a clean Algolia-backed JSON endpoint the storefront itself
  calls: ``GET https://tornillos.mercadona.es/search?q={term}&lang=es``. The
  warehouse is taken from the session, so we navigate the storefront home first
  (which also doubles as the login check). Each hit carries ``id`` / ``slug`` /
  ``display_name`` / ``price_instructions`` / ``thumbnail``; the product URL is
  ``https://tienda.mercadona.es/product/{id}/{slug}``. Hits come
  relevance-ranked.

* **Ametller** — Salesforce Commerce Cloud's SCAPI Shopper Search
  ``product-search`` endpoint, riding the same SLAS token the basket code reads
  (:func:`automation.ametller._read_auth`). ``total == 0`` means the store
  genuinely carries no match (e.g. it has no watermelon). Names come back in
  **Catalan** ("Alvocat" for aguacate), but the store's own engine already
  matched the Spanish query — so we **preserve its relevance order** rather than
  re-ranking by string similarity, which would be wrong for Catalan. The
  product URL is ``https://www.ametllerorigen.com/es/{slug}/{productId}.html``
  where the slug is slugified from the (Catalan) name — the site 301-redirects
  it to the canonical Spanish-slug URL. Do **not** pass ``locale`` to SCAPI
  (``es-ES`` → 400 "Unsupported Locale"); omitting it works.
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from playwright.sync_api import Page

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation import ametller  # noqa: E402  (SCAPI constants + auth reader)
from automation.browser import (  # noqa: E402
    ProfileNotInitializedError,
    SessionExpiredError,
    goto_with_login_check,
    launch_context,
)
from src.product_match import label as match_label  # noqa: E402
from src.product_match import score as match_score  # noqa: E402

logger = logging.getLogger("automation.product_search")

# Store search endpoints / URL templates.
_MERCADONA_HOME = "https://tienda.mercadona.es/"
_MERCADONA_SEARCH = "https://tornillos.mercadona.es/search"
_MERCADONA_PRODUCT = "https://tienda.mercadona.es/product/{id}/{slug}"
_AMETLLER_SEARCH_PAGE = "https://www.ametllerorigen.com/es/search"
_AMETLLER_SEARCH = (
    f"{ametller._SCAPI_BASE}/search/shopper-search/v1/organizations/"
    f"{ametller._SCAPI_ORG}/product-search"
)
_AMETLLER_PRODUCT = "https://www.ametllerorigen.com/es/{slug}/{pid}.html"

# Loading the storefront search page authorises the SLAS token for the SCAPI
# Shopper-Search scope — a direct call after only visiting the home page 401s
# (verified live 2026-07-15). This is how long to let that page settle first.
_AMETLLER_SEARCH_SETTLE_S = 4.0

# Per-store cap on candidates returned for display.
DEFAULT_LIMIT = 8


@dataclass
class Candidate:
    """One store product proposed for the spoken term — everything a card needs."""

    store: str
    name: str
    product_url: str
    price_text: str          # e.g. "5,15 €" — display-ready
    price_eur: Optional[float]
    thumbnail: str
    native_rank: int         # the store's own relevance position (0 = best)
    score: float             # src.product_match.score vs the query (display aid)
    match: str               # "strong" | "partial" | "weak" (display label)


def _slugify(name: str) -> str:
    """Slugify a product name for the Ametller ``/es/{slug}/{id}.html`` URL."""
    decomposed = unicodedata.normalize("NFKD", name)
    ascii_only = "".join(c for c in decomposed if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "-", ascii_only.lower()).strip("-")


def _fmt_price(value: Optional[float]) -> str:
    """Format euros the Spanish way ("5,15 €"), or "" when unknown."""
    if value is None:
        return ""
    return f"{value:.2f} €".replace(".", ",")


def _to_float(value: object) -> Optional[float]:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _rank(query: str, store: str, name: str, url: str, price: Optional[float],
          thumb: str, native_rank: int) -> Candidate:
    s = match_score(query, name)
    return Candidate(
        store=store, name=name, product_url=url,
        price_text=_fmt_price(price), price_eur=price, thumbnail=thumb,
        native_rank=native_rank, score=s, match=match_label(s),
    )


def search_mercadona(page: Page, query: str, limit: int) -> list[Candidate]:
    """Search Mercadona via its Algolia search endpoint. Session-ridden."""
    goto_with_login_check(page, "mercadona", _MERCADONA_HOME)
    resp = page.request.get(
        _MERCADONA_SEARCH, params={"q": query, "lang": "es"}, timeout=20000
    )
    if not resp.ok:
        raise RuntimeError(f"Mercadona search returned {resp.status}")
    hits = resp.json().get("hits", []) or []
    out: list[Candidate] = []
    for i, h in enumerate(hits[:limit]):
        pid, slug = h.get("id"), h.get("slug")
        if not pid or not slug:
            continue
        pi = h.get("price_instructions") or {}
        out.append(_rank(
            query, "mercadona", str(h.get("display_name") or "").strip(),
            _MERCADONA_PRODUCT.format(id=pid, slug=slug),
            _to_float(pi.get("unit_price")), str(h.get("thumbnail") or ""), i,
        ))
    return out


def search_ametller(page: Page, query: str, limit: int) -> list[Candidate]:
    """Search Ametller via SCAPI Shopper Search, riding the SLAS token.

    Navigating to the storefront **search page** (not just the home page) first
    is load-bearing: it authorises the SLAS token for the Shopper-Search scope,
    so the direct SCAPI call below returns 200 instead of a 401 (verified live
    2026-07-15 — home-only navigation 401s intermittently as the token ages).
    """
    goto_with_login_check(page, "ametller", f"{_AMETLLER_SEARCH_PAGE}?q={quote(query)}")
    time.sleep(_AMETLLER_SEARCH_SETTLE_S)  # let the search component prime the SCAPI session
    auth = ametller._read_auth(page)
    if auth["customer_type"] != "registered":
        raise SessionExpiredError("ametller")
    resp = page.request.get(
        _AMETLLER_SEARCH,
        params={"siteId": ametller._SCAPI_SITE, "q": query, "limit": limit},
        headers={"Authorization": f"Bearer {auth['token']}"},
        timeout=20000,
    )
    if not resp.ok:
        raise RuntimeError(f"Ametller search returned {resp.status}")
    data = resp.json()
    hits = data.get("hits", []) or []
    out: list[Candidate] = []
    for i, h in enumerate(hits[:limit]):
        pid = h.get("productId")
        name = str(h.get("productName") or "").strip()
        if not pid or not name:
            continue
        out.append(_rank(
            query, "ametller", name,
            _AMETLLER_PRODUCT.format(slug=_slugify(name), pid=pid),
            _to_float(h.get("price")), str((h.get("image") or {}).get("disBaseLink") or ""), i,
        ))
    return out


# Store key → search function.
SEARCHERS = {"mercadona": search_mercadona, "ametller": search_ametller}

# Display names for progress messages.
_STORE_LABEL = {"mercadona": "Mercadona", "ametller": "Ametller"}

# A progress sink: called with a short human (Spanish) status line as the search
# advances, so the app can show what's happening instead of a static spinner.
ProgressFn = Callable[[str], None]


def _noop_progress(_msg: str) -> None:
    pass


def _search_one(page: Page, query: str, limit: int, on_progress: ProgressFn = _noop_progress) -> dict:
    """Search both stores for a single ``query`` on an open page.

    Returns ``{"query", "candidates": [Candidate-dicts], "errors": {store: msg}}``.
    A store that errors (session expired, network) is recorded in ``errors`` and
    skipped; the other store's results still come back. Candidates are ordered
    by store (Mercadona first, then Ametller), each in the store's own relevance
    order — never silently reduced past ``limit`` without the cap being visible
    to the caller (per-store ``limit``). ``on_progress`` receives a status line
    before and after each store so the caller can narrate the run.
    """
    candidates: list[Candidate] = []
    errors: dict[str, str] = {}
    for store, searcher in SEARCHERS.items():
        label = _STORE_LABEL.get(store, store)
        on_progress(f"Buscando «{query}» en {label}…")
        try:
            found = searcher(page, query, limit)
            logger.info("🔎 [%s] %d candidate(s) for %r", store, len(found), query)
            candidates.extend(found)
            on_progress(f"{label}: {len(found)} resultado(s)")
        except Exception as err:  # noqa: BLE001 — one store failing must not sink the other
            logger.warning("⚠️ [%s] search failed: %s", store, err)
            errors[store] = str(err)
            on_progress(f"{label}: sin resultados")
    return {
        "query": query,
        "candidates": [asdict(c) for c in candidates],
        "errors": errors,
    }


def search_all(queries: list[str], *, limit: int = DEFAULT_LIMIT,
               headless: bool = False, on_progress: ProgressFn = _noop_progress) -> dict:
    """Search every store for each term in ``queries``, in one Chrome session.

    Returns ``{"results": [ {query, candidates, errors}, … ]}`` — one entry per
    non-empty query, preserving input order. Headed by default: Mercadona's
    search endpoint 403s a headless client (bot detection), and the store sites
    are best driven headed anyway (see ``browser.py``). ``on_progress`` is called
    with short Spanish status lines as the run advances.
    """
    terms = [q.strip() for q in queries if q and q.strip()]
    if not terms:
        return {"results": []}

    on_progress("Abriendo el navegador…")
    playwright, context, page = launch_context(headless=headless, wait_for_profile=True)
    try:
        results = [_search_one(page, term, limit, on_progress) for term in terms]
    finally:
        context.close()
        playwright.stop()
    on_progress("Preparando resultados…")
    return {"results": results}


def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Search stores for product term(s).")
    p.add_argument("--query", required=True, action="append",
                   help="Spanish product term, e.g. 'sandia'. Repeatable for several items.")
    p.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="Max candidates per store.")
    p.add_argument("--headless", action="store_true",
                   help="Run without a window (note: Mercadona 403s a headless client).")
    p.add_argument("--json", action="store_true", help="Emit JSON to stdout (else a human summary).")
    p.add_argument("--debug", action="store_true", help="Verbose logging to stderr.")
    return p.parse_args(argv)


def main(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    # Logs go to stderr so --json stdout stays clean machine-readable JSON.
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        stream=sys.stderr,
    )
    if args.json:
        sys.stdout.reconfigure(encoding="utf-8")  # emoji-safe under capture

    def emit_progress(msg: str) -> None:
        # NDJSON progress events on stdout (only in --json mode) so the app can
        # narrate the run live; the final result is a distinct event line.
        if args.json:
            print(json.dumps({"event": "progress", "message": msg}, ensure_ascii=False), flush=True)
        else:
            print(f"… {msg}", file=sys.stderr, flush=True)

    exit_code = 0
    try:
        result = search_all(args.query, limit=args.limit, headless=args.headless,
                            on_progress=emit_progress)
    except ProfileNotInitializedError as err:
        # Emit the reason on stdout too (not just stderr) so the app, which reads
        # this process's stdout JSON, can tell the user to log the stores in.
        result = {"results": [], "error": str(err)}
        exit_code = 2

    if args.json:
        print(json.dumps({"event": "result", "result": result}, ensure_ascii=False))
    else:
        for entry in result["results"]:
            print(f"\n🔎 {entry['query']!r} — {len(entry['candidates'])} candidate(s)")
            for c in entry["candidates"]:
                print(f"  [{c['store']}] {c['name']}  {c['price_text']}  ({c['match']})")
                print(f"       {c['product_url']}")
            for store, msg in entry["errors"].items():
                print(f"  ⚠️ {store}: {msg}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
