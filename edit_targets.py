"""Edit Targets mode — adjust the target quantity (cantidad) per item."""

import pandas as pd
import streamlit as st

from data import COLUMNS, get_unique_zones, update_target_quantity
from ui_helpers import buy_html, qty_html


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Render the edit-targets interface."""
    zones = get_unique_zones(df)
    selected_zone = st.selectbox("Zone", zones, label_visibility="collapsed")

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
                df = update_target_quantity(df, idx, -1)
                st.rerun()
        with col3:
            st.markdown(qty_html(current_qty, target_qty), unsafe_allow_html=True)
        with col4:
            if st.button("➕", key=f"target_plus_{idx}"):
                df = update_target_quantity(df, idx, 1)
                st.rerun()
        with col5:
            st.markdown(buy_html(buy_qty), unsafe_allow_html=True)

    return df
