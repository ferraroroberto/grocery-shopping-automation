"""Inventory data layer: config loading, XLSX read/write, and shared mutators.

This module is UI-free — it never imports streamlit. Failures are signalled
via typed exceptions; the UI layer (under `app/`) catches them and renders
appropriate messages.
"""

import json
import logging
import math
import os
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple

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

# Optional per-store product-URL columns: `url_<store>`, one per
# store key in `benchmark/stores.json`. Never required — a sheet without any
# loads unchanged; `super` + `buscador` stay the chosen store and its URL.
STORE_URL_PREFIX = "url_"

# Optional per-row fallback columns: the store and product URL the
# cart run sends the shortfall to when the primary comes up short. Never
# required; blank or invalid cells mean the row has no fallback.
FALLBACK_SUPER_COLUMN = "fallback_super"
FALLBACK_URL_COLUMN = "fallback_url"

# Optional per-row conversion from the units the household counts to the packs
# the store sells (a 1 kg bag ≈ 5 apples). Never required; blank, invalid or
# non-positive cells mean 1 unit per pack.
UNITS_PER_PACK_COLUMN = "units_per_pack"

# `apply_item_edit` default: leave the row's units-per-pack cell untouched, so
# callers that know nothing about it (voice, product search) never wipe it.
KEEP = object()

SPREADSHEET_LOCKED_HINT = (
    "The spreadsheet is open in Excel or locked by OneDrive. "
    "Close it in Excel, wait for sync, then try again."
)


class SpreadsheetLockedError(RuntimeError):
    """Raised when the xlsx is open in Excel or locked by OneDrive."""


class InventoryFileError(RuntimeError):
    """Raised for non-lock errors loading or saving the inventory file."""


def resolve_xlsx_path(xlsx_path: Optional[str] = None) -> Path:
    """Return an absolute Path for the configured xlsx (or override)."""
    raw = xlsx_path or CONFIG["data"]["xlsx_file"]
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (REPO_ROOT / p).resolve()
    return p


def purchase_logs_dir() -> Path:
    """``automation.purchase_logs_dir`` from config (relative to the repo), else ``<repo>/purchase_logs``.

    The one place that decides: the cart automation writes its purchase logs
    and run records there, and the benchmark and the app's Shop glance read them.
    """
    p = Path(CONFIG.get("automation", {}).get("purchase_logs_dir", "purchase_logs")).expanduser()
    return p if p.is_absolute() else (REPO_ROOT / p).resolve()


def open_spreadsheet(xlsx_path: Optional[str] = None) -> Path:
    """Open the configured (or given) xlsx in the OS default application.

    Returns the resolved Path on success. Raises FileNotFoundError if the
    file does not exist — callers translate that into their own UI/HTTP
    error shape (Streamlit `st.sidebar.error`, FastAPI 404).
    """
    path = resolve_xlsx_path(xlsx_path)
    if not path.exists():
        raise FileNotFoundError(f"file not found: {path}")
    if platform.system() == "Windows":
        os.startfile(str(path))  # noqa: S606
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
    return path


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


def _coerce_quantity_column(df: pd.DataFrame, col: str) -> pd.Series:
    """Coerce `col` to int, treating a blank cell as 0 and a non-numeric one as an error.

    A blank or stray-text `cantidad`/`tenemos` cell is an ordinary result of
    editing the sheet through "Open spreadsheet", and plain `.astype(int)` on
    such a column raises `IntCastingNaNError` — neither `SpreadsheetLockedError`
    nor `InventoryFileError`, so `load_inventory_or_error` didn't translate it
    and every inventory endpoint returned a bare 500 with no message (issue
    #339). A blank cell defaults to 0; a cell that is filled in but still not a
    number names the offending rows instead.
    """
    original = df[col]
    numeric = pd.to_numeric(original, errors="coerce")
    unparsable = numeric.isna() & original.notna()
    if unparsable.any():
        names = df.loc[unparsable, COLUMNS["comida"]].fillna("(unnamed row)").tolist()
        raise InventoryFileError(
            f"'{col}' is not a number for: {', '.join(map(str, names))}"
        )
    return numeric.fillna(0).astype(int)


