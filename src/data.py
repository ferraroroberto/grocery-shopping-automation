"""Inventory data layer: config loading, XLSX read/write, and shared mutators.

This module is UI-free — it never imports streamlit. Failures are signalled
via typed exceptions; the UI layer (under `app/`) catches them and renders
appropriate messages.
"""

import json
import logging
import shutil
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = Path(__file__).resolve().parent / "config.json"
CONFIG_EXAMPLE_PATH = Path(__file__).resolve().parent / "config.example.json"

# `config.json` is gitignored — seed it from `config.example.json` (committed)
# the first time the app runs in a fresh checkout.
if not CONFIG_PATH.exists() and CONFIG_EXAMPLE_PATH.exists():
    shutil.copyfile(CONFIG_EXAMPLE_PATH, CONFIG_PATH)

with open(CONFIG_PATH, "r", encoding="utf-8") as f:
    CONFIG = json.load(f)

logging.basicConfig(
    level=getattr(logging, CONFIG["logging"]["level"]),
    format=CONFIG["logging"]["format"],
)
logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Third-party log noise suppression
# ─────────────────────────────────────────────────────────────────────────────
# Background: running this web app on Windows (uvicorn/FastAPI, or the legacy
# Streamlit server) produces two kinds of repetitive third-party log records
# that are not actionable for this project:
#
# 1. `asyncio` — Windows' Proactor event loop emits an ERROR with a traceback
#    ending in `ConnectionResetError: [WinError 10054]` whenever a long-lived
#    HTTP/websocket peer disconnects abruptly (browser tab refresh, mobile
#    screen sleep, Wi-Fi handoff, app restart). The underlying TCP reset is
#    benign — the client reconnects on its own, and nothing in user-space needs
#    to handle it. During a single audit session we typically see a dozen of
#    these, each as a 4-line traceback.
#
# 2. `httpx` — the Anthropic SDK uses httpx, which logs an INFO line for every
#    HTTP request (`HTTP Request: POST ...`). Our own loggers in
#    `inventory_extract` and `transcribe_client` already record the same calls
#    with more context, so the httpx echo is duplicate noise.
#
# What we suppress, narrowly:
#  - `asyncio`: a logging.Filter that drops ONLY error records whose exception
#    traceback contains the literal `WinError 10054`. Any other asyncio error
#    (and any non-error record) passes through untouched. On non-Windows
#    platforms this filter is effectively a no-op since the marker won't appear.
#  - `httpx`: level raised from INFO to WARNING. Real HTTP problems
#    (connection refused, 4xx/5xx) still surface.
#
# How to re-enable for debugging:
#  - Comment out the `_suppress_known_log_noise()` call below, OR
#  - Lower a specific logger at runtime, e.g. in a Python REPL or
#    early in your entrypoint (`app/api.py` or `app/app.py`):
#        logging.getLogger("httpx").setLevel(logging.INFO)
#        logging.getLogger("asyncio").filters.clear()
#
# When NOT to suppress: if you ever see ANY asyncio error you don't recognise
# (i.e. one whose traceback does NOT mention WinError 10054), the filter is
# already letting it through — investigate that one. If you suspect the filter
# itself is hiding a real error, disable it and reproduce.
# ─────────────────────────────────────────────────────────────────────────────


class _AsyncioConnectionResetFilter(logging.Filter):
    """Drop asyncio ERROR records whose traceback ends in WinError 10054."""

    _MARKER = "WinError 10054"

    def filter(self, record: logging.LogRecord) -> bool:
        if record.levelno < logging.ERROR or not record.exc_info:
            return True
        try:
            exc_text = logging.Formatter().formatException(record.exc_info)
        except Exception:  # pragma: no cover — defensive: never break logging
            return True
        return self._MARKER not in exc_text


def _suppress_known_log_noise() -> None:
    """Apply the targeted filters described above. Idempotent."""
    asyncio_logger = logging.getLogger("asyncio")
    if not any(isinstance(f, _AsyncioConnectionResetFilter) for f in asyncio_logger.filters):
        asyncio_logger.addFilter(_AsyncioConnectionResetFilter())
    logging.getLogger("httpx").setLevel(logging.WARNING)


