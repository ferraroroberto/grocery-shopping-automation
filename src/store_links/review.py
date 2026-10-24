"""Per-item review: detail, flags, overrides, checked state and the run status."""

from __future__ import annotations

import logging
import math
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from benchmark import score as bscore
from benchmark.build_basket import item_key
from src.data import COLUMNS, cell_text, store_url_columns

from .apply import _pack, _round_half_up
from .context import _RunContext, _best_candidate, _candidate, _check_row, _norm_store, _num, _row_store
from .registry import (
    StoreLinkError,
    _is_url,
    _require_run,
    latest_run_dir,
    link_kind,
    load_mappings,
    load_registry,
)
from .state import _baseline_field, _update_overrides, load_baseline, load_overrides

logger = logging.getLogger(__name__)


# Units an override may give a pack in (the benchmark's own units).
OVERRIDE_UNITS = ("kg", "l", "ud", "m")
# How often the benchmark should be re-run: a run's "next due" date.
REVIEW_INTERVAL_DAYS = 90
# Review flags, in display order (see :func:`checks`).
CHECK_FLAGS = ("moved", "pack_x2", "unit_mismatch", "search_link", "suspect_link",
               "override", "benchmark_changed", "stock_unconverted")
# Optional inventory column: the item's size/count per unit, shown in the detail.
UNIDADES_COLUMN = "unidades"


# ─────────────────────────────────────────────────────────────────────────────
# Per-item review: detail, checks, overrides, checked
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
