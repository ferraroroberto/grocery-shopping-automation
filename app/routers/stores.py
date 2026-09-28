"""Per-store product links, store picks and the cost simulator (issue #148).

Thin HTTP layer over :mod:`src.store_links`: its ``StoreLinkError`` becomes
400, ``NoBenchmarkRunError`` 404, and the spreadsheet lock/file errors go
through :func:`app.api_common.mutate_or_error` like every other mutator.
Mutating routes echo the full inventory payload, as the inventory routes do.
"""

import logging
from functools import lru_cache
from typing import Any, Callable, Optional

import pandas as pd
from fastapi import APIRouter
from pydantic import BaseModel, Field

from app.api_common import (
    get_row,
    inventory_error,
    inventory_payload,
    load_inventory_or_error,
    mutate_or_error,
)
from src import store_links
from src.store_links import NoBenchmarkRunError, StoreLinkError

logger = logging.getLogger(__name__)

router = APIRouter()

# Used only when the cart-automation package can't be imported here.
_FALLBACK_HANDLERS = frozenset({"mercadona", "ametller"})


class StoreUrlPayload(BaseModel):
    store: str
    url: str = ""


class PickPayload(BaseModel):
    store: str


class PicksPayload(BaseModel):
    picks: dict[str, str] = Field(default_factory=dict)


class SimulatePayload(PicksPayload):
    orders_per_month: Optional[float] = Field(default=None, gt=0)
    frequency: Optional[str] = None


class ChangePayload(BaseModel):
    id: int
    store: str
    cantidad: int = Field(..., ge=0)


class ApplyPayload(BaseModel):
    changes: list[ChangePayload]


def _call(fn: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
    """Run a store_links call, mapping its errors to HTTP (lock/file via mutate_or_error)."""
    try:
        return mutate_or_error(fn, *args, **kwargs)
    except StoreLinkError as exc:
        raise inventory_error(400, str(exc)) from exc
    except NoBenchmarkRunError as exc:
        raise inventory_error(404, str(exc)) from exc


@lru_cache(maxsize=1)
def _handler_stores() -> frozenset[str]:
    """Stores with a cart-automation handler (others are manual orders)."""
    try:
        from automation.run_automation import HANDLERS
    except ImportError as exc:
        logger.warning("⚠️ Cart handlers not importable (%s) — assuming %s", exc, sorted(_FALLBACK_HANDLERS))
        return _FALLBACK_HANDLERS
    return frozenset(HANDLERS)


def _picks_by_id(df: pd.DataFrame, picks_by_key: dict[str, str]) -> dict[str, str]:
    """Benchmark key → store, re-keyed by inventory item id (keys with no row dropped)."""
    by_key = store_links.rows_by_key(df)
    return {str(by_key[key]): store for key, store in picks_by_key.items() if key in by_key}


@router.put("/api/items/{item_id}/store-url")
def set_store_url(item_id: int, payload: StoreUrlPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    get_row(df, item_id)
    _call(store_links.set_store_url, df, item_id, payload.store, payload.url)
    return inventory_payload(load_inventory_or_error())


@router.post("/api/items/{item_id}/pick")
def pick_store(item_id: int, payload: PickPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    get_row(df, item_id)
    _call(store_links.pick_store, df, item_id, payload.store)
    return inventory_payload(load_inventory_or_error())


@router.post("/api/stores/import-latest")
def import_latest() -> dict[str, Any]:
    df = load_inventory_or_error()
    counts = _call(store_links.import_latest, df)
    return {**inventory_payload(load_inventory_or_error()), "import": counts}


@router.get("/api/stores")
def stores() -> dict[str, Any]:
    registry = store_links.load_registry()
    handlers = _handler_stores()
    run = store_links.latest_run_dir()
    return {
        "run_date": run.name if run else None,
        "stores": [
            {"key": key, "name": entry.get("name", key), "shop_url": entry.get("shop_url", ""),
             "has_handler": key in handlers}
            for key, entry in registry.items()
        ],
        "frequencies": store_links.FREQUENCIES,
        "default_frequency": store_links.DEFAULT_FREQUENCY,
    }


@router.get("/api/stores/recommended")
def recommended() -> dict[str, Any]:
    rec = _call(store_links.recommended_picks)
    df = load_inventory_or_error()
    return {**rec, "picks_by_key": rec["picks"], "picks": _picks_by_id(df, rec["picks"])}


@router.post("/api/stores/simulate")
def simulate(payload: SimulatePayload) -> dict[str, Any]:
    opm = _call(store_links.resolve_frequency, payload.orders_per_month, payload.frequency)
    df = load_inventory_or_error()
    return _call(store_links.simulate, df, payload.picks, opm)


@router.post("/api/stores/apply-preview")
def apply_preview(payload: PicksPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    return {"changes": _call(store_links.apply_preview, df, payload.picks)}


@router.post("/api/stores/apply")
def apply(payload: ApplyPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    changes = [{"row": c.id, "store": c.store, "cantidad": c.cantidad} for c in payload.changes]
    _call(store_links.apply_changes, df, changes)
    return {**inventory_payload(load_inventory_or_error()), "applied": len(changes)}
