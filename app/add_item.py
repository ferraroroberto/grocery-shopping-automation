"""Add Item mode — form-based creation of new inventory items."""

import pandas as pd
import streamlit as st

from app.ui_helpers import render_save_error
from src.data import (
    InventoryFileError,
    SpreadsheetLockedError,
    build_new_item_row,
    get_unique_supermarkets,
    get_unique_zones,
    save_inventory_data,
)


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Render the add-item interface for creating new inventory items."""
    existing_supermarkets = get_unique_supermarkets(df)
    existing_zones = get_unique_zones(df)

    with st.form(key="add_item_form"):
        col1, col2 = st.columns(2)

        with col1:
            new_super = st.selectbox("🏪 Supermarket", options=existing_supermarkets, key="add_item_supermarket")
            new_lugar = st.selectbox("🏠 Zone", options=existing_zones, key="add_item_zone")
            new_comida = st.text_input("🥘 Item Name")

        with col2:
            new_cantidad = st.number_input("🎯 Target", value=0, min_value=0, step=1)
            new_tenemos = st.number_input("📦 Current", value=0, min_value=0, step=1)
            new_buscador = st.text_input("🔗 URL")

        if st.form_submit_button("➕ Add Item", type="primary", width="stretch"):
            if not new_comida.strip():
                st.error("❌ Item name is required!")
            else:
                new_row = build_new_item_row(
                    super_value=new_super,
                    lugar=new_lugar,
                    comida=new_comida,
                    cantidad=new_cantidad,
                    tenemos=new_tenemos,
                    buscador=new_buscador,
                )
                df_extended = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

                try:
                    save_inventory_data(df_extended)
                except (SpreadsheetLockedError, InventoryFileError) as e:
                    render_save_error(e)
                else:
                    st.session_state.inventory_data = df_extended
                    st.success(f"✅ Added '{new_comida}'")
                    st.rerun()

    return df