_suppress_known_log_noise()

COLUMNS = CONFIG["data"]["columns"]
MODES = CONFIG["ui"]["modes"]

SPREADSHEET_LOCKED_HINT = (
    "The spreadsheet is open in Excel or locked by OneDrive. "
    "Close it in Excel, wait for sync, then try again."
)


class SpreadsheetLockedError(RuntimeError):
    """Raised when the xlsx is open in Excel or locked by OneDrive."""


class InventoryFileError(RuntimeError):
    """Raised for non-lock errors loading or saving the inventory file."""


def _resolve_xlsx_path(xlsx_path: Optional[str] = None) -> Path:
    """Return an absolute Path for the configured xlsx (or override)."""
    raw = xlsx_path or CONFIG["data"]["xlsx_file"]
    p = Path(raw)
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    return p


def _is_spreadsheet_lock_error(exc: BaseException) -> bool:
    if isinstance(exc, PermissionError):
        return True
    if isinstance(exc, OSError):
        errno = getattr(exc, "errno", None)
        if errno is not None and errno in (13, 11):
            return True
    lowered = str(exc).lower()
    return any(
        phrase in lowered
        for phrase in (
            "permission denied",
            "being used by another process",
            "access is denied",
            "the process cannot access the file",
        )
    )


def load_inventory_data() -> Optional[pd.DataFrame]:
    """Load inventory data from XLSX file.

    Returns the DataFrame, or None if the file is missing or has wrong
    columns. Raises SpreadsheetLockedError if the file is locked,
    InventoryFileError for other I/O errors.
    """
    xlsx_path = _resolve_xlsx_path()
    if not xlsx_path.exists():
        logger.error(f"Inventory file not found: {xlsx_path}")
        return None

    try:
        df = pd.read_excel(xlsx_path, engine="openpyxl")
    except Exception as e:
        logger.error(f"Error loading data: {e}")
        if _is_spreadsheet_lock_error(e):
            raise SpreadsheetLockedError(SPREADSHEET_LOCKED_HINT) from e
        raise InventoryFileError(str(e)) from e

    required_columns = list(COLUMNS.values())
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing columns in data: {missing_cols}")
        return None

    df[COLUMNS["cantidad"]] = df[COLUMNS["cantidad"]].astype(int)
    df[COLUMNS["tenemos"]] = df[COLUMNS["tenemos"]].astype(int)
    df[COLUMNS["comprar"]] = (df[COLUMNS["cantidad"]] - df[COLUMNS["tenemos"]]).clip(lower=0)

    logger.info(f"✅ Loaded inventory data: {len(df)} items")
    return df


def save_inventory_data(df: pd.DataFrame, xlsx_path: Optional[str] = None) -> None:
    """Save inventory data back to XLSX file.

    Raises SpreadsheetLockedError if the file is locked, InventoryFileError
    for other I/O errors.
    """
    target = _resolve_xlsx_path(xlsx_path)
    try:
        df.to_excel(target, index=False, engine="openpyxl")
        logger.info(f"✅ Inventory data saved to {target}")
    except Exception as e:
        logger.error(f"Error saving data: {e}")
        if _is_spreadsheet_lock_error(e):
            raise SpreadsheetLockedError(SPREADSHEET_LOCKED_HINT) from e
        raise InventoryFileError(str(e)) from e


def get_unique_zones(df: pd.DataFrame) -> List[str]:
    """Return sorted unique zones from inventory data."""
    return sorted(df[COLUMNS["lugar"]].unique().tolist())


def get_unique_supermarkets(df: pd.DataFrame) -> List[str]:
    """Return sorted unique supermarkets from inventory data."""
    return sorted(df[COLUMNS["super"]].unique().tolist())


def get_supermarket_stats(shopping_items: pd.DataFrame, bought_items: set) -> Dict[str, Dict[str, int]]:
    """Calculate per-supermarket statistics for the shopping items."""
    stats = {}
    for supermarket in get_unique_supermarkets(shopping_items):
        sm_items = shopping_items[shopping_items[COLUMNS["super"]] == supermarket]
        bought_in_sm = sm_items[sm_items.index.isin(bought_items)]
        stats[supermarket] = {
            "total_unique": len(sm_items),
            "total_quantity": int(sm_items[COLUMNS["comprar"]].sum()),
            "got_it_unique": len(bought_in_sm),
            "got_it_quantity": int(bought_in_sm[COLUMNS["comprar"]].sum()),
        }
    return stats


