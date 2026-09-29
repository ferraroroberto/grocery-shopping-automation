"""Per-store product links, store picks and the cost simulator (issue #148).

UI-free. Each inventory row may carry one product URL per supermarket in an
optional ``url_<store>`` column (store keys from ``benchmark/stores.json``);
``super`` + ``buscador`` stay the *chosen* store and its URL, so the cart
automation and the email/audit flows keep working unchanged.

Prices and quantities come from the latest scored supermarket-benchmark run
(``benchmark_runs/<YYYY-MM-DD>/``); the cost maths is :mod:`benchmark.score`'s
own (``build_offers`` / ``price_assignment``), never re-implemented here.
Inventory rows are joined to benchmark items by
:func:`benchmark.build_basket.item_key` of ``comida``.

Every mutator persists through :func:`src.data.save_inventory_data` and rolls
the in-memory frame back when the save fails (e.g. the sheet is locked).

**Overrides and review state (#165)** live in
``<runs_dir>/_state/overrides.json`` (gitignored household data, never in the
xlsx and never written into the benchmark mappings)::

    {"items":   {<item key>: {<store>: {"name", "pack_size", "unit", "pack_price",
                                         "note", "updated", "benchmark_snapshot"}}},
     "checked": {<item key>: "<YYYY-MM-DD>"}}

An override is the household's own correction of one store's product for one
basket item. Any field left out (null) falls back to the benchmark's value, so
a price-only override keeps the benchmark's pack. :meth:`_RunContext.offer`
returns it as an ``Offer`` with status ``"override"``, so the simulator, the
per-item prices and the apply pack conversion all use it. ``benchmark_snapshot``
is the benchmark's ``{name, pack_size, unit, pack_price}`` when the override
was saved (null when the benchmark had none), so a later run that changes
those values is flagged ``benchmark_changed``. ``checked`` is the review
progress (any row's ``item_key(comida)``). A missing or unreadable file reads
as empty (⚠️ logged); a write sets an unreadable file aside first, and every
write is atomic (temp file + replace).

**Monthly cost what-if baseline (#183)** lives in
``<runs_dir>/_state/baseline.json``::

    {"set_at": "<ISO timestamp>", "run_date": "<YYYY-MM-DD>",
     "items": {<item key>: {"store", "name", "pack_size", "unit", "pack_price"}}}

:func:`set_baseline` freezes the list's current stores (and their price at
that moment) so a later :func:`simulate` prices its "today" side from there
instead of the benchmark's basket store — the switch becomes the new status
quo. It only applies while ``run_date`` still matches the latest run;
:func:`clear_baseline` (or a newer benchmark run) goes back to the benchmark's
status quo. It never touches the review reference points (``moved``,
``is_basket_store``, the quantity check's "before") — those keep comparing
against the benchmark until a new run.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
import tempfile
from datetime import date, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype

from benchmark import score as bscore
from benchmark.build_basket import item_key
from src.data import (
    COLUMNS,
    CONFIG,
    REPO_ROOT,
    InventoryFileError,
    SpreadsheetLockedError,
    cell_text,
    save_inventory_data,
    store_url_column,
    store_url_columns,
)

logger = logging.getLogger(__name__)

STORES_REGISTRY_PATH = REPO_ROOT / "benchmark" / "stores.json"
DEFAULT_RUNS_DIR = REPO_ROOT / "benchmark_runs"
_RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A store-search results page rather than a product: bot-protected stores
# (Bonpreu, Alcampo) left the benchmark only their search URL.
_SEARCH_URL_RE = re.compile(r"/(search|buscar|busqueda)(?:[/?#]|$)|[?&](q|query|search)=", re.IGNORECASE)


def link_kind(store: str, url: str) -> str:
    """``"search"`` (a search-results page), ``"suspect"`` (an http(s) URL
    that doesn't match the store's ``product_url_pattern`` in
    ``benchmark/stores.json`` — e.g. a Carrefour slug without its
    `/R-<id>/p` product id, which redirects to the home page; a store with
    no declared pattern is never "suspect") or ``"product"``.

    The pattern is only applied to an actual http(s) URL — some stores'
    ``buscador``/``url_<store>`` cells hold a plain search term instead of a
    link, which is a pre-existing data shape, not a bad product link.
    """
    if _SEARCH_URL_RE.search(url):
        return "search"
    if not _is_url(url):
        return "product"
    pattern = _product_url_pattern(store)
    if pattern is not None and not pattern.search(url):
        return "suspect"
    return "product"


@lru_cache(maxsize=None)
def _product_url_pattern(store: str) -> Optional[re.Pattern[str]]:
    """``store``'s compiled ``product_url_pattern``, read once per process (the registry is tracked config)."""
    raw = (load_registry().get(store) or {}).get("product_url_pattern")
    return re.compile(raw) if raw else None

# Ordering-frequency presets, in orders per month per store.
FREQUENCIES: dict[str, float] = {
    "weekly": 52 / 12,
    "2-weekly": 26 / 12,
    "monthly": 1.0,
}
DEFAULT_FREQUENCY = "weekly"

# Units an override may give a pack in (the benchmark's own units).
OVERRIDE_UNITS = ("kg", "l", "ud", "m")
# How often the benchmark should be re-run: a run's "next due" date.
REVIEW_INTERVAL_DAYS = 90
# Review flags, in display order (see :func:`checks`).
CHECK_FLAGS = ("moved", "pack_x2", "unit_mismatch", "search_link", "suspect_link",
               "override", "benchmark_changed", "stock_unconverted")
# Optional inventory column: the item's size/count per unit, shown in the detail.
UNIDADES_COLUMN = "unidades"


class StoreLinkError(ValueError):
    """A request the data can't satisfy: unknown row/store, missing URL, bad value."""


class NoBenchmarkRunError(LookupError):
    """No scored benchmark run to price or recommend from."""


# ─────────────────────────────────────────────────────────────────────────────
# Registry + run discovery
# ─────────────────────────────────────────────────────────────────────────────


def load_registry() -> dict[str, dict]:
    """Store key → registry entry from ``benchmark/stores.json``."""
    return json.loads(STORES_REGISTRY_PATH.read_text(encoding="utf-8"))["stores"]


def runs_dir() -> Path:
    """The benchmark runs directory: ``benchmark.runs_dir`` in config, else ``<repo>/benchmark_runs``."""
    raw = (CONFIG.get("benchmark") or {}).get("runs_dir")
    if not raw:
        return DEFAULT_RUNS_DIR
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def latest_run_dir(base: Optional[Path] = None) -> Optional[Path]:
    """Newest ``YYYY-MM-DD`` run that has been scored, or None.

    A run counts once it has both ``basket.json`` and ``scenarios.json``: a
    run still being researched has a basket but partial store files, and
    pricing from it would silently drop stores. The run date is the
    directory name.
    """
    base = base or runs_dir()
    if not base.is_dir():
        return None
    runs = sorted(
        p for p in base.iterdir()
        if p.is_dir() and _RUN_DIR_RE.match(p.name)
        and (p / "basket.json").is_file() and (p / "scenarios.json").is_file()
    )
    return runs[-1] if runs else None


def _require_run(run_dir: Optional[Path]) -> Path:
    run = run_dir or latest_run_dir()
    if run is None:
        raise NoBenchmarkRunError(f"No scored benchmark run found in {runs_dir()}")
    return run


