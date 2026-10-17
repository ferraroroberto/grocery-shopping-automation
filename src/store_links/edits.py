"""Inventory writes with rollback: the URL import, single-row edits and the store pick."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from pandas.api.types import is_object_dtype, is_string_dtype

from benchmark.paths import runs_dir
from src.data import (
    COLUMNS,
    FALLBACK_SUPER_COLUMN,
    FALLBACK_URL_COLUMN,
    InventoryFileError,
    SpreadsheetLockedError,
    cell_text,
    fallback_pair,
    save_inventory_data,
    store_url_column,
)

from .context import _RunContext, _check_row, _norm_store, _row_store, rows_by_key
from .registry import (
    StoreLinkError,
    _is_url,
    _same_url,
    latest_run_dir,
    link_kind,
    load_mappings,
    load_registry,
)

logger = logging.getLogger(__name__)


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


def set_fallback(df: pd.DataFrame, row: int, store: str, url: str, *, save: bool = True,
                 xlsx_path: Optional[str] = None) -> pd.DataFrame:
    """Set one row's fallback product (#283, #284), or clear it with a blank store *and* url.

    The two cells go together. A sheet without the ``fallback_*`` columns gains
    them only when a fallback is first set; clearing there writes nothing.
    """
    _check_row(df, row)
    store, url = (store or "").strip().lower(), (url or "").strip()
    if not store and not url:
        if FALLBACK_SUPER_COLUMN in df.columns or FALLBACK_URL_COLUMN in df.columns:
            edits = _Edits(df)
            edits.set(row, FALLBACK_SUPER_COLUMN, "")
            edits.set(row, FALLBACK_URL_COLUMN, "")
            if save:
                edits.commit(xlsx_path)
        logger.info("✅ Item %d: fallback cleared", row)
        return df
    if not store or not url:
        raise StoreLinkError("a fallback needs both a store and a product link")
    if store not in load_registry():
        raise StoreLinkError(f"unknown store {store!r} (not in benchmark/stores.json)")
    if not _is_url(url):
        raise StoreLinkError("url must start with http:// or https://")
    if fallback_pair(store, url, _row_store(df, row), df.at[row, COLUMNS["buscador"]]) is None:
        raise StoreLinkError("that is the item's own buy link — pick another product or store")
    edits = _Edits(df)
    edits.set(row, FALLBACK_SUPER_COLUMN, store)
    edits.set(row, FALLBACK_URL_COLUMN, url)
    if save:
        edits.commit(xlsx_path)
    logger.info("✅ Item %d: fallback set to %s", row, store)
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