def load_inventory_data() -> Optional[pd.DataFrame]:
    """Load inventory data from XLSX file.

    Returns the DataFrame, or None if the file is missing or has wrong
    columns. Raises SpreadsheetLockedError if the file is locked,
    InventoryFileError for other I/O errors.
    """
    xlsx_path = resolve_xlsx_path()
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

    # Only the configured columns are required; optional `url_<store>`
    # columns and any hand-added ones ride along untouched.
    required_columns = list(COLUMNS.values())
    missing_cols = [col for col in required_columns if col not in df.columns]
    if missing_cols:
        logger.error(f"Missing columns in data: {missing_cols}")
        return None

    df[COLUMNS["cantidad"]] = _coerce_quantity_column(df, COLUMNS["cantidad"])
    df[COLUMNS["tenemos"]] = _coerce_quantity_column(df, COLUMNS["tenemos"])
    df[COLUMNS["comprar"]] = (df[COLUMNS["cantidad"]] - df[COLUMNS["tenemos"]]).clip(lower=0)

    logger.info(f"✅ Loaded inventory data: {len(df)} items")
    return df


def save_inventory_data(df: pd.DataFrame, xlsx_path: Optional[str] = None) -> None:
    """Save inventory data back to XLSX file.

    Raises SpreadsheetLockedError if the file is locked, InventoryFileError
    for other I/O errors.
    """
    target = resolve_xlsx_path(xlsx_path)
    try:
        df.to_excel(target, index=False, engine="openpyxl")
        logger.info(f"✅ Inventory data saved to {target}")
    except Exception as e:
        logger.error(f"Error saving data: {e}")
        if _is_spreadsheet_lock_error(e):
            raise SpreadsheetLockedError(SPREADSHEET_LOCKED_HINT) from e
        raise InventoryFileError(str(e)) from e


def store_url_column(store: str) -> str:
    """Column holding one store's product URL, e.g. ``url_carrefour``."""
    return f"{STORE_URL_PREFIX}{store.strip().lower()}"


def store_url_columns(df: pd.DataFrame) -> Dict[str, str]:
    """Store key → its `url_<store>` column, for the ones this sheet has."""
    return {
        col[len(STORE_URL_PREFIX):]: col
        for col in df.columns
        if isinstance(col, str) and col.startswith(STORE_URL_PREFIX) and len(col) > len(STORE_URL_PREFIX)
    }


def cell_text(value: object) -> str:
    """A cell as trimmed text; NaN/None (empty Excel cells) become ``""``."""
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return ""
    return str(value).strip()


def clean_url(raw: object) -> str:
    """Return a usable product URL, or ``""`` for missing / non-URL values.

    Some inventory rows store an uppercase product description in the
    ``buscador`` column instead of a link (e.g. ``"HIELO CUBITOS"``). Only
    ``http(s)`` values are treated as usable URLs; everything else collapses
    to an empty string.
    """
    text = cell_text(raw)
    if text.lower() == "nan" or not text.lower().startswith(("http://", "https://")):
        return ""
    return text


def fallback_pair(
    store_raw: object,
    url_raw: object,
    primary_store: str,
    primary_url: str,
    label: str = "",
) -> Optional[Tuple[str, str]]:
    """A row's fallback as ``(store, url)``, or ``None`` (issue #283).

    The one rule for both readers of the fallback cells — the cart run and the
    item sheet's record: a blank store, a non-``http(s)`` URL, or a fallback
    equal to the row's own primary means no fallback. ``label`` (the item's
    name) makes the own-primary case log a warning; leave it out to stay quiet.
    """
    store = cell_text(store_raw).lower()
    url = clean_url(url_raw)
    if not store or store == "nan" or not url:
        return None
    if store == primary_store.strip().lower() and url == clean_url(primary_url):
        if label:
            logger.warning("⚠️ fallback for '%s' is its own primary — ignored", label)
        return None
    return store, url


def get_unique_zones(df: pd.DataFrame) -> List[str]:
    """Return sorted unique zones from inventory data.

    NaN is dropped, not sorted: a row created without a zone/supermarket
    (e.g. voice-added, issue #86) must not crash the str sort.
    """
    return sorted(df[COLUMNS["lugar"]].dropna().unique().tolist())


def get_unique_supermarkets(df: pd.DataFrame) -> List[str]:
    """Return sorted unique supermarkets from inventory data (NaN dropped)."""
    return sorted(df[COLUMNS["super"]].dropna().unique().tolist())


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