def update_item_quantity(df: pd.DataFrame, item_index: int, delta: int) -> pd.DataFrame:
    """Update tenemos for an item, recompute comprar, and persist.

    On save failure, the in-memory values are restored and the underlying
    exception (SpreadsheetLockedError / InventoryFileError) is re-raised.
    """
    old_tenemos = int(df.at[item_index, COLUMNS["tenemos"]])
    old_comprar = int(df.at[item_index, COLUMNS["comprar"]])
    new_qty = max(0, old_tenemos + delta)
    df.at[item_index, COLUMNS["tenemos"]] = new_qty
    df.at[item_index, COLUMNS["comprar"]] = max(0, df.at[item_index, COLUMNS["cantidad"]] - new_qty)
    try:
        save_inventory_data(df)
    except (SpreadsheetLockedError, InventoryFileError):
        df.at[item_index, COLUMNS["tenemos"]] = old_tenemos
        df.at[item_index, COLUMNS["comprar"]] = old_comprar
        raise
    logger.debug(f"Updated item {item_index}: tenemos={new_qty}")
    return df


def update_target_quantity(df: pd.DataFrame, item_index: int, delta: int) -> pd.DataFrame:
    """Update cantidad (target) for an item, recompute comprar, and persist."""
    old_target = int(df.at[item_index, COLUMNS["cantidad"]])
    old_comprar = int(df.at[item_index, COLUMNS["comprar"]])
    new_target = max(0, old_target + delta)
    df.at[item_index, COLUMNS["cantidad"]] = new_target
    df.at[item_index, COLUMNS["comprar"]] = max(0, new_target - df.at[item_index, COLUMNS["tenemos"]])
    try:
        save_inventory_data(df)
    except (SpreadsheetLockedError, InventoryFileError):
        df.at[item_index, COLUMNS["cantidad"]] = old_target
        df.at[item_index, COLUMNS["comprar"]] = old_comprar
        raise
    logger.debug(f"Updated target for item {item_index}: cantidad={new_target}")
    return df


def bulk_apply_tenemos(
    df: pd.DataFrame,
    updates: Dict[int, int],
    save: bool = True,
    xlsx_path: Optional[str] = None,
) -> pd.DataFrame:
    """Apply many tenemos updates in one pass and (optionally) save once.

    `updates` maps DataFrame index → new tenemos value. Negative values are
    clamped to 0. `comprar` is recomputed for each touched row. Atomic: if
    the save fails, the original tenemos/comprar values are restored and
    the underlying exception is re-raised.

    `xlsx_path` overrides the configured file path — used by tests against
    a fixture so the live spreadsheet is never written.
    """
    if not updates:
        return df

    snapshot: Dict[int, tuple] = {}
    for idx, new_val in updates.items():
        snapshot[idx] = (
            int(df.at[idx, COLUMNS["tenemos"]]),
            int(df.at[idx, COLUMNS["comprar"]]),
        )
        clamped = max(0, int(new_val))
        df.at[idx, COLUMNS["tenemos"]] = clamped
        df.at[idx, COLUMNS["comprar"]] = max(
            0, int(df.at[idx, COLUMNS["cantidad"]]) - clamped
        )

    if not save:
        logger.debug(f"Bulk-updated {len(updates)} rows in memory (no save)")
        return df

    try:
        save_inventory_data(df, xlsx_path=xlsx_path)
        logger.info(f"✅ Bulk applied {len(updates)} tenemos updates")
        return df
    except (SpreadsheetLockedError, InventoryFileError):
        logger.error(f"Bulk save failed, rolling back {len(updates)} rows")
        for idx, (old_t, old_c) in snapshot.items():
            df.at[idx, COLUMNS["tenemos"]] = old_t
            df.at[idx, COLUMNS["comprar"]] = old_c
        raise
