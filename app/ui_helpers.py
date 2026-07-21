"""UI-only helpers: CSS, inline HTML formatters, and sidebar utility actions."""

import logging
import os
import platform
import subprocess
import sys
from pathlib import Path

import streamlit as st

from src.data import CONFIG, InventoryFileError, SpreadsheetLockedError

logger = logging.getLogger(__name__)


def render_save_error(exc: Exception) -> None:
    """Render a data-layer save exception as a Streamlit message.

    Lock errors are shown as warnings (the user can retry after closing
    Excel); other I/O errors are shown as errors.
    """
    if isinstance(exc, SpreadsheetLockedError):
        st.warning(f"Could not save. {exc}")
    elif isinstance(exc, InventoryFileError):
        st.error(f"❌ Error saving inventory data: {exc}")
    else:
        st.error(f"❌ Unexpected error: {exc}")

CSS = """
<style>
/* Shrink top/bottom padding of main area */
.main .block-container {
    padding-top: 0.75rem !important;
    padding-bottom: 0.5rem !important;
}
/* Tighter column cell padding */
[data-testid="column"] {
    padding-left: 0.15rem !important;
    padding-right: 0.15rem !important;
}
/* Smaller buttons */
.stButton > button {
    padding: 0.2rem 0.55rem !important;
    font-size: 0.92rem !important;
    line-height: 1.3 !important;
    min-height: 0 !important;
}
/* Compact link buttons */
.stLinkButton > a {
    padding: 0.2rem 0.55rem !important;
    font-size: 0.85rem !important;
}
/* Compact dividers */
hr { margin: 0.2rem 0 !important; }
/* Compact expander header */
details > summary {
    padding: 0.35rem 0.6rem !important;
    font-size: 0.9rem !important;
}
/* Sidebar compactness */
section[data-testid="stSidebar"] .block-container {
    padding-top: 0.5rem !important;
}
/* Compact radio buttons */
.stRadio > div { gap: 0.1rem !important; }
/* Compact selectbox */
[data-testid="stSelectbox"] { margin-bottom: 0.2rem !important; }
/* Mobile landscape hint — hidden on desktop */
.mobile-landscape-hint { display: none; }
@media (max-width: 768px) {
    .mobile-landscape-hint {
        display: block;
        padding: 0.3rem 0.6rem;
        margin-bottom: 0.4rem;
        font-size: 0.78rem;
        color: #aaa;
        background: rgba(255,255,255,0.05);
        border-left: 2px solid #555;
        border-radius: 2px;
    }
}
/* Mobile: keep columns in a single row, shrink buttons/text to fit */
@media (max-width: 768px) {
    [data-testid="stHorizontalBlock"] {
        flex-wrap: nowrap !important;
        align-items: center !important;
        gap: 0.1rem !important;
    }
    [data-testid="stHorizontalBlock"] > [data-testid="column"] {
        min-width: 0 !important;
        overflow: hidden;
    }
    [data-testid="stHorizontalBlock"] .stButton > button {
        padding: 0.15rem 0.2rem !important;
        font-size: 0.75rem !important;
        min-width: 0 !important;
        width: 100% !important;
    }
    [data-testid="stHorizontalBlock"] p,
    [data-testid="stHorizontalBlock"] strong {
        overflow: hidden;
        text-overflow: ellipsis;
        white-space: nowrap;
        font-size: 0.82rem !important;
        margin: 0 !important;
    }
}
</style>
"""


def qty_html(current: int, target: int) -> str:
    """Compact inline HTML for current/target display with color coding."""
    color = "#21c354" if current >= target else ("#ffa500" if current > 0 else "#ff4b4b")
    return (
        f"<div style='text-align:center;padding-top:6px;font-size:0.93rem'>"
        f"<span style='color:{color};font-weight:600'>{current}</span>"
        f"<span style='color:#666'>/{target}</span></div>"
    )


def buy_html(qty: int) -> str:
    """Compact inline HTML for buy quantity display."""
    if qty > 0:
        return (
            f"<div style='text-align:center;padding-top:6px;font-size:0.93rem;"
            f"color:#ff4b4b;font-weight:600'>↓{qty}</div>"
        )
    return "<div style='text-align:center;padding-top:6px;font-size:0.93rem;color:#21c354'>✓</div>"


def copy_to_clipboard(text: str) -> None:
    """Copy text to Windows clipboard via clip.exe."""
    subprocess.Popen(
        ["clip"],
        stdin=subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    ).communicate(input=text.encode("utf-8"))


def open_inventory_spreadsheet() -> None:
    """Open the configured XLSX in the default application (e.g. Excel)."""
    raw = Path(CONFIG["data"]["xlsx_file"]).expanduser()
    if not raw.is_absolute():
        raw = (Path(__file__).resolve().parent.parent / raw).resolve()
    path = raw
    if not path.exists():
        st.sidebar.error(f"File not found:\n`{path}`")
        logger.error("Open spreadsheet: file missing at %s", path)
        return
    try:
        if platform.system() == "Windows":
            os.startfile(str(path))  # noqa: S606
        elif platform.system() == "Darwin":
            subprocess.run(["open", str(path)], check=False)
        else:
            subprocess.run(["xdg-open", str(path)], check=False)
        st.sidebar.success("Opened. Close the workbook before saving changes from this app.")
    except OSError as e:
        st.sidebar.error(f"Could not open file: {e}")
        logger.error("Open spreadsheet failed: %s", e)
