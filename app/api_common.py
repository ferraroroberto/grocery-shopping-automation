"""Shared HTTP plumbing for the API routers.

One home for the translation between `src/data.py`'s pandas/Excel data layer
and the HTTP surface: the data layer's lock/missing-file exceptions become
status codes, and a loaded frame becomes the `/api/inventory` payload that
every mutating route echoes back.

Routers import from here rather than from each other, so no router module
depends on another.
"""

from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import HTTPException

from src.data import (
    COLUMNS,
    CONFIG,
    InventoryFileError,
    SpreadsheetLockedError,
    get_supermarket_stats,
    load_inventory_data,
    save_inventory_data,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = Path(__file__).resolve().parent / "static"


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


def get_row(df: pd.DataFrame, item_id: int) -> pd.Series:
    if item_id not in df.index:
        raise inventory_error(404, f"item {item_id} not found")
    return df.loc[item_id]


def _records_from_frame(df: pd.DataFrame) -> list[dict[str, Any]]:
    indexed_df = df.reset_index(names="id")
    safe_df = indexed_df.astype(object).where(pd.notna(indexed_df), None)
    return safe_df.to_dict(orient="records")


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