def _clean_str(value: Optional[str]) -> str:
    """Trim a text field, tolerating None (e.g. an empty-options selectbox)."""
    return str(value).strip() if value is not None else ""


def units_per_pack_of(value: object) -> Optional[float]:
    """A units-per-pack cell as a positive float, or ``None`` when it is unset.

    Blank, non-numeric, non-finite and non-positive cells all read as unset, so
    a hand-edited sheet can never turn the cart quantity into a division error.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) and number > 0 else None


def packs_for(units: int, per_pack: Optional[float]) -> int:
    """Store packs that cover `units` counted units: ``ceil(units / per_pack)``.

    0 when nothing is needed; any shortfall buys at least one pack. Unset
    `per_pack` means one unit per pack, so the quantity passes through. The
    quotient is rounded first because ``3 / 0.1`` is ``30.000000000000004``,
    which a bare ``ceil`` would turn into 31.
    """
    if units <= 0:
        return 0
    return math.ceil(round(units / (per_pack or 1.0), 9))


def _set_units_per_pack(df: pd.DataFrame, item_index: int, value: object) -> None:
    """Write one row's units-per-pack cell; clearing never adds the column to a sheet without it."""
    per_pack = units_per_pack_of(value)
    if per_pack is None:
        if UNITS_PER_PACK_COLUMN in df.columns:
            df.at[item_index, UNITS_PER_PACK_COLUMN] = float("nan")
        return
    if UNITS_PER_PACK_COLUMN not in df.columns:
        df[UNITS_PER_PACK_COLUMN] = float("nan")
    df.at[item_index, UNITS_PER_PACK_COLUMN] = per_pack


def apply_item_edit(
    df: pd.DataFrame,
    item_index: int,
    *,
    super_value: Optional[str],
    lugar: Optional[str],
    comida: Optional[str],
    cantidad: int,
    tenemos: int,
    buscador: Optional[str],
    units_per_pack: object = KEEP,
) -> pd.DataFrame:
    """Overwrite one item's editable fields in place and recompute comprar.

    Single source of truth for "what does saving an item mean": trims string
    fields and clamps `cantidad`/`tenemos` to int before recomputing
    `comprar`, matching `update_item_quantity`/`update_target_quantity`
    above. `units_per_pack` is written when given (``None`` clears it) and
    left alone when omitted. Does not persist — callers save (and roll back on
    failure) with `save_inventory_data`.
    """
    cantidad = int(cantidad)
    tenemos = int(tenemos)
    df.at[item_index, COLUMNS["super"]] = _clean_str(super_value)
    df.at[item_index, COLUMNS["lugar"]] = _clean_str(lugar)
    df.at[item_index, COLUMNS["comida"]] = _clean_str(comida)
    df.at[item_index, COLUMNS["cantidad"]] = cantidad
    df.at[item_index, COLUMNS["tenemos"]] = tenemos
    df.at[item_index, COLUMNS["buscador"]] = _clean_str(buscador)
    df.at[item_index, COLUMNS["comprar"]] = max(0, cantidad - tenemos)
    if units_per_pack is not KEEP:
        _set_units_per_pack(df, item_index, units_per_pack)
    return df


def build_new_item_row(
    *,
    super_value: Optional[str],
    lugar: Optional[str],
    comida: Optional[str],
    cantidad: int,
    tenemos: int,
    buscador: Optional[str],
    units_per_pack: Optional[float] = None,
) -> Dict[str, object]:
    """Build one new inventory row, trimmed/clamped the same way as `apply_item_edit`."""
    cantidad = int(cantidad)
    tenemos = int(tenemos)
    row: Dict[str, object] = {
        COLUMNS["super"]: _clean_str(super_value),
        COLUMNS["lugar"]: _clean_str(lugar),
        COLUMNS["comida"]: _clean_str(comida),
        COLUMNS["cantidad"]: cantidad,
        COLUMNS["tenemos"]: tenemos,
        COLUMNS["buscador"]: _clean_str(buscador),
        COLUMNS["comprar"]: max(0, cantidad - tenemos),
    }
    per_pack = units_per_pack_of(units_per_pack)
    if per_pack is not None:
        row[UNITS_PER_PACK_COLUMN] = per_pack
    return row


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
