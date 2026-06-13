#!/usr/bin/env python3
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
_repo_str = str(REPO_ROOT)
# Always put REPO_ROOT first — Streamlit adds the script dir (app/) to sys.path before
# the script runs, so a plain insert-if-absent leaves app/ ahead and Python resolves
# `from app import …` to app.py itself, causing a circular import.
if _repo_str in sys.path:
    sys.path.remove(_repo_str)
sys.path.insert(0, _repo_str)

import streamlit as st

from app import add_item, audio_audit, audit, edit_item, edit_targets, export, shopping
from app.ui_helpers import CSS, copy_to_clipboard, open_inventory_spreadsheet
from src.data import (
    COLUMNS,
    CONFIG,
    MODES,
    InventoryFileError,
    SpreadsheetLockedError,
    get_supermarket_stats,
    load_inventory_data,
)
from src.net import local_ip


def _render_sidebar() -> str:
    """Render sidebar (mode picker + actions + stats). Returns the selected mode key."""
    with st.sidebar:
        mode_key = st.radio(
            "Mode",
            options=list(MODES.keys()),
            format_func=lambda k: MODES[k],
            key="mode_selector",
            label_visibility="collapsed",
        )
        st.divider()

        if st.button(
            "📂 Open spreadsheet",
            help="Opens the Excel file in the default app (e.g. Excel). Useful when OneDrive has not refreshed yet.",
            width="stretch",
        ):
            open_inventory_spreadsheet()

        local_url = f"https://{local_ip()}:8501"
        if st.button(
            "📋 Copy link",
            help=f"Copies {local_url} to clipboard — paste in Telegram to open on mobile.",
            width="stretch",
        ):
            copy_to_clipboard(local_url)
            st.success("✓ Copied!")

        if st.button("🔴 Close app", help="Stop the Streamlit server.", width="stretch"):
            os._exit(0)

        if st.session_state.inventory_data is not None:
            df_stats = st.session_state.inventory_data
            sm_shopping = df_stats[df_stats[COLUMNS["comprar"]] > 0].copy()

            st.divider()
            st.caption(f"{len(df_stats)} total tracked items")

            if not sm_shopping.empty:
                stats_by_supermarket = get_supermarket_stats(sm_shopping, st.session_state.bought_items)
                preferred_order = ["mercadona", "ametller"]
                ordered_supermarkets = [sm for sm in preferred_order if sm in stats_by_supermarket] + [
                    sm for sm in stats_by_supermarket if sm not in preferred_order
                ]

                for sm in ordered_supermarkets:
                    stats = stats_by_supermarket[sm]
                    offset_items = st.session_state.get(f"cart_offset_items_{sm}", 0)
                    offset_units = st.session_state.get(f"cart_offset_units_{sm}", 0)
                    done_u = stats["got_it_unique"] + offset_items
                    total_u = stats["total_unique"]
                    done_q = stats["got_it_quantity"] + offset_units
                    total_q = stats["total_quantity"]
                    st.markdown(f"**{sm.title()}**")
                    pm1, pm2 = st.columns(2)
                    with pm1:
                        st.metric("Items got", f"{done_u}/{total_u}")
                    with pm2:
                        st.metric("Units got", f"{done_q}/{total_q}")
                    oc1, oc2 = st.columns(2)
                    with oc1:
                        st.number_input(
                            "＋items",
                            value=0,
                            min_value=0,
                            step=1,
                            key=f"cart_offset_items_{sm}",
                        )
                    with oc2:
                        st.number_input(
                            "＋units",
                            value=0,
                            min_value=0,
                            step=1,
                            key=f"cart_offset_units_{sm}",
                        )

    return mode_key


def _warn_if_automation_running(mode_key: str) -> None:
    """Warn if a cart-automation run is still live while not in Shopping mode.

    The subprocess keeps running and draining output on its own thread even
    when the operator navigates away — this just surfaces it and offers a stop,
    so a run is never silently orphaned.
    """
    from app import automation_runner

    process = st.session_state.get("automation_process")
    if mode_key == "shopping" or not automation_runner.is_running(process):
        return
    st.warning(
        "⚠️ A cart automation run is still in progress (started in the "
        "Shopping List mode). It will keep running in the background."
    )
    if st.button("🛑 Stop automation run", key="automation_stop_global"):
        automation_runner.stop_run(process)
        st.rerun()


def _init_session_state() -> None:
    if "inventory_data" not in st.session_state:
        try:
            df = load_inventory_data()
        except SpreadsheetLockedError as e:
            st.error(f"❌ Could not load inventory. {e}")
            st.stop()
        except InventoryFileError as e:
            st.error(f"❌ Error loading inventory data: {e}")
            st.stop()
        if df is None:
            xlsx_rel = CONFIG["data"]["xlsx_file"]
            st.error(
                f"❌ Inventory file not found at `{xlsx_rel}` "
                "(or it has the wrong columns). "
                "Copy `data/list.example.xlsx` to `data/list.xlsx` to start, "
                "or update `xlsx_file` in `config.json`."
            )
            st.stop()
        st.session_state.inventory_data = df

    if "bought_items" not in st.session_state:
        st.session_state.bought_items = set()

    if "extra_shopping_items" not in st.session_state:
        st.session_state.extra_shopping_items = {}

    if "extra_bought_items" not in st.session_state:
        st.session_state.extra_bought_items = {}

    if "extra_item_counter" not in st.session_state:
        st.session_state.extra_item_counter = 0


def _render_top_bar() -> None:
    """App title plus a Refresh button that resets to a clean, freshly-loaded state.

    Refresh stops any live automation run, clears all session state (the
    automation run, bought/extra marks, every widget value) and reruns — the
    next pass reloads the inventory fresh from the Excel file. The Excel file
    itself is never written or cleared.
    """
    from app import automation_runner

    title_col, refresh_col = st.columns([5, 1])
    with title_col:
        st.markdown("### 🛒 Inventory & Shopping Helper")
    with refresh_col:
        if st.button(
            "🔄 Refresh",
            width="stretch",
            key="app_refresh_btn",
            help=(
                "Reload the app from a clean state — stops any automation run "
                "and clears all in-app state, then reloads the inventory from "
                "the Excel file. The Excel file is not modified."
            ),
        ):
            automation_runner.stop_run(st.session_state.get("automation_process"))
            st.session_state.clear()
            st.rerun()


def main() -> None:
    st.set_page_config(**CONFIG["ui"]["page_config"])
    st.markdown(CSS, unsafe_allow_html=True)
    _render_top_bar()

    _init_session_state()
    mode_key = _render_sidebar()

    _warn_if_automation_running(mode_key)

    df = st.session_state.inventory_data

    if mode_key == "audit":
        st.session_state.inventory_data = audit.main(df)
    elif mode_key == "audio_audit":
        result = audio_audit.main(df)
        if result is not None:
            st.session_state.inventory_data = result
    elif mode_key == "edit":
        st.session_state.inventory_data = edit_targets.main(df)
    elif mode_key == "edit_item":
        st.session_state.inventory_data = edit_item.main(df)
    elif mode_key == "add_item":
        st.session_state.inventory_data = add_item.main(df)
    elif mode_key == "shopping":
        shopping.main(df)
    elif mode_key == "export":
        export.main(df)


if __name__ == "__main__":
    main()
