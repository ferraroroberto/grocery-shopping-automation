"""Audit Inventory mode — walk through zones and update current stock."""

import pandas as pd
import streamlit as st

from app.ui_helpers import buy_html, qty_html, render_save_error
from src.data import (
    COLUMNS,
    InventoryFileError,
    SpreadsheetLockedError,
    get_unique_zones,
    update_item_quantity,
    update_target_quantity,
)


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Render the audit mode interface."""
    st.markdown(
        "<div class='mobile-landscape-hint'>📐 Rotate to landscape for best experience</div>",
        unsafe_allow_html=True,
    )
    zones = get_unique_zones(df)
    selected_zone = st.selectbox("Zone", zones, label_visibility="collapsed", key="audit_zone")

    zone_data = df[
        (df[COLUMNS["lugar"]] == selected_zone) & (df[COLUMNS["cantidad"]] > 0)
    ].copy().sort_values(COLUMNS["comida"], key=lambda s: s.str.lower())

    if zone_data.empty:
        st.info(f"No tracked items in {selected_zone}")
        return df

    st.caption(f"{selected_zone.title()} · {len(zone_data)} items")

    _, hv, hd, ht, hb = st.columns([4, 2, 2, 2, 2])
    with hv:
        st.markdown("<div style='text-align:center;font-size:0.72rem;color:#888;padding-bottom:0'>➖ have ➕</div>", unsafe_allow_html=True)
    with hd:
        st.markdown("<div style='text-align:center;font-size:0.72rem;color:#888'>have/tgt</div>", unsafe_allow_html=True)
    with ht:
        st.markdown("<div style='text-align:center;font-size:0.72rem;color:#888;padding-bottom:0'>⊖ target ⊕</div>", unsafe_allow_html=True)
    with hb:
        st.markdown("<div style='text-align:center;font-size:0.72rem;color:#888'>buy</div>", unsafe_allow_html=True)

    for idx in zone_data.index:
        item_name = zone_data.at[idx, COLUMNS["comida"]]
        current_qty = zone_data.at[idx, COLUMNS["tenemos"]]
        target_qty = zone_data.at[idx, COLUMNS["cantidad"]]
        buy_qty = zone_data.at[idx, COLUMNS["comprar"]]

        col1, col2, col3, col4, col5, col6, col7 = st.columns([4, 1, 1, 2, 1, 1, 2])
        with col1:
            st.markdown(f"**{item_name}**")
        with col2:
            if st.button("➖", key=f"audit_minus_{idx}", help="Decrease stock"):
                try:
                    df = update_item_quantity(df, idx, -1)
                except (SpreadsheetLockedError, InventoryFileError) as e:
                    render_save_error(e)
                else:
                    st.rerun()
        with col3:
            if st.button("➕", key=f"audit_plus_{idx}", help="Increase stock"):
                try:
                    df = update_item_quantity(df, idx, 1)
                except (SpreadsheetLockedError, InventoryFileError) as e:
                    render_save_error(e)
                else:
                    st.rerun()
        with col4:
            st.markdown(qty_html(current_qty, target_qty), unsafe_allow_html=True)
        with col5:
            if st.button("⊖", key=f"audit_target_minus_{idx}", help="Decrease target"):
                try:
                    df = update_target_quantity(df, idx, -1)
                except (SpreadsheetLockedError, InventoryFileError) as e:
                    render_save_error(e)
                else:
                    st.rerun()
        with col6:
            if st.button("⊕", key=f"audit_target_plus_{idx}", help="Increase target"):
                try:
                    df = update_target_quantity(df, idx, 1)
                except (SpreadsheetLockedError, InventoryFileError) as e:
                    render_save_error(e)
                else:
                    st.rerun()
        with col7:
            st.markdown(buy_html(buy_qty), unsafe_allow_html=True)

    return df
