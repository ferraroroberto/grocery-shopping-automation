"""Shared HTTP plumbing for the API routers.

One home for the translation between `src/data.py`'s pandas/Excel data layer
and the HTTP surface: the data layer's lock/missing-file exceptions become
status codes, and a loaded frame becomes the `/api/inventory` payload that
every mutating route echoes back.

Routers import from here rather than from each other, so no router module
depends on another.
"""

import logging
from pathlib import Path
from typing import Any, Optional

import pandas as pd
from fastapi import HTTPException

from benchmark.build_basket import item_key
from src.data import (
    COLUMNS,
    CONFIG,
    FALLBACK_SUPER_COLUMN,
    FALLBACK_URL_COLUMN,
    InventoryFileError,
    SpreadsheetLockedError,
    cell_text,
    fallback_pair,
    get_supermarket_stats,
    load_inventory_data,
    save_inventory_data,
    store_url_columns,
)
from src.store_links import link_kind

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"

# Item ids are row positions, re-numbered on every load, so a client holding
# the list from before another device's delete would write to the wrong row.
# Item routes take the item's name and answer 409 with this marker
# header when the row at that id is no longer that item.
STALE_ITEM_HEADER = "X-Inventory-Stale"
STALE_ITEM_DETAIL = "This list changed on another device; reload and try again."

logger = logging.getLogger(__name__)


def inventory_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def load_inventory_or_error() -> pd.DataFrame:
    try:
        df = load_inventory_data()
    except SpreadsheetLockedError as exc:
        raise inventory_error(423, str(exc)) from exc
    except InventoryFileError as exc:
        raise inventory_error(500, str(exc)) from exc

    if df is None:
        raise inventory_error(
            404,
            "Inventory file is missing or does not contain the configured columns.",
        )
    return df


def save_or_error(df: pd.DataFrame) -> None:
    mutate_or_error(save_inventory_data, df)


def mutate_or_error(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call a data-layer mutator, translating its lock/file errors to HTTP.

    Shared by every endpoint that calls into `src/data.py`'s mutators —
    `SpreadsheetLockedError` -> 423, `InventoryFileError` -> 500 — so that
    translation lives in one place instead of being re-typed per call site.
    """
    try:
        return fn(*args, **kwargs)
    except SpreadsheetLockedError as exc:
        raise inventory_error(423, str(exc)) from exc
    except InventoryFileError as exc:
        raise inventory_error(500, str(exc)) from exc


def get_row(df: pd.DataFrame, item_id: int, name: Optional[str] = None) -> pd.Series:
    """The row at `item_id`; with `name`, only while it is still that item.

    `name` is compared by the item key the store links use (case, accents and
    spacing don't count). A mismatch, or an id past the end, means the caller's
    list is stale: 409 with `STALE_ITEM_HEADER`, never a write to another row.
    """
    row = df.loc[item_id] if item_id in df.index else None
    if name is not None and (row is None or item_key(cell_text(row[COLUMNS["comida"]])) != item_key(name)):
        found = None if row is None else cell_text(row[COLUMNS["comida"]])
        logger.warning("⚠️ Stale item request refused: id %s expected %r, row holds %r", item_id, name, found)
        raise HTTPException(status_code=409, detail=STALE_ITEM_DETAIL, headers={STALE_ITEM_HEADER: "1"})
    if row is None:
        raise inventory_error(404, f"item {item_id} not found")
    return row


def _records_from_frame(df: pd.DataFrame) -> list[dict[str, Any]]:
    indexed_df = df.reset_index(names="id")
    safe_df = indexed_df.astype(object).where(pd.notna(indexed_df), None)
    records = safe_df.to_dict(orient="records")
    # Per-store product links: {store: url} for every non-empty
    # `url_<store>` cell, so the UI needn't know the column naming.
    url_cols = store_url_columns(df)
    for record in records:
        record["urls"] = {
            store: url for store, col in url_cols.items() if (url := cell_text(record.get(col)))
        }
        # Links that are not a product page (a search page, or a URL missing
        # the store's product id), so the UI can say so instead of posing
        # them as product links.
        record["url_kinds"] = {
            store: kind for store, url in record["urls"].items()
            if (kind := link_kind(store, url)) != "product"
        }
        # The fallback the cart run would use: the shared rule in
        # src.data, so the sheet never shows one the run would ignore.
        pair = fallback_pair(
            record.get(FALLBACK_SUPER_COLUMN), record.get(FALLBACK_URL_COLUMN),
            cell_text(record.get(COLUMNS["super"])), record.get(COLUMNS["buscador"]),
        )
        record["fallback"] = {"store": pair[0], "url": pair[1], "kind": link_kind(*pair)} if pair else None
    return records


def inventory_payload(df: pd.DataFrame) -> dict[str, Any]:
    shopping_items = df[df[COLUMNS["comprar"]] > 0].copy()
    stats = get_supermarket_stats(shopping_items, set()) if not shopping_items.empty else {}
    zones = sorted(str(zone) for zone in df[COLUMNS["lugar"]].dropna().unique())
    supermarkets = sorted(str(sm) for sm in df[COLUMNS["super"]].dropna().unique())

    return {
        "app": {
            "title": CONFIG["app"]["title"],
            "version": CONFIG["app"]["version"],
        },
        "columns": COLUMNS,
        "summary": {
            "total_items": int(len(df)),
            "shopping_items": int(len(shopping_items)),
            "shopping_units": int(shopping_items[COLUMNS["comprar"]].sum()) if not shopping_items.empty else 0,
            "zones": zones,
            "supermarkets": supermarkets,
            "supermarket_stats": stats,
        },
        "audio": {
            "models": CONFIG["audio_audit"]["llm_models_available"],
            "default_model": CONFIG["audio_audit"]["llm_model"],
            "clamp": int(CONFIG["audio_audit"].get("max_count_clamp_above_target", 5)),
        },
        "items": _records_from_frame(df),
    }