def load_mappings(base: Optional[Path] = None) -> dict[str, dict[str, dict]]:
    """Store → {item key → mapping} from ``_state/mappings/*.json``."""
    mdir = (base or runs_dir()) / "_state" / "mappings"
    out: dict[str, dict[str, dict]] = {}
    for path in sorted(mdir.glob("*.json")) if mdir.is_dir() else []:
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            logger.warning("⚠️ Skipping unreadable mapping %s: %s", path.name, err)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Overrides store (#165) — format in the module docstring
# ─────────────────────────────────────────────────────────────────────────────


def overrides_path(base: Optional[Path] = None) -> Path:
    """``<runs_dir>/_state/overrides.json``."""
    return (base or runs_dir()) / "_state" / "overrides.json"


def _read_overrides(path: Path) -> tuple[dict[str, dict], bool]:
    """``(document, readable)``; a missing file is readable and empty."""
    empty: dict[str, dict] = {"items": {}, "checked": {}}
    if not path.is_file():
        return empty, True
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as err:
        logger.warning("⚠️ Ignoring unreadable overrides file %s: %s", path, err)
        return empty, False
    return {f: doc[f] if isinstance(doc.get(f), dict) else {} for f in ("items", "checked")}, True


def load_overrides(base: Optional[Path] = None) -> dict[str, dict]:
    """``{"items", "checked"}`` from the overrides file; missing or unreadable → empty."""
    return _read_overrides(overrides_path(base))[0]


def _update_overrides(base: Optional[Path], change: Callable[[dict[str, dict]], None]) -> dict[str, dict]:
    """Load, apply ``change(doc)`` and write the overrides file atomically.

    An unreadable file is renamed aside (``overrides.corrupt-<time>.json``)
    rather than overwritten, so a hand-edit gone wrong loses nothing.
    """
    path = overrides_path(base)
    doc, readable = _read_overrides(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not readable:
        aside = path.with_name(f"overrides.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
        os.replace(path, aside)
        logger.warning("⚠️ Moved unreadable overrides file aside to %s", aside.name)
    change(doc)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".overrides.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Monthly cost what-if baseline (#183) — format in the module docstring
# ─────────────────────────────────────────────────────────────────────────────


def baseline_path(base: Optional[Path] = None) -> Path:
    """``<runs_dir>/_state/baseline.json``."""
    return (base or runs_dir()) / "_state" / "baseline.json"


def _read_baseline(path: Path) -> tuple[Optional[dict], bool]:
    """``(document, readable)``; a missing file is readable and empty (None)."""
    if not path.is_file():
        return None, True
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
            raise ValueError("not a baseline document")
    except (OSError, ValueError) as err:
        logger.warning("⚠️ Ignoring unreadable baseline file %s: %s", path, err)
        return None, False
    return doc, True


def load_baseline(base: Optional[Path] = None) -> Optional[dict]:
    """The saved baseline document (``{"set_at", "run_date", "items"}``), or None."""
    return _read_baseline(baseline_path(base))[0]


def _write_baseline(base: Optional[Path], doc: Optional[dict]) -> None:
    """Write the baseline file atomically, or remove it when ``doc`` is None."""
    path = baseline_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    if doc is None:
        path.unlink(missing_ok=True)
        return
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".baseline.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _baseline_field(doc: Optional[dict], latest_run_date: Optional[str]) -> Optional[dict[str, Any]]:
    """The ``baseline`` field embedded in a simulate()/GET result: ``{"set_at", "run_date", "stale"}``."""
    if doc is None:
        return None
    run_date = doc.get("run_date")
    return {"set_at": doc.get("set_at"), "run_date": run_date, "stale": run_date != latest_run_date}


def _num(value: Any) -> Optional[float]:
    """A finite float, or None (hand-edited JSON may hold strings or junk)."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    return out if math.isfinite(out) else None


def _candidate(rec: Optional[dict], url: str) -> Optional[dict]:
    """The candidate (a record or one of its ``alternatives``) whose URL is ``url``."""
    if not rec or not url:
        return None
    for cand in [rec] + list(rec.get("alternatives") or []):
        if _same_url(url, cell_text(cand.get("url"))):
            return cand
    return None


def _best_candidate(rec: Optional[dict]) -> Optional[dict]:
    """The cheapest-per-unit matched candidate of a record (priced or not, e.g. unverified)."""
    cands = bscore.record_candidates(rec)
    return min(cands, key=lambda c: float(c["pack_price"]) / float(c["pack_size"])) if cands else None


def _override_offer(key: str, store: str, ov: dict, base: Optional[bscore.Offer]) -> Optional[bscore.Offer]:
    """``ov`` merged over the benchmark's offer; None when it still lacks a pack or a price."""
    size = _num(ov.get("pack_size")) or (base.pack_size if base else None)
    price = _num(ov.get("pack_price"))
    if price is None and base is not None:
        price = base.pack_price
    if not size or size <= 0 or price is None or price < 0:
        return None
    return bscore.Offer(store, key, cell_text(ov.get("name")) or (base.name if base else ""),
                        base.url if base else "", float(size), float(price), "override")


class _RunContext:
    """One scored run, loaded once per call: basket items, offers, delivery terms, overrides."""

    def __init__(self, run_dir: Path) -> None:
        self.run_dir = run_dir
        self.run_date = run_dir.name
        basket, self.stores = bscore.load_run(run_dir)
        self.all_items: dict[str, dict] = {it["key"]: it for it in basket["items"]}
        self.priced: dict[str, dict] = {
            k: it for k, it in self.all_items.items() if bscore.monthly_base(it) is not None
        }
        self.offers = bscore.build_offers(basket, self.stores)
        self.deliveries = {s: (doc or {}).get("delivery") or {} for s, doc in self.stores.items()}
        doc = load_overrides(run_dir.parent)
        self.overrides: dict[str, Any] = doc["items"]
        self.checked: dict[str, Any] = doc["checked"]

    def override(self, key: str, store: str) -> Optional[dict]:
        """The household's override for ``key`` at ``store``, if any."""
        per_store = self.overrides.get(key)
        entry = per_store.get(store) if isinstance(per_store, dict) else None
        return entry if isinstance(entry, dict) else None

    def override_stores(self, key: str) -> list[str]:
        per_store = self.overrides.get(key)
        return sorted(s for s, v in per_store.items() if isinstance(v, dict)) if isinstance(per_store, dict) else []

    def record(self, key: str, store: str) -> Optional[dict]:
        """The run's research record for ``key`` at ``store`` (any status)."""
        rec = ((self.stores.get(store) or {}).get("items") or {}).get(key)
        return rec if isinstance(rec, dict) else None

    def benchmark_offer(self, key: str, store: str) -> Optional[bscore.Offer]:
        """The benchmark's price for ``key`` at ``store``: today's product at its basket store, else the best offer."""
        item = self.priced.get(key)
        if item is None:
            return None
        if store == item["store"]:
            return bscore.baseline_offer(item)
        return self.offers.get(key, {}).get(store)

    def override_base(self, key: str, store: str) -> Optional[bscore.Offer]:
        """What an override at ``store`` falls back to for the fields it leaves out.

        The benchmark's priced offer; else its matched-but-unpriced candidate
        (e.g. an unverified match — the "benchmark" values the review dialog
        shows), since saving your own values over it is your acceptance of it.
        """
        base = self.benchmark_offer(key, store)
        if base is not None:
            return base
        cand = _best_candidate(self.record(key, store))
        if cand is None:
            return None
        return bscore.Offer(store, key, cell_text(cand.get("name")), cell_text(cand.get("url")),
                            float(cand["pack_size"]), float(cand["pack_price"]), cell_text(cand.get("status")))

    def offer(self, key: str, store: str) -> Optional[bscore.Offer]:
        """What ``key`` costs at ``store``: the household's override when there is one, else the benchmark's.

        The single pricing hook — the simulator, the per-item prices and the
        apply pack conversion all price through it.
        """
        ov = self.override(key, store)
        if ov is None or key not in self.priced:
            return self.benchmark_offer(key, store)
        return _override_offer(key, store, ov, self.override_base(key, store))


# ─────────────────────────────────────────────────────────────────────────────
# Row ↔ benchmark item
# ─────────────────────────────────────────────────────────────────────────────


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _same_url(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.strip().rstrip("/") == b.strip().rstrip("/")


def _row_store(df: pd.DataFrame, row: int) -> str:
    return cell_text(df.at[row, COLUMNS["super"]]).lower()


def rows_by_key(df: pd.DataFrame) -> dict[str, int]:
    """Benchmark item key → inventory row (first row wins on a duplicate name)."""
    out: dict[str, int] = {}
    for idx, comida in df[COLUMNS["comida"]].items():
        key = item_key(cell_text(comida))
        if key and key not in out:
            out[key] = int(idx)
    return out


def _resolve_row(df: pd.DataFrame, ref: Union[int, str], by_key: dict[str, int]) -> int:
    """An item id (int or digit string) or a benchmark key → inventory row."""
    text = str(ref).strip()
    if text.isdigit() and int(text) in df.index:
        return int(text)
    if text in by_key:
        return by_key[text]
    raise StoreLinkError(f"unknown item {ref!r} (neither an item id nor a benchmark item key)")


def _norm_store(store: str) -> str:
    store = (store or "").strip().lower()
    if not store:
        raise StoreLinkError("store is required")
    return store


# ─────────────────────────────────────────────────────────────────────────────
# Mutations with rollback
# ─────────────────────────────────────────────────────────────────────────────


class _Edits:
    """Cell writes on ``df`` that can be persisted once or rolled back together."""

    def __init__(self, df: pd.DataFrame) -> None:
        self.df = df
        self.cells: list[tuple[int, str, Any]] = []
        self.new_cols: list[str] = []

    def set(self, row: int, col: str, value: Any) -> None:
        df = self.df
        if col not in df.columns:
            df[col] = pd.Series([None] * len(df), index=df.index, dtype=object)
            self.new_cols.append(col)
        elif isinstance(value, str) and not (is_object_dtype(df[col]) or is_string_dtype(df[col])):
            # An all-empty column reads back from Excel as float64 NaN.
            df[col] = df[col].astype(object)
        self.cells.append((row, col, df.at[row, col]))
        df.at[row, col] = value

    def rollback(self) -> None:
        for row, col, old in reversed(self.cells):
            if col not in self.new_cols:
                self.df.at[row, col] = old
        if self.new_cols:
            self.df.drop(columns=self.new_cols, inplace=True)

    def commit(self, xlsx_path: Optional[str] = None) -> None:
        if not self.cells:
            return
        try:
            save_inventory_data(self.df, xlsx_path=xlsx_path)
        except (SpreadsheetLockedError, InventoryFileError):
            logger.error("❌ Save failed — rolling back %d cell write(s)", len(self.cells))
            self.rollback()
            raise


# ─────────────────────────────────────────────────────────────────────────────
# Import (first seed + quarterly re-import)
# ─────────────────────────────────────────────────────────────────────────────


def _mapping_url(key: str, store: str, entry: dict, ctx: Optional[_RunContext]) -> str:
    """The URL to seed for one mapping entry.

    A mapping may list ``alternatives`` (e.g. a bulk pack). The benchmark
    prices the cheapest per-unit candidate, so seed *that* product's URL when
    the latest run priced one of them — the link then matches the simulated
    price — else the mapping's main URL.
    """
    candidates = [entry] + list(entry.get("alternatives") or [])
    if ctx is not None:
        offer = ctx.offers.get(key, {}).get(store)
        if offer is not None and any(_same_url(offer.url, c.get("url", "")) for c in candidates):
            return offer.url
    return cell_text(entry.get("url"))


def import_latest(df: pd.DataFrame, *, save: bool = True, xlsx_path: Optional[str] = None,
                  base: Optional[Path] = None) -> dict[str, Any]:
    """Seed ``url_<store>`` cells from the benchmark mappings. Idempotent.

    Two sources, in order: each row's own ``buscador`` (when it is an
    http(s) link) fills ``url_<super>``; then every
    ``_state/mappings/<store>.json`` entry fills ``url_<store>`` for the row
    whose ``item_key(comida)`` matches. A non-empty cell is **never**
    overwritten, and a column is only created for a store that gets at least
    one URL. Serves both the first seed and every re-import after a run.

    Returns ``{"seeded", "skipped_existing", "unmatched", "unmatched_keys",
    "stores", "suspect_urls", "run_date"}``: cells written, URLs not written
    because the cell already had a value, mapping keys no inventory row
    matches, and per store the mapping URLs that don't look like a product
    page (each store's ``product_url_pattern`` in ``benchmark/stores.json``;
    imported anyway, logged ⚠️).
    """
    base = base or runs_dir()
    registry = load_registry()
    run = latest_run_dir(base)
    ctx = _RunContext(run) if run else None
    by_key = rows_by_key(df)
    edits = _Edits(df)
    seeded = skipped = 0
    touched: set[str] = set()

    def offer_cell(row: int, store: str, url: str) -> None:
        nonlocal seeded, skipped
        col = store_url_column(store)
        if col in df.columns and cell_text(df.at[row, col]):
            skipped += 1
            return
        edits.set(row, col, url)
        seeded += 1
        touched.add(store)

    for idx in df.index:
        store = _row_store(df, int(idx))
        url = cell_text(df.at[idx, COLUMNS["buscador"]])
        if store in registry and _is_url(url):
            offer_cell(int(idx), store, url)

    unmatched: set[str] = set()
    suspect: dict[str, int] = {}
    for store, mapping in load_mappings(base).items():
        if store not in registry:
            logger.warning("⚠️ Mapping for unknown store %r ignored", store)
            continue
        for key, entry in mapping.items():
            url = _mapping_url(key, store, entry, ctx)
            if not _is_url(url):
                continue
            if link_kind(store, url) == "suspect":
                suspect[store] = suspect.get(store, 0) + 1
            if key not in by_key:
                unmatched.add(key)
                continue
            offer_cell(by_key[key], store, url)

    if save:
        edits.commit(xlsx_path)
    counts = {
        "seeded": seeded, "skipped_existing": skipped, "unmatched": len(unmatched),
        "unmatched_keys": sorted(unmatched), "stores": sorted(touched),
        "suspect_urls": suspect, "run_date": ctx.run_date if ctx else None,
    }
    for store, n in sorted(suspect.items()):
        logger.warning("⚠️ %d %s mapping URL(s) lack a product id (%s) — they may redirect to the home page",
                       n, store, registry[store]["product_url_pattern"])
    logger.info("ℹ️ Store-URL import: %d seeded, %d skipped (already set), %d unmatched mapping key(s)",
                seeded, skipped, len(unmatched))
    if seeded:
        logger.info("✅ Seeded URLs for store(s): %s", ", ".join(sorted(touched)))
    return counts


# ─────────────────────────────────────────────────────────────────────────────
# Single-row edits
# ─────────────────────────────────────────────────────────────────────────────


def _check_row(df: pd.DataFrame, row: int) -> None:
    if row not in df.index:
        raise StoreLinkError(f"item {row} not found")


def store_url(df: pd.DataFrame, row: int, store: str) -> str:
    """The row's ``url_<store>`` value, or ``""``."""
    col = store_url_column(store)
    return cell_text(df.at[row, col]) if col in df.columns else ""


def set_store_url(df: pd.DataFrame, row: int, store: str, url: str, *, save: bool = True,
                  xlsx_path: Optional[str] = None) -> pd.DataFrame:
    """Set (or clear, with ``""``) one row's URL for one registry store.

    When ``store`` is the row's chosen store, ``buscador`` follows a new URL
    so the cart automation opens it; clearing leaves ``buscador`` alone.
    """
    _check_row(df, row)
    store = _norm_store(store)
    if store not in load_registry():
        raise StoreLinkError(f"unknown store {store!r} (not in benchmark/stores.json)")
    url = (url or "").strip()
    if url and not _is_url(url):
        raise StoreLinkError("url must start with http:// or https://")
    edits = _Edits(df)
    edits.set(row, store_url_column(store), url)
    if url and _row_store(df, row) == store:
        edits.set(row, COLUMNS["buscador"], url)
    if save:
        edits.commit(xlsx_path)
    logger.info("✅ Item %d: %s URL %s", row, store, "set" if url else "cleared")
    return df


def pick_store(df: pd.DataFrame, row: int, store: str, *, save: bool = True,
               xlsx_path: Optional[str] = None) -> pd.DataFrame:
    """Make ``store`` the row's chosen store: ``super`` + ``buscador`` together.

    ``cantidad`` is left as is — use :func:`apply_preview` /
    :func:`apply_changes` to convert it when pack sizes differ.
    """
    _check_row(df, row)
    store = _norm_store(store)
    url = store_url(df, row, store)
    if not url:
        raise StoreLinkError(f"item {row} has no {store} URL — set one before picking {store}")
    edits = _Edits(df)
    edits.set(row, COLUMNS["super"], store)
    edits.set(row, COLUMNS["buscador"], url)
    if save:
        edits.commit(xlsx_path)
    logger.info("✅ Item %d now bought at %s", row, store)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Simulation
# ─────────────────────────────────────────────────────────────────────────────


def resolve_frequency(orders_per_month: Optional[float] = None, frequency: Optional[str] = None) -> float:
    """Orders per month from an explicit value or a preset name (default weekly)."""
    if orders_per_month is not None:
        if orders_per_month <= 0:
            raise StoreLinkError("orders_per_month must be > 0")
        return float(orders_per_month)
    name = frequency or DEFAULT_FREQUENCY
    if name not in FREQUENCIES:
        raise StoreLinkError(f"unknown frequency {name!r}; use one of {', '.join(FREQUENCIES)}")
    return FREQUENCIES[name]


def _current_stores(df: pd.DataFrame, ctx: _RunContext, by_key: dict[str, int]) -> dict[str, str]:
    """Benchmark key → the store in the list now: the row's ``super``, else the basket's store."""
    out = {}
    for key, item in ctx.all_items.items():
        row = by_key.get(key)
        out[key] = (_row_store(df, row) if row is not None else "") or item["store"]
    return out


def _offer_snapshot(ctx: _RunContext, key: str, store: str) -> dict[str, Any]:
    """``{"store", "name", "pack_size", "unit", "pack_price"}`` for ``ctx.offer(key, store)`` right now (#183)."""
    offer = ctx.offer(key, store)
    if offer is None:
        return {"store": store, "name": None, "pack_size": None, "unit": None, "pack_price": None}
    ov = ctx.override(key, store)
    item_unit = ((ctx.all_items.get(key) or {}).get("current") or {}).get("unit") or ""
    unit = (cell_text(ov.get("unit")) if ov else "") or item_unit
    return {"store": store, "name": offer.name, "pack_size": offer.pack_size,
            "unit": unit or None, "pack_price": offer.pack_price}


def set_baseline(df: pd.DataFrame, run_dir: Optional[Path] = None) -> dict[str, Any]:
    """Freeze the list's current stores as the new "today" for :func:`simulate` (#183).

    Snapshots :func:`_current_stores` — each basket item's store as the list
    has it right now — plus its price through :meth:`_RunContext.offer` at
    this moment, keyed by benchmark item key. Written to
    ``<runs_dir>/_state/baseline.json``, replacing any earlier baseline.
    Returns ``{"set_at", "run_date"}``.
    """
    run = _require_run(run_dir)
    ctx = _RunContext(run)
    by_key = rows_by_key(df)
    stores = _current_stores(df, ctx, by_key)
    set_at = datetime.now().isoformat(timespec="seconds")
    items = {key: _offer_snapshot(ctx, key, store) for key, store in stores.items()}
    _write_baseline(run.parent, {"set_at": set_at, "run_date": ctx.run_date, "items": items})
    logger.info("ℹ️ Baseline set: %d item(s) frozen at %s (run %s)", len(items), set_at, ctx.run_date)
    return {"set_at": set_at, "run_date": ctx.run_date}


def clear_baseline(base: Optional[Path] = None) -> None:
    """Remove the baseline: :func:`simulate` goes back to the benchmark's status quo."""
    had = load_baseline(base) is not None
    _write_baseline(base, None)
    logger.info("ℹ️ Baseline %s", "removed — back to the benchmark's status quo" if had else "not set, nothing to remove")


def _price(ctx: _RunContext, stores: dict[str, str], orders_per_month: float) -> dict[str, Any]:
    """Total a key → store assignment with benchmark.score; list what can't be priced."""
    assignment: dict[str, bscore.Offer] = {}
    unpriced = []
    for key, store in sorted(stores.items()):
        if key not in ctx.priced:
            unpriced.append({"key": key, "store": store, "reason": "no_base_quantity"})
            continue
        offer = ctx.offer(key, store)
        if offer is None:
            unpriced.append({"key": key, "store": store, "reason": "no_offer"})
            continue
        assignment[key] = offer
    priced = bscore.price_assignment(assignment, ctx.priced, ctx.deliveries, orders_per_month)
    return {**priced, "unpriced": unpriced}


def _item_prices(ctx: _RunContext, by_key: dict[str, int]) -> dict[str, dict[str, float]]:
    """Item id → {store: pro-rata monthly € for that item} at every store that prices it.

    Goods only (no delivery), so it doesn't depend on the ordering frequency.
    """
    out: dict[str, dict[str, float]] = {}
    for key, row in by_key.items():
        item = ctx.priced.get(key)
        if item is None:
            continue
        stores = {item["store"], *ctx.offers.get(key, {}), *ctx.override_stores(key)}
        prices = {s: round(bscore.cost(item, offer), 2) for s in sorted(stores)
                  if (offer := ctx.offer(key, s)) is not None}
        if prices:
            out[str(row)] = prices
    return out


def simulate(df: pd.DataFrame, picks: Mapping[Union[int, str], str], orders_per_month: float,
             run_dir: Optional[Path] = None) -> dict[str, Any]:
    """Monthly cost of ``picks`` vs today's stores, both at ``orders_per_month``.

    ``picks`` maps an item id or a benchmark key to a store; items not in it
    stay at the store in the list now (the row's ``super``). "Today" is
    normally the benchmark's status quo — each basket item at the store it
    was bought from when the run was taken — so it stays fixed after a
    switch is applied to ``super``. A baseline (:func:`set_baseline`, #183)
    freezes the list's current stores as "today" instead, for as long as it
    is still tied to the latest run — an older one is ignored (``baseline``
    is still reported, with ``stale`` True) and "today" falls back to the
    benchmark's status quo. The universe is the latest run's basket. Returns
    ``{"picks", "today"}`` — each ``goods``,
    ``delivery``, ``total``, ``delivery_optimised``, ``total_optimised``,
    ``per_store`` and ``items`` from :func:`benchmark.score.price_assignment`
    plus ``unpriced`` (``{key, store, reason, id, comida}`` for items with no
    price at their store; excluded from the totals, never counted as 0) — and
    ``delta`` (picks − today). ``comparable`` is False when the two sides
    leave out different items, so ``delta`` is not like-for-like.
    ``not_in_benchmark`` lists inventory rows the run has no item for,
    ``item_prices`` maps item id → {store: monthly € for that item}, and
    ``baseline`` is ``{"set_at", "run_date", "stale"}`` or None.

    Basket items whose list row has target (``cantidad``) 0 are not bought,
    so they are left out of **both** sides — a store kept only by them would
    otherwise add a delivery fee nobody pays — and listed in ``excluded``
    (``{id, comida, key, store}``). ``item_prices`` still prices them.
    """
    if orders_per_month <= 0:
        raise StoreLinkError("orders_per_month must be > 0")
    ctx = _RunContext(_require_run(run_dir))
    baseline_doc = load_baseline(ctx.run_dir.parent)
    baseline = _baseline_field(baseline_doc, ctx.run_date)
    if baseline and baseline["stale"]:
        logger.info("ℹ️ Baseline from run %s ignored: the latest run is %s", baseline["run_date"], ctx.run_date)
    by_key = rows_by_key(df)
    key_by_row = {row: key for key, row in by_key.items()}
    excluded = [
        {"id": row, "comida": cell_text(df.at[row, COLUMNS["comida"]]), "key": key, "store": _row_store(df, row)}
        for key, row in sorted(by_key.items(), key=lambda kv: kv[1])
        if key in ctx.all_items and not (_num(df.at[row, COLUMNS["cantidad"]]) or 0) > 0
    ]
    skip = {e["key"] for e in excluded}
    if baseline_doc is not None and not baseline["stale"]:
        base_items = baseline_doc.get("items") or {}
        today = {key: (base_items.get(key) or {}).get("store") or item["store"]
                 for key, item in ctx.all_items.items() if key not in skip}
    else:
        today = {key: item["store"] for key, item in ctx.all_items.items() if key not in skip}
    chosen = {key: store for key, store in _current_stores(df, ctx, by_key).items() if key not in skip}
    for ref, store in picks.items():
        row = _resolve_row(df, ref, by_key)
        key = key_by_row.get(row) or item_key(cell_text(df.at[row, COLUMNS["comida"]]))
        if key in chosen:
            chosen[key] = _norm_store(store)

    picked, now = _price(ctx, chosen, orders_per_month), _price(ctx, today, orders_per_month)
    for side in (picked, now):
        for entry in side["unpriced"]:
            row = by_key.get(entry["key"])
            entry["id"] = row
            entry["comida"] = cell_text(df.at[row, COLUMNS["comida"]]) if row is not None else entry["key"]
    not_in_benchmark = [
        {"id": int(idx), "comida": cell_text(df.at[idx, COLUMNS["comida"]]),
         "key": item_key(cell_text(df.at[idx, COLUMNS["comida"]])), "store": _row_store(df, int(idx))}
        for idx in df.index
        if item_key(cell_text(df.at[idx, COLUMNS["comida"]])) not in ctx.all_items
    ]
    result = {
        "run_date": ctx.run_date,
        "orders_per_month": round(orders_per_month, 4),
        "picks": picked,
        "today": now,
        "delta": {f: round(picked[f] - now[f], 2) for f in ("goods", "delivery", "total",
                                                            "delivery_optimised", "total_optimised")},
        "comparable": {u["key"] for u in picked["unpriced"]} == {u["key"] for u in now["unpriced"]},
        "not_in_benchmark": not_in_benchmark,
        "excluded": excluded,
        "item_prices": _item_prices(ctx, by_key),
        "baseline": baseline,
    }
    logger.info("ℹ️ Simulated %d item(s) at %.2f orders/month (%d with target 0 left out): "
                "picks %.2f vs today %.2f (fee-optimised)", len(chosen), orders_per_month,
                len(excluded), picked["total_optimised"], now["total_optimised"])
    return result


def recommended_picks(run_dir: Optional[Path] = None) -> dict[str, Any]:
    """The latest run's recommended plan: ``{"run_date", "stores", "picks": {key: store}, totals}``."""
    run = _require_run(run_dir)
    scenarios = json.loads((run / "scenarios.json").read_text(encoding="utf-8"))
    rec = scenarios.get("recommended")
    if not rec:
        raise NoBenchmarkRunError(f"Run {run.name} has no recommended plan")
    return {
        "run_date": run.name,
        "stores": rec["stores"],
        "picks": dict(rec["items"]),
        **{f: rec.get(f) for f in ("goods", "delivery", "total", "delivery_optimised", "total_optimised")},
    }


# ─────────────────────────────────────────────────────────────────────────────
# Apply (with pack-size conversion)
# ─────────────────────────────────────────────────────────────────────────────


def _benchmark_pack(ctx: _RunContext, mappings: dict[str, dict[str, dict]], key: str, store: str,
                    url: str) -> Optional[dict[str, Any]]:
    item = ctx.all_items.get(key)
    if item and store == item["store"]:
        cur = item.get("current") or {}
        if cur.get("pack_size"):
            return {"size": float(cur["pack_size"]), "unit": cur.get("unit") or ""}
        return None
    cand = _candidate(mappings.get(store, {}).get(key), url)
    if cand and cand.get("pack_size"):
        return {"size": float(cand["pack_size"]), "unit": cand.get("unit") or ""}
    return None


def _pack(ctx: _RunContext, mappings: dict[str, dict[str, dict]], key: str, store: str,
          url: str) -> Optional[dict[str, Any]]:
    """``{"size", "unit"}`` of the product ``url`` points to at ``store``, if known.

    Today's basket store uses the basket's current product; any other store
    looks the URL up among the mapping's candidates (main + alternatives).
    The household's override for that store, when it gives a pack size or
    unit, wins (a missing unit falls back to the benchmark's, then the
    basket item's).
    """
    base = _benchmark_pack(ctx, mappings, key, store, url)
    ov = ctx.override(key, store)
    if ov is None:
        return base
    size = _num(ov.get("pack_size")) or (base or {}).get("size")
    item_unit = ((ctx.all_items.get(key) or {}).get("current") or {}).get("unit") or ""
    unit = cell_text(ov.get("unit")) or (base or {}).get("unit") or item_unit
    return {"size": float(size), "unit": unit} if size and size > 0 else None


def _product_name(ctx: _RunContext, mappings: dict[str, dict[str, dict]], key: str, store: str,
                  url: str) -> Optional[str]:
    """Display name of the product ``url`` points to at ``store`` (your override's name wins), if known.

    Same lookup as :func:`_pack`: the basket's current product at its own
    store, else the mapping candidate whose URL is ``url``.
    """
    ov = ctx.override(key, store)
    if ov and cell_text(ov.get("name")):
        return cell_text(ov.get("name"))
    item = ctx.all_items.get(key)
    if item and store == item["store"]:
        return cell_text((item.get("current") or {}).get("name")) or None
    return cell_text((_candidate(mappings.get(store, {}).get(key), url) or {}).get("name")) or None


def _round_half_up(value: float) -> int:
    """Nearest whole number, halves up (the epsilon absorbs float noise like 1.4999999)."""
    return math.floor(value + 0.5 + 1e-9)


def apply_preview(df: pd.DataFrame, picks: Mapping[Union[int, str], str],
                  run_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """What applying ``picks`` would change, one entry per row whose store changes.

    ``cantidad`` is the suggested target: ``ceil(old × old_pack / new_pack)``
    when both packs are known in the same unit, else the old target with a
    flag — ``unit_mismatch``, ``pack_unknown`` or ``no_url`` (the row has no
    URL for the new store, so it can't be applied yet). ``tenemos`` is the
    stock converted by the same factor, rounded to the nearest pack (#160),
    next to ``tenemos_from``; unconverted when the target isn't. Packs use
    the household's overrides where set. ``old_name`` / ``new_name`` are the
    products the old and new links point to, for display (None when unknown).
    """
    run = latest_run_dir() if run_dir is None else run_dir
    ctx = _RunContext(run) if run else None
    mappings = load_mappings(run.parent if run else None)
    by_key = rows_by_key(df)
    out = []
    for ref, raw_store in picks.items():
        row = _resolve_row(df, ref, by_key)
        to, frm = _norm_store(raw_store), _row_store(df, row)
        if to == frm:
            continue
        comida = cell_text(df.at[row, COLUMNS["comida"]])
        key = item_key(comida)
        new_url = store_url(df, row, to)
        old_url = cell_text(df.at[row, COLUMNS["buscador"]])
        old_pack = _pack(ctx, mappings, key, frm, old_url) if ctx else None
        new_pack = _pack(ctx, mappings, key, to, new_url) if ctx else None
        old_qty = int(df.at[row, COLUMNS["cantidad"]])
        old_stock = int(df.at[row, COLUMNS["tenemos"]])
        qty, stock, flags = old_qty, old_stock, []
        if not new_url:
            flags.append("no_url")
        if old_pack is None or new_pack is None:
            flags.append("pack_unknown")
        elif old_pack["unit"] != new_pack["unit"]:
            flags.append("unit_mismatch")
        else:
            factor = old_pack["size"] / new_pack["size"]
            # The epsilon keeps 4 × 0.25 / 1.0 from rounding up to 2 on float noise.
            qty = math.ceil(old_qty * factor - 1e-9)
            stock = _round_half_up(old_stock * factor)
        out.append({
            "id": row, "comida": comida, "key": key, "from": frm, "to": to,
            "old_pack": old_pack, "new_pack": new_pack,
            "old_name": _product_name(ctx, mappings, key, frm, old_url) if ctx else None,
            "new_name": _product_name(ctx, mappings, key, to, new_url) if ctx else None,
            "old_cantidad": old_qty, "cantidad": max(0, qty),
            "tenemos_from": old_stock, "tenemos": max(0, stock),
            "url": new_url or None, "flags": flags,
        })
    return out


def apply_changes(df: pd.DataFrame, changes: list[Mapping[str, Any]], *,
                  xlsx_path: Optional[str] = None) -> pd.DataFrame:
    """Apply ``[{row, store, cantidad, tenemos?}]``: ``super``, ``buscador`` (from ``url_<store>``),
    ``cantidad`` and — when the change carries it — the converted stock ``tenemos`` (#160).

    Every change is validated before anything is written, and the whole batch
    is saved once — or rolled back together when the save fails.
    """
    plan = []
    for change in changes:
        row = int(change["row"])
        _check_row(df, row)
        store = _norm_store(str(change["store"]))
        url = store_url(df, row, store)
        if not url:
            raise StoreLinkError(f"item {row} has no {store} URL — set one before applying")
        qty = int(change["cantidad"])
        if qty < 0:
            raise StoreLinkError(f"item {row}: cantidad must be >= 0")
        stock = change.get("tenemos")
        if stock is not None:
            stock = int(stock)
            if stock < 0:
                raise StoreLinkError(f"item {row}: tenemos must be >= 0")
        plan.append((row, store, url, qty, stock))
    edits = _Edits(df)
    for row, store, url, qty, stock in plan:
        edits.set(row, COLUMNS["super"], store)
        edits.set(row, COLUMNS["buscador"], url)
        edits.set(row, COLUMNS["cantidad"], qty)
        if stock is not None:
            edits.set(row, COLUMNS["tenemos"], stock)
        edits.set(row, COLUMNS["comprar"], max(0, qty - int(df.at[row, COLUMNS["tenemos"]])))
    edits.commit(xlsx_path)
    with_stock = sum(1 for *_, stock in plan if stock is not None)
    logger.info("✅ Applied %d store change(s)", len(plan))
    if with_stock:
        logger.info("ℹ️ %d applied change(s) also set the converted stock (tenemos)", with_stock)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Per-item review: detail, checks, overrides, checked (#165)
# ─────────────────────────────────────────────────────────────────────────────

_SNAPSHOT_FIELDS = ("name", "pack_size", "unit", "pack_price")
_META_FIELDS = ("confidence", "quality_vs_current", "evidence", "notes")


def _unit_price(size: Optional[float], price: Optional[float]) -> Optional[float]:
    return round(price / size, 2) if size and price is not None else None


def _benchmark_view(ctx: _RunContext, key: str, store: str) -> Optional[dict[str, Any]]:
    """What the run says about ``key`` at ``store``: product values plus research metadata.

    The priced offer when there is one (``priced`` True); else, for display,
    the basket product at its own store, the cheapest matched-but-unverified
    candidate (status ``unverified``), or a bare record (e.g. ``not_found``,
    no product values). None when the run has nothing for that store.
    """
    item = ctx.all_items.get(key)
    if item is None:
        return None
    rec = ctx.record(key, store)
    cur = item.get("current") or {}
    offer = ctx.benchmark_offer(key, store)
    if offer is not None:
        cand = _candidate(rec, offer.url) or {}
        unit = cur.get("unit") if offer.status == "baseline" else cand.get("unit")
        values = {"name": offer.name, "pack_size": offer.pack_size, "unit": unit,
                  "pack_price": offer.pack_price, "url": offer.url}
        status, priced, meta = offer.status, True, cand
    elif store == item["store"]:
        values = {"name": cur.get("name"), "pack_size": _num(cur.get("pack_size")), "unit": cur.get("unit"),
                  "pack_price": _num(cur.get("pack_price")), "url": cur.get("url")}
        status, priced, meta = "baseline", False, {}
    elif best := _best_candidate(rec):
        values = {"name": best.get("name"), "pack_size": float(best["pack_size"]), "unit": best.get("unit"),
                  "pack_price": float(best["pack_price"]), "url": best.get("url")}
        status = "unverified" if bscore.is_unverified(item, best) else best.get("status")
        priced, meta = False, best
    elif rec is not None:
        values = dict.fromkeys((*_SNAPSHOT_FIELDS, "url"))
        status, priced, meta = rec.get("status"), False, rec
    else:
        return None
    return {**values, "status": status, "priced": priced, **{f: meta.get(f) for f in _META_FIELDS}}


def _snapshot_changed(ov: dict, now: Optional[dict[str, Any]]) -> bool:
    """Whether the benchmark's values moved since ``ov`` was saved (False when it has no snapshot)."""
    if "benchmark_snapshot" not in ov:
        return False
    snap = ov.get("benchmark_snapshot")
    if not isinstance(snap, dict) or now is None:
        return isinstance(snap, dict) != (now is not None)
    for field in _SNAPSHOT_FIELDS:
        a, b = snap.get(field), now.get(field)
        if field in ("pack_size", "pack_price"):
            a, b = _num(a), _num(b)
            if (a is None) != (b is None) or (a is not None and abs(a - b) > 1e-9):
                return True
        elif (a or None) != (b or None):
            return True
    return False


class _Review:
    """Everything an item review reads, loaded once per call."""

    def __init__(self, run_dir: Optional[Path]) -> None:
        run = latest_run_dir() if run_dir is None else run_dir
        self.ctx = _RunContext(run) if run else None
        self.mappings = load_mappings(run.parent) if run else {}
        self.checked = self.ctx.checked if self.ctx else load_overrides()["checked"]
        self.registry = load_registry()


def _store_entry(rv: _Review, key: str, item: Optional[dict], store: str, url: str,
                 list_store: str) -> Optional[dict[str, Any]]:
    """One store's row in the item detail, or None when nothing is known about it."""
    ctx = rv.ctx
    bench = _benchmark_view(ctx, key, store) if ctx and item else None
    ov = ctx.override(key, store) if ctx and item else None
    if bench is None and ov is None and not url and store not in (list_store, (item or {}).get("store")):
        return None
    bench_values = {f: bench[f] for f in _SNAPSHOT_FIELDS} if bench and bench["pack_size"] else None
    eff = ctx.offer(key, store) if ctx and item else None
    bv, ov_ = bench_values or {}, ov or {}
    item_unit = ((item or {}).get("current") or {}).get("unit")
    size = _num(ov_.get("pack_size")) or bv.get("pack_size")
    price = _num(ov_.get("pack_price"))
    if price is None:
        price = bv.get("pack_price")
    return {
        "store": store,
        "store_name": (rv.registry.get(store) or {}).get("name", store),
        "is_list_store": store == list_store,
        "is_basket_store": bool(item) and store == item["store"],
        "url": url,
        "url_kind": link_kind(store, url) if url else None,
        "benchmark_url": (bench or {}).get("url") or None,
        "name": cell_text(ov_.get("name")) or bv.get("name"),
        "pack_size": size,
        "unit": cell_text(ov_.get("unit")) or bv.get("unit") or (item_unit if ov else None),
        "pack_price": price,
        "price_per_unit": _unit_price(size, price),
        "monthly_cost": round(bscore.cost(item, eff), 2) if eff and key in ctx.priced else None,
        "status": eff.status if eff else (bench or {}).get("status"),
        "priced": eff is not None,
        **{f: (bench or {}).get(f) for f in _META_FIELDS},
        "source": "override" if ov else ("benchmark" if bench_values else "link-only"),
        "benchmark": bench_values,
        "override": {f: ov.get(f) for f in (*_SNAPSHOT_FIELDS, "note", "updated")} if ov else None,
        "benchmark_changed": _snapshot_changed(ov, bench_values) if ov else False,
    }


def _quantity(item: Optional[dict], now_pack: Optional[dict[str, Any]], pack_source: Optional[str],
              list_store: str, cantidad: int, tenemos: int) -> dict[str, Any]:
    """Before (basket store, run-time target) → now (list store, ``cantidad``) in real units."""
    cur = (item or {}).get("current") or {}
    b_size, b_unit = _num(cur.get("pack_size")), cur.get("unit")
    target = (item or {}).get("target")
    before = None
    if item:
        before = {"store": item["store"], "packs": target, "pack_size": b_size, "unit": b_unit,
                  "total": round(target * b_size, 3) if target is not None and b_size else None}
    n_size, n_unit = (now_pack["size"], now_pack["unit"]) if now_pack else (None, None)
    now = {"store": list_store, "packs": cantidad, "pack_size": n_size, "unit": n_unit,
           "total": round(cantidad * n_size, 3) if n_size else None,
           "pack_source": pack_source if now_pack else None}
    same_unit = bool(b_size and n_size and b_unit == n_unit)
    factor = b_size / n_size if same_unit else None
    return {
        "before": before, "now": now,
        "pack_ratio": round(1 / factor, 4) if factor else None,
        "same_unit": same_unit,
        "unit_mismatch": bool(b_size and n_size and b_unit != n_unit),
        "delta_pct": round(100 * (now["total"] / before["total"] - 1), 1)
        if same_unit and before and before["total"] else None,
        "suggested": {"cantidad": max(0, math.ceil(target * factor - 1e-9)),
                      "tenemos": max(0, _round_half_up(tenemos * factor))}
        if factor and target is not None else None,
    }


def _detail(df: pd.DataFrame, row: int, rv: _Review) -> dict[str, Any]:
    ctx = rv.ctx
    comida = cell_text(df.at[row, COLUMNS["comida"]])
    key = item_key(comida)
    item = ctx.all_items.get(key) if ctx and key else None
    list_store = _row_store(df, row)
    buscador = cell_text(df.at[row, COLUMNS["buscador"]])
    list_url = buscador if _is_url(buscador) else ""
    urls = {s: u for s, col in store_url_columns(df).items() if (u := cell_text(df.at[row, col]))}
    cantidad = int(df.at[row, COLUMNS["cantidad"]])
    tenemos = int(df.at[row, COLUMNS["tenemos"]])
    checked = (cell_text(rv.checked.get(key)) or None) if key else None

    names = set(urls) | ({list_store} if list_store else set())
    if item:
        names.add(item["store"])
        names.update(s for s in ctx.stores if bscore.record_candidates(ctx.record(key, s)))
        names.update(ctx.override_stores(key))
    stores = [entry for s in sorted(names)
              if (entry := _store_entry(rv, key, item, s, urls.get(s) or (list_url if s == list_store else ""),
                                        list_store))]

    # The list store's pack: the product its link points to (or your override),
    # else that store's priced offer.
    now_pack = _pack(ctx, rv.mappings, key, list_store, list_url) if item and list_store else None
    pack_source = "link"
    list_entry = next((s for s in stores if s["is_list_store"]), None)
    if now_pack is None and list_entry and list_entry["pack_size"]:
        now_pack, pack_source = {"size": list_entry["pack_size"], "unit": list_entry["unit"] or ""}, "offer"
    quantity = _quantity(item, now_pack, pack_source, list_store, cantidad, tenemos)

    before = monthly = None
    if item:
        cur = item.get("current") or {}
        size, price = _num(cur.get("pack_size")), _num(cur.get("pack_price"))
        before = {"store": item["store"], "name": cur.get("name"), "url": cur.get("url"),
                  "pack_size": size, "unit": cur.get("unit"), "pack_price": price,
                  "unit_price": _num(cur.get("unit_price")) or _unit_price(size, price),
                  "target": item.get("target"), "monthly_packs": item.get("monthly_packs")}
        qty = _num(item.get("monthly_base_qty")) or bscore.monthly_base(item)
        n_size = quantity["now"]["pack_size"]
        monthly = {"qty": qty, "unit": cur.get("unit"),
                   "packs_at_list_store": round(qty / n_size, 2) if qty and quantity["same_unit"] else None}

    flags: set[str] = set()
    ratio = quantity["pack_ratio"]
    moved = bool(item and list_store and list_store != item["store"])
    if moved:
        flags.add("moved")
    if ratio is not None and (ratio >= 2 - 1e-9 or ratio <= 0.5 + 1e-9):
        flags.add("pack_x2")
    if quantity["unit_mismatch"]:
        flags.add("unit_mismatch")
    if list_url and (kind := link_kind(list_store, list_url)) != "product":
        flags.add(f"{kind}_link")
    if any(s["override"] for s in stores):
        flags.add("override")
    if any(s["benchmark_changed"] for s in stores):
        flags.add("benchmark_changed")
    # Nothing records whether stock was already recounted in the new packs, so
    # this is a heuristic: moved, packs differ, stock on hand, not yet checked.
    if moved and ratio is not None and abs(ratio - 1) > 1e-9 and tenemos > 0 and not checked:
        flags.add("stock_unconverted")

    return {
        "id": row, "comida": comida, "key": key or None, "in_basket": item is not None,
        "run_date": ctx.run_date if ctx else None,
        "list": {
            "store": list_store, "url": buscador, "url_kind": link_kind(list_store, list_url) if list_url else None,
            "cantidad": cantidad, "tenemos": tenemos, "comprar": int(df.at[row, COLUMNS["comprar"]]),
            "unidades": _num(df.at[row, UNIDADES_COLUMN]) if UNIDADES_COLUMN in df.columns else None,
            "urls": urls, "url_kinds": {s: link_kind(s, u) for s, u in urls.items()},
        },
        "before": before,
        "monthly": monthly,
        "spec": item.get("spec") if item else None,
        "tier": item.get("tier") if item else None,
        "stores": stores,
        "quantity": quantity,
        "flags": [f for f in CHECK_FLAGS if f in flags],
        "checked": checked,
    }


def item_detail(df: pd.DataFrame, item_id: int, run_dir: Optional[Path] = None) -> dict[str, Any]:
    """Everything the app believes about one item, per store, for the review dialog.

    ``list`` (the row as it is now), ``before`` (the basket product at the
    benchmark's store and the run-time ``target``), ``monthly`` use,
    ``spec``/``tier``, ``stores`` (one per store with a benchmark record, an
    override or a URL: effective values — your override merged over the
    benchmark — with the benchmark's own values beside them), the
    before → now ``quantity`` maths in real units, review ``flags`` (see
    :func:`checks`) and the ``checked`` date. Works without a benchmark run
    (links only).
    """
    _check_row(df, item_id)
    return _detail(df, item_id, _Review(run_dir))


def checks(df: pd.DataFrame, run_dir: Optional[Path] = None) -> dict[str, Any]:
    """Review flags for every row: ``{"run_date", "checks", "counts", "checked"}``.

    ``checks`` maps item id → flags (only items with at least one), from
    ``CHECK_FLAGS``: ``moved`` (list store ≠ basket store), ``pack_x2`` (the
    list store's pack is ≥ 2× or ≤ ½ the basket's, same unit),
    ``unit_mismatch``, ``search_link`` / ``suspect_link`` (the list store's
    URL), ``override``, ``benchmark_changed`` (a newer run moved the values
    an override was made against) and ``stock_unconverted`` — a heuristic,
    since nothing records whether stock was recounted: moved, packs differ,
    ``tenemos`` > 0 and not yet checked. Benchmark-based flags need the item
    in the run's basket; link flags apply to any row. ``counts`` has one
    count per flag plus ``flagged``, ``needs_checking`` (flagged, not
    checked) and ``checked``; ``checked`` maps item id → date checked.
    """
    rv = _Review(run_dir)
    flagged: dict[str, list[str]] = {}
    checked: dict[str, str] = {}
    counts = dict.fromkeys(CHECK_FLAGS, 0)
    for idx in df.index:
        detail = _detail(df, int(idx), rv)
        if detail["checked"]:
            checked[str(idx)] = detail["checked"]
        if detail["flags"]:
            flagged[str(idx)] = detail["flags"]
            for flag in detail["flags"]:
                counts[flag] += 1
    counts.update(flagged=len(flagged), needs_checking=sum(1 for i in flagged if i not in checked),
                  checked=len(checked))
    return {"run_date": rv.ctx.run_date if rv.ctx else None, "checks": flagged, "counts": counts,
            "checked": checked}


def _basket_key(df: pd.DataFrame, item_id: int, ctx: _RunContext) -> str:
    comida = cell_text(df.at[item_id, COLUMNS["comida"]])
    key = item_key(comida)
    if key not in ctx.all_items:
        raise StoreLinkError(f"item {item_id} ({comida!r}) is not in the {ctx.run_date} benchmark basket — "
                             "overrides are kept per basket item")
    return key


def set_override(df: pd.DataFrame, item_id: int, store: str, *, name: Optional[str] = None,
                 pack_size: Optional[float] = None, unit: Optional[str] = None,
                 pack_price: Optional[float] = None, note: Optional[str] = None,
                 run_dir: Optional[Path] = None) -> dict[str, Any]:
    """Save your own product / pack / price for one basket item at one store; returns the item detail.

    The given fields replace any earlier override for that store; fields left
    out fall back to the benchmark. The benchmark's values at this moment are
    kept as ``benchmark_snapshot``.
    """
    _check_row(df, item_id)
    store = _norm_store(store)
    if store not in load_registry():
        raise StoreLinkError(f"unknown store {store!r} (not in benchmark/stores.json)")
    name, note = (name or "").strip() or None, (note or "").strip() or None
    unit = (unit or "").strip().lower() or None
    if pack_size is not None and not (math.isfinite(pack_size) and pack_size > 0):
        raise StoreLinkError("pack_size must be > 0")
    if pack_price is not None and not (math.isfinite(pack_price) and pack_price >= 0):
        raise StoreLinkError("pack_price must be >= 0")
    if unit is not None and unit not in OVERRIDE_UNITS:
        raise StoreLinkError(f"unit must be one of {', '.join(OVERRIDE_UNITS)}")
    if pack_size is None and pack_price is None and name is None:
        raise StoreLinkError("give at least a pack size, a pack price or a product name")
    run = _require_run(run_dir)
    ctx = _RunContext(run)
    key = _basket_key(df, item_id, ctx)
    bench = _benchmark_view(ctx, key, store)
    base = ctx.override_base(key, store)
    if pack_size is None and base is None:
        raise StoreLinkError(f"give the pack size: the benchmark has no {store} product to take it from")
    if pack_price is None and base is None:
        raise StoreLinkError(f"give the pack price: the benchmark has no {store} price to take it from")
    entry = {
        "name": name, "pack_size": pack_size, "unit": unit, "pack_price": pack_price, "note": note,
        "updated": datetime.now().isoformat(timespec="seconds"),
        "benchmark_snapshot": {f: bench[f] for f in _SNAPSHOT_FIELDS} if bench and bench["pack_size"] else None,
    }

    def change(doc: dict[str, dict]) -> None:
        per_store = doc["items"].get(key)
        doc["items"][key] = {**(per_store if isinstance(per_store, dict) else {}), store: entry}

    _update_overrides(run.parent, change)
    logger.info("ℹ️ Override saved: %s at %s (%s)", key, store,
                ", ".join(f"{f}={entry[f]}" for f in _SNAPSHOT_FIELDS if entry[f] is not None))
    return item_detail(df, item_id, run)


def reset_override(df: pd.DataFrame, item_id: int, store: str,
                   run_dir: Optional[Path] = None) -> dict[str, Any]:
    """Drop your override for one item at one store (back to the benchmark); returns the item detail."""
    _check_row(df, item_id)
    store = _norm_store(store)
    run = _require_run(run_dir)
    key = _basket_key(df, item_id, _RunContext(run))
    removed = False

    def change(doc: dict[str, dict]) -> None:
        nonlocal removed
        per_store = doc["items"].get(key)
        if isinstance(per_store, dict) and store in per_store:
            del per_store[store]
            removed = True
            if not per_store:
                del doc["items"][key]

    _update_overrides(run.parent, change)
    logger.info("ℹ️ Override %s: %s at %s", "reset to benchmark" if removed else "not set, nothing to reset",
                key, store)
    return item_detail(df, item_id, run)


def set_item_checked(df: pd.DataFrame, item_id: int, checked: bool,
                     base: Optional[Path] = None) -> dict[str, Any]:
    """Mark one item reviewed (today's date) or not; returns ``{"key", "checked"}``."""
    _check_row(df, item_id)
    key = item_key(cell_text(df.at[item_id, COLUMNS["comida"]]))
    if not key:
        raise StoreLinkError(f"item {item_id} has no name to key its review state by")
    stamp = date.today().isoformat() if checked else None

    def change(doc: dict[str, dict]) -> None:
        if stamp:
            doc["checked"][key] = stamp
        else:
            doc["checked"].pop(key, None)

    _update_overrides(base, change)
    logger.info("ℹ️ Item %d (%s) %s", item_id, key, f"checked on {stamp}" if stamp else "unchecked")
    return {"key": key, "checked": stamp}


def run_status(base: Optional[Path] = None) -> dict[str, Any]:
    """The latest run's age and review due date, the stores it covers, the override count and the baseline (#183)."""
    items = load_overrides(base)["items"]
    n_overrides = sum(len(v) for v in items.values() if isinstance(v, dict))
    run = latest_run_dir(base)
    baseline = _baseline_field(load_baseline(base), run.name if run else None)
    if run is None:
        return {"run_date": None, "age_days": None, "next_due": None, "stores_covered": [],
                "overrides": n_overrides, "baseline": baseline}
    run_day = date.fromisoformat(run.name)
    return {
        "run_date": run.name,
        "age_days": (date.today() - run_day).days,
        "next_due": (run_day + timedelta(days=REVIEW_INTERVAL_DAYS)).isoformat(),
        "stores_covered": sorted(p.stem for p in (run / "stores").glob("*.json")),
        "overrides": n_overrides,
        "baseline": baseline,
    }
