"""Search the supermarket sites for a spoken product term (issue #87).

Given a Spanish free-text term (e.g. "sandia"), search every supported store and
return candidate products — name, price, product URL, thumbnail — so the app can
show them as cards the user validates. **No automated decision**: this module
only *finds and ranks for display*; a human picks which candidate fills
``buscador``.

Both stores' search mechanisms ride the logged-in shared Chrome profile
(``automation/browser.py``). Ametller was dropped from product search in issue
#211 (its cart handler and stored items stay):

* **Mercadona** — a clean Algolia-backed JSON endpoint the storefront itself
  calls: ``GET https://tornillos.mercadona.es/search?q={term}&lang=es``. The
  warehouse is taken from the session, so we navigate the storefront home first
  (which also doubles as the login check). Each hit carries ``id`` / ``slug`` /
  ``display_name`` / ``price_instructions`` / ``thumbnail``; the product URL is
  ``https://tienda.mercadona.es/product/{id}/{slug}``. Hits come
  relevance-ranked.

* **Carrefour** (issue #157) — behind Cloudflare like the cart handler
  (:mod:`automation.carrefour`), so this is a real-Chrome DOM read rather than
  a direct API call: load ``https://www.carrefour.es/?query={term}`` (``query=``,
  not ``q=`` — verified live 2026-09-29) and read the results grid, whose cards
  are ``[data-test="search-grid-result"]``. Each card's ``<a data-test=
  "result-link">`` carries the ``/R-<id>/p`` product path
  (:func:`automation.carrefour.product_id_from_url` extracts the id), the name
  sits in ``[data-test="result-title"] p``, the price in ``[data-test=
  "result-current-price"]``, and the image in ``img[data-test=
  "result-picture-image"]``. The DOM → dict extraction runs in-page
  (:data:`_CARREFOUR_CARD_JS`); the cleanup and validation is the pure
  :func:`_parse_carrefour_card`, so it is unit-tested without a browser. Cards
  come in the store's own relevance order, same as Mercadona.

Each store's outcome is streamed as it lands (issue #211): ``on_store`` gets a
``waiting`` event for every store before the browser is up, then ``searching``
and ``done`` / ``failed`` per store, so the app shows Mercadona's cards while
Carrefour is still loading and tells a failed store apart from "no results".
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Callable, Optional
from urllib.parse import quote

from playwright.sync_api import Page
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation import carrefour  # noqa: E402  (BASE_URL + product_id_from_url)
from automation.browser import (  # noqa: E402
    ProfileBusyError,
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

_CARREFOUR_SEARCH = "https://www.carrefour.es/?query={query}"
_CARREFOUR_CARD_SELECTOR = '[data-test="search-grid-result"]'

# How long to let the (client-rendered) results grid settle after navigation,
# and how long to wait for the first card before treating the term as a
# genuine no-match (verified live 2026-09-29, issue #157).
_CARREFOUR_SEARCH_SETTLE_S = 4.0
_CARREFOUR_CARD_TIMEOUT_MS = 10000

# In-page card → plain-dict extraction (no parsing logic here — that's the
# pure :func:`_parse_carrefour_card` below, so it can be unit-tested offline).
_CARREFOUR_CARD_JS = """els => els.map(el => {
  const linkEl = el.querySelector('a[data-test="result-link"]') || el.querySelector('a[data-test="result-title"]');
  const nameEl = el.querySelector('[data-test="result-title"] p') || el.querySelector('[data-test="result-title"]');
  const priceEl = el.querySelector('[data-test="result-current-price"]');
  const imgEl = el.querySelector('img[data-test="result-picture-image"]');
  return {
    href: linkEl ? (linkEl.getAttribute('href') || '') : '',
    name: (nameEl ? nameEl.textContent : (imgEl ? imgEl.getAttribute('alt') : '')) || '',
    price_text: priceEl ? priceEl.textContent : '',
    image: imgEl ? (imgEl.getAttribute('src') || '') : '',
  };
})"""

# Spanish-formatted euro price inside a card's text, e.g. "0,84 €" (a sale
# card also carries a struck-through "result-previous-price" — we only read
# "result-current-price", so this only ever matches the one that's charged).
_CARREFOUR_PRICE_RE = re.compile(r"(\d+),(\d+)\s*€")

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


def _parse_carrefour_price(text: str) -> Optional[float]:
    """Extract a Spanish-formatted euro amount ("0,84 €") from card text."""
    m = _CARREFOUR_PRICE_RE.search(text or "")
    if not m:
        return None
    return float(f"{m.group(1)}.{m.group(2)}")


def _parse_carrefour_card(raw: dict) -> Optional[dict]:
    """Turn one extracted search-result card into a candidate dict, or ``None``.

    Pure — no browser/page dependency, so it is unit-tested with recorded card
    data (see ``tests/test_product_search.py``) rather than a live Cloudflare
    fetch. Returns ``None`` for a card with no product id or no name (e.g. a
    sponsored banner tile that isn't a real result).
    """
    href = str(raw.get("href") or "")
    pid = carrefour.product_id_from_url(href)
    # Cards show the name with a trailing period ("... 500 g."); the other two
    # stores don't, so trim it for a consistent look across cards.
    name = re.sub(r"\.\s*$", "", str(raw.get("name") or "").strip())
    if not pid or not name:
        return None
    url = href if href.startswith("http") else f"{carrefour.BASE_URL}{href}"
    return {
        "name": name,
        "url": url,
        "price": _parse_carrefour_price(str(raw.get("price_text") or "")),
        "image": str(raw.get("image") or ""),
    }


def search_carrefour(page: Page, query: str, limit: int) -> list[Candidate]:
    """Search Carrefour by loading its results grid in real Chrome.

    Carrefour sits behind Cloudflare (like the cart handler,
    :mod:`automation.carrefour`), so this drives the page itself rather than
    calling an API directly. ``query=`` (not ``q=``) is the storefront's own
    search param. An empty results grid (genuinely no matches) returns ``[]``
    rather than raising.
    """
    goto_with_login_check(page, "carrefour", _CARREFOUR_SEARCH.format(query=quote(query)))
    time.sleep(_CARREFOUR_SEARCH_SETTLE_S)  # client-rendered grid needs a moment
    cards = page.locator(_CARREFOUR_CARD_SELECTOR)
    try:
        cards.first.wait_for(state="visible", timeout=_CARREFOUR_CARD_TIMEOUT_MS)
    except PlaywrightTimeoutError:
        logger.info("ℹ️ Carrefour search %r: no result cards within %d ms", query, _CARREFOUR_CARD_TIMEOUT_MS)
        return []
    raw_cards = cards.evaluate_all(_CARREFOUR_CARD_JS)
    parsed_cards = [c for c in map(_parse_carrefour_card, raw_cards) if c is not None]
    out: list[Candidate] = [
        _rank(query, "carrefour", c["name"], c["url"], c["price"], c["image"], i)
        for i, c in enumerate(parsed_cards[:limit])
    ]
    logger.info("ℹ️ Carrefour search %r: %d card(s), %d usable, %d returned",
                query, len(raw_cards), len(parsed_cards), len(out))
    return out


# Store key → search function. Order is the run order: Mercadona first — a
# plain JSON GET, so its cards land in seconds even when Carrefour's
# client-rendered grid is slow.
SEARCHERS = {"mercadona": search_mercadona, "carrefour": search_carrefour}

# Display names for progress messages.
_STORE_LABEL = {"mercadona": "Mercadona", "carrefour": "Carrefour"}

# A progress sink: called with a short human status line as the search
# advances, so the app can show what's happening instead of a static spinner.
ProgressFn = Callable[[str], None]

# A per-store sink (issue #211): called with one event dict each time a store
# changes state for a query — ``{"query", "store", "state", "candidates",
# "error", "reason", "elapsed_s"}`` where ``state`` is ``waiting`` (browser not
# up yet) / ``searching`` / ``done`` / ``failed``. ``done`` with no candidates
# is a genuine no-match; ``failed`` carries a short ``reason`` the UI turns into
# copy (``session`` / ``timeout`` / ``error``) and the raw ``error`` for logs.
StoreFn = Callable[[dict], None]


def _noop_progress(_msg: str) -> None:
    pass


def _noop_store(_event: dict) -> None:
    pass


def _failure_reason(err: Exception) -> str:
    """Classify a store failure into the short code the UI words for the user."""
    if isinstance(err, SessionExpiredError):
        return "session"
    if isinstance(err, PlaywrightTimeoutError):
        return "timeout"
    return "error"


def _store_event(query: str, store: str, state: str, *, candidates: Optional[list[dict]] = None,
                 error: Optional[str] = None, reason: Optional[str] = None,
                 elapsed_s: float = 0.0) -> dict:
    return {"query": query, "store": store, "state": state, "candidates": candidates or [],
            "error": error, "reason": reason, "elapsed_s": round(elapsed_s, 1)}


def _search_one(page: Page, query: str, limit: int, on_progress: ProgressFn = _noop_progress,
                on_store: StoreFn = _noop_store) -> dict:
    """Search every store in :data:`SEARCHERS` for a single ``query`` on an open page.

    Returns ``{"query", "candidates": [Candidate-dicts], "errors": {store: msg}}``.
    A store that errors (session expired, network) is recorded in ``errors`` and
    skipped; the other stores' results still come back. Candidates are ordered
    by store (:data:`SEARCHERS` order), each in the store's own relevance order
    — never silently reduced past ``limit`` without the cap being visible to the
    caller (per-store ``limit``). ``on_progress`` receives a status line before
    and after each store; ``on_store`` receives each store's result the moment
    it lands, so one slow store never hides another's cards (issue #211).
    """
    candidates: list[Candidate] = []
    errors: dict[str, str] = {}
    for store, searcher in SEARCHERS.items():
        label = _STORE_LABEL.get(store, store)
        on_progress(f"Searching {label} for “{query}”…")
        on_store(_store_event(query, store, "searching"))
        t0 = time.monotonic()
        try:
            found = searcher(page, query, limit)
        except Exception as err:  # noqa: BLE001 — one store failing must not sink the other
            elapsed = time.monotonic() - t0
            reason = _failure_reason(err)
            logger.warning("⚠️ [%s] search %r failed after %.1fs (%s): %s",
                           store, query, elapsed, reason, err)
            errors[store] = str(err)
            on_progress(f"{label}: couldn't search")
            on_store(_store_event(query, store, "failed", error=str(err), reason=reason,
                                  elapsed_s=elapsed))
            continue
        elapsed = time.monotonic() - t0
        logger.info("🔎 [%s] %d candidate(s) for %r in %.1fs", store, len(found), query, elapsed)
        candidates.extend(found)
        on_progress(f"{label}: {len(found)} result(s)")
        on_store(_store_event(query, store, "done", candidates=[asdict(c) for c in found],
                              elapsed_s=elapsed))
    return {
        "query": query,
        "candidates": [asdict(c) for c in candidates],
        "errors": errors,
    }


def search_all(queries: list[str], *, limit: int = DEFAULT_LIMIT,
               headless: bool = False, on_progress: ProgressFn = _noop_progress,
               on_store: StoreFn = _noop_store) -> dict:
    """Search every store for each term in ``queries``, in one Chrome session.

    Returns ``{"results": [ {query, candidates, errors}, … ]}`` — one entry per
    non-empty query, preserving input order. Headed by default: Mercadona's
    search endpoint 403s a headless client (bot detection), and the store sites
    are best driven headed anyway (see ``browser.py``). ``on_progress`` is called
    with short status lines as the run advances; ``on_store`` with each store's
    state change (see :data:`StoreFn`) — every store starts ``waiting`` until the
    shared Chrome profile is free and the browser is up.
    """
    terms = [q.strip() for q in queries if q and q.strip()]
    if not terms:
        return {"results": []}

    for term in terms:
        for store in SEARCHERS:
            on_store(_store_event(term, store, "waiting"))
    on_progress("Opening the browser…")
    t0 = time.monotonic()

    def on_wait(delay_s: int, attempt: int, total: int) -> None:
        on_progress(f"Waiting for the browser — another job is using it "
                    f"(retry {attempt}/{total} in {delay_s}s)")

    playwright, context, page = launch_context(headless=headless, wait_for_profile=True,
                                               on_wait=on_wait)
    logger.info("ℹ️ browser ready after %.1fs", time.monotonic() - t0)
    try:
        results = [_search_one(page, term, limit, on_progress, on_store) for term in terms]
    finally:
        context.close()
        playwright.stop()
    logger.info("✅ product search done in %.1fs for %d term(s)", time.monotonic() - t0, len(terms))
    on_progress("Preparing results…")
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

    def emit_store(event: dict) -> None:
        # One NDJSON line per store state change so the app can show each
        # store's cards (or its failure) as soon as it lands (issue #211).
        if args.json:
            print(json.dumps({"event": "store", **event}, ensure_ascii=False), flush=True)

    exit_code = 0
    try:
        result = search_all(args.query, limit=args.limit, headless=args.headless,
                            on_progress=emit_progress, on_store=emit_store)
    except (ProfileNotInitializedError, ProfileBusyError) as err:
        # Emit the reason on stdout too (not just stderr) so the app, which reads
        # this process's stdout JSON, can tell the user to log the stores in
        # (or that another job still holds the browser).
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
