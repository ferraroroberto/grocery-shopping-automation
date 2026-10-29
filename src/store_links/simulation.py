"""The monthly-cost simulator and the what-if baseline."""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Optional, Union

import pandas as pd

from benchmark import score as bscore
from benchmark.build_basket import item_key
from src.data import COLUMNS, cell_text

from .context import _RunContext, _norm_store, _num, _resolve_row, _row_store, rows_by_key
from .registry import NoBenchmarkRunError, StoreLinkError, _require_run
from .state import _baseline_field, _write_baseline, load_baseline

logger = logging.getLogger(__name__)


# Ordering-frequency presets, in orders per month per store.
FREQUENCIES: dict[str, float] = {
    "weekly": 52 / 12,
    "2-weekly": 26 / 12,
    "monthly": 1.0,
}
DEFAULT_FREQUENCY = "weekly"


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
    """``{"store", "name", "pack_size", "unit", "pack_price"}`` for ``ctx.offer(key, store)`` right now."""
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
