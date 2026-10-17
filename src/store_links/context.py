"""One scored run loaded for pricing, and the inventory row ↔ benchmark item join."""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd

from benchmark import score as bscore
from benchmark.build_basket import item_key
from src.data import COLUMNS, cell_text

from .registry import StoreLinkError, _same_url
from .state import load_overrides


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


def _check_row(df: pd.DataFrame, row: int) -> None:
    if row not in df.index:
        raise StoreLinkError(f"item {row} not found")
