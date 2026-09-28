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
"""

from __future__ import annotations

import json
import logging
import math
import re
from pathlib import Path
from typing import Any, Mapping, Optional, Union

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
)

logger = logging.getLogger(__name__)

STORES_REGISTRY_PATH = REPO_ROOT / "benchmark" / "stores.json"
DEFAULT_RUNS_DIR = REPO_ROOT / "benchmark_runs"
_RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# What a real product-page URL looks like, per store. A URL that doesn't
# match (e.g. a Carrefour slug without its `/R-<id>/p` product id, which
# redirects to the home page) is still imported, but counted and logged so a
# bad benchmark run is visible.
_PRODUCT_URL_PATTERNS: dict[str, re.Pattern[str]] = {
    "carrefour": re.compile(r"/R-[A-Za-z0-9-]+/p"),
}

# Ordering-frequency presets, in orders per month per store.
FREQUENCIES: dict[str, float] = {
    "weekly": 52 / 12,
    "2-weekly": 26 / 12,
    "monthly": 1.0,
}
DEFAULT_FREQUENCY = "weekly"


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


class _RunContext:
    """One scored run, loaded once per call: basket items, offers, delivery terms."""

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

    def offer(self, key: str, store: str) -> Optional[bscore.Offer]:
        """What ``key`` costs at ``store``: today's product at its basket store, else the best offer."""
        item = self.priced.get(key)
        if item is None:
            return None
        if store == item["store"]:
            return bscore.baseline_offer(item)
        return self.offers.get(key, {}).get(store)


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
    page (see ``_PRODUCT_URL_PATTERNS``; imported anyway, logged ⚠️).
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
        pattern = _PRODUCT_URL_PATTERNS.get(store)
        for key, entry in mapping.items():
            url = _mapping_url(key, store, entry, ctx)
            if not _is_url(url):
                continue
            if pattern is not None and not pattern.search(url):
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
                       n, store, _PRODUCT_URL_PATTERNS[store].pattern)
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


def _today_stores(df: pd.DataFrame, ctx: _RunContext, by_key: dict[str, int]) -> dict[str, str]:
    """Benchmark key → today's store: the row's ``super``, else the basket's store."""
    out = {}
    for key, item in ctx.all_items.items():
        row = by_key.get(key)
        out[key] = (_row_store(df, row) if row is not None else "") or item["store"]
    return out


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
        stores = {item["store"], *ctx.offers.get(key, {})}
        prices = {s: round(bscore.cost(item, offer), 2) for s in sorted(stores)
                  if (offer := ctx.offer(key, s)) is not None}
        if prices:
            out[str(row)] = prices
    return out


def simulate(df: pd.DataFrame, picks: Mapping[Union[int, str], str], orders_per_month: float,
             run_dir: Optional[Path] = None) -> dict[str, Any]:
    """Monthly cost of ``picks`` vs today's stores, both at ``orders_per_month``.

    ``picks`` maps an item id or a benchmark key to a store; items not in it
    stay at today's store (the row's ``super``). The universe is the latest
    run's basket. Returns ``{"picks", "today"}`` — each ``goods``,
    ``delivery``, ``total``, ``delivery_optimised``, ``total_optimised``,
    ``per_store`` and ``items`` from :func:`benchmark.score.price_assignment`
    plus ``unpriced`` (``{key, store, reason, id, comida}`` for items with no
    price at their store; excluded from the totals, never counted as 0) — and
    ``delta`` (picks − today). ``comparable`` is False when the two sides
    leave out different items, so ``delta`` is not like-for-like.
    ``not_in_benchmark`` lists inventory rows the run has no item for, and
    ``item_prices`` maps item id → {store: monthly € for that item}.
    """
    if orders_per_month <= 0:
        raise StoreLinkError("orders_per_month must be > 0")
    ctx = _RunContext(_require_run(run_dir))
    by_key = rows_by_key(df)
    key_by_row = {row: key for key, row in by_key.items()}
    today = _today_stores(df, ctx, by_key)
    chosen = dict(today)
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
        "item_prices": _item_prices(ctx, by_key),
    }
    logger.info("ℹ️ Simulated %d item(s) at %.2f orders/month: picks %.2f vs today %.2f (fee-optimised)",
                len(chosen), orders_per_month, picked["total_optimised"], now["total_optimised"])
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


def _pack(ctx: _RunContext, mappings: dict[str, dict[str, dict]], key: str, store: str,
          url: str) -> Optional[dict[str, Any]]:
    """``{"size", "unit"}`` of the product ``url`` points to at ``store``, if known.

    Today's basket store uses the basket's current product; any other store
    looks the URL up among the mapping's candidates (main + alternatives).
    """
    item = ctx.all_items.get(key)
    if item and store == item["store"]:
        cur = item.get("current") or {}
        if cur.get("pack_size"):
            return {"size": float(cur["pack_size"]), "unit": cur.get("unit") or ""}
        return None
    entry = mappings.get(store, {}).get(key)
    if not entry:
        return None
    for cand in [entry] + list(entry.get("alternatives") or []):
        if _same_url(url, cand.get("url", "")) and cand.get("pack_size"):
            return {"size": float(cand["pack_size"]), "unit": cand.get("unit") or ""}
    return None


def apply_preview(df: pd.DataFrame, picks: Mapping[Union[int, str], str],
                  run_dir: Optional[Path] = None) -> list[dict[str, Any]]:
    """What applying ``picks`` would change, one entry per row whose store changes.

    ``cantidad`` is the suggested target: ``ceil(old × old_pack / new_pack)``
    when both packs are known in the same unit, else the old target with a
    flag — ``unit_mismatch``, ``pack_unknown`` or ``no_url`` (the row has no
    URL for the new store, so it can't be applied yet).
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
        old_pack = _pack(ctx, mappings, key, frm, cell_text(df.at[row, COLUMNS["buscador"]])) if ctx else None
        new_pack = _pack(ctx, mappings, key, to, new_url) if ctx else None
        old_qty = int(df.at[row, COLUMNS["cantidad"]])
        qty, flags = old_qty, []
        if not new_url:
            flags.append("no_url")
        if old_pack is None or new_pack is None:
            flags.append("pack_unknown")
        elif old_pack["unit"] != new_pack["unit"]:
            flags.append("unit_mismatch")
        else:
            # The epsilon keeps 4 × 0.25 / 1.0 from rounding up to 2 on float noise.
            qty = math.ceil(old_qty * old_pack["size"] / new_pack["size"] - 1e-9)
        out.append({
            "id": row, "comida": comida, "key": key, "from": frm, "to": to,
            "old_pack": old_pack, "new_pack": new_pack,
            "old_cantidad": old_qty, "cantidad": max(0, qty), "url": new_url or None, "flags": flags,
        })
    return out


def apply_changes(df: pd.DataFrame, changes: list[Mapping[str, Any]], *,
                  xlsx_path: Optional[str] = None) -> pd.DataFrame:
    """Apply ``[{row, store, cantidad}]``: ``super``, ``buscador`` (from ``url_<store>``) and ``cantidad``.

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
        plan.append((row, store, url, qty))
    edits = _Edits(df)
    for row, store, url, qty in plan:
        edits.set(row, COLUMNS["super"], store)
        edits.set(row, COLUMNS["buscador"], url)
        edits.set(row, COLUMNS["cantidad"], qty)
        edits.set(row, COLUMNS["comprar"], max(0, qty - int(df.at[row, COLUMNS["tenemos"]])))
    edits.commit(xlsx_path)
    logger.info("✅ Applied %d store change(s)", len(plan))
    return df
