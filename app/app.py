#!/usr/bin/env python3
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import streamlit as st

from app import add_item, audio_audit, audit, edit_item, edit_targets, export, shopping
from app.ui_helpers import CSS, copy_to_clipboard, get_local_ip, open_inventory_spreadsheet
from src.data import (
    COLUMNS,
    CONFIG,
    MODES,
    InventoryFileError,
    SpreadsheetLockedError,
    get_supermarket_stats,
    load_inventory_data,
)


def _render_sidebar() -> None:
    with st.sidebar:
        if st.button(
            "📂 Open spreadsheet",
            help="Opens the Excel file in the default app (e.g. Excel). Useful when OneDrive has not refreshed yet.",
            width="stretch",
        ):
            open_inventory_spreadsheet()

        local_url = f"https://{get_local_ip()}:8501"
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


def main() -> None:
    st.set_page_config(**CONFIG["ui"]["page_config"])
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown("### 🛒 Inventory & Shopping Helper")

    _init_session_state()
    _render_sidebar()

    df = st.session_state.inventory_data
    tabs = st.tabs(list(MODES.values()))

    for key, tab in zip(MODES.keys(), tabs):
        with tab:
            if key == "audit":
                st.session_state.inventory_data = audit.main(df)
            elif key == "audio_audit":
                st.session_state.inventory_data = audio_audit.main(df)
            elif key == "edit":
                st.session_state.inventory_data = edit_targets.main(df)
            elif key == "edit_item":
                st.session_state.inventory_data = edit_item.main(df)
            elif key == "add_item":
                st.session_state.inventory_data = add_item.main(df)
            elif key == "shopping":
                shopping.main(df)
            elif key == "export":
                export.main(df)


if __name__ == "__main__":
    main()
