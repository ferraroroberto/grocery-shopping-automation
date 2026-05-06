"""Edit Targets mode — adjust the target quantity (cantidad) per item."""

import pandas as pd
import streamlit as st

from app.ui_helpers import buy_html, qty_html, render_save_error
from src.data import (
    COLUMNS,
    InventoryFileError,
    SpreadsheetLockedError,
    get_unique_zones,
    update_target_quantity,
)


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Render the edit-targets interface."""
    zones = get_unique_zones(df)
    selected_zone = st.selectbox("Zone", zones, label_visibility="collapsed", key="edit_targets_zone")

    zone_data = (
        df[df[COLUMNS["lugar"]] == selected_zone]
        .copy()
        .sort_values(COLUMNS["comida"], key=lambda s: s.str.lower())
    )

    if zone_data.empty:
        st.info(f"No items found in {selected_zone}")
        return df

    st.caption(f"{selected_zone.title()} · {len(zone_data)} items · have / target · buy")

    for idx in zone_data.index:
        item_name = zone_data.at[idx, COLUMNS["comida"]]
        current_qty = zone_data.at[idx, COLUMNS["tenemos"]]
        target_qty = zone_data.at[idx, COLUMNS["cantidad"]]
        buy_qty = zone_data.at[idx, COLUMNS["comprar"]]

        col1, col2, col3, col4, col5 = st.columns([4, 1, 2, 1, 2])
        with col1:
            st.markdown(f"**{item_name}**")
        with col2:
            if st.button("➖", key=f"target_minus_{idx}"):
                try:
                    df = update_target_quantity(df, idx, -1)
                except (SpreadsheetLockedError, InventoryFileError) as e:
                    render_save_error(e)
                else:
                    st.rerun()
        with col3:
            st.markdown(qty_html(current_qty, target_qty), unsafe_allow_html=True)
        with col4:
            if st.button("➕", key=f"target_plus_{idx}"):
                try:
                    df = update_target_quantity(df, idx, 1)
                except (SpreadsheetLockedError, InventoryFileError) as e:
                    render_save_error(e)
                else:
                    st.rerun()
        with col5:
            st.markdown(buy_html(buy_qty), unsafe_allow_html=True)

    return df
