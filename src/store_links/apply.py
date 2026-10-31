"""Applying store picks to the list: the preview with pack-size conversion, and the write."""

from __future__ import annotations

import logging
import math
from pathlib import Path
from typing import Any, Mapping, Optional, Union

import pandas as pd

from benchmark.build_basket import item_key
from src.data import COLUMNS, cell_text, comprar_for

from .context import _RunContext, _candidate, _check_row, _norm_store, _num, _resolve_row, _row_store, rows_by_key
from .edits import _Edits, store_url
from .registry import StoreLinkError, latest_run_dir, load_mappings

logger = logging.getLogger(__name__)


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
        edits.set(row, COLUMNS["comprar"], comprar_for(qty, df.at[row, COLUMNS["tenemos"]]))
    edits.commit(xlsx_path)
    with_stock = sum(1 for *_, stock in plan if stock is not None)
    logger.info("✅ Applied %d store change(s)", len(plan))
    if with_stock:
        logger.info("ℹ️ %d applied change(s) also set the converted stock (tenemos)", with_stock)
    return df
