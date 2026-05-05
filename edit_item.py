"""Edit Item mode — search for any item and edit all its fields, or delete it."""

import pandas as pd
import streamlit as st

from data import COLUMNS, save_inventory_data


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Render the edit-item interface for searching and editing individual items."""
    search_term = st.text_input(
        "🔍 Search",
        placeholder="Type item name...",
    ).strip().lower()

    if search_term:
        filtered_df = df[df[COLUMNS["comida"]].str.lower().str.contains(search_term, na=False)].copy()
    else:
        filtered_df = df.copy()
    filtered_df = filtered_df.sort_values(COLUMNS["comida"], key=lambda s: s.str.lower())

    if filtered_df.empty:
        st.info(f"No items found matching '{search_term}'" if search_term else "No items to display.")
        return df

    st.caption(f"{len(filtered_df)} item(s)")

    for idx in filtered_df.index:
        item_name = filtered_df.at[idx, COLUMNS["comida"]]

        with st.expander(f"🔧 {item_name}", expanded=len(filtered_df) == 1):
            with st.form(key=f"edit_form_{idx}"):
                col1, col2 = st.columns(2)

                with col1:
                    current_super = filtered_df.at[idx, COLUMNS["super"]]
                    new_super = st.text_input(
                        "🏪 Supermarket",
                        value=current_super if pd.notna(current_super) else "",
                    )

                    current_lugar = filtered_df.at[idx, COLUMNS["lugar"]]
                    new_lugar = st.text_input(
                        "🏠 Zone",
                        value=current_lugar if pd.notna(current_lugar) else "",
                    )

                    current_comida = filtered_df.at[idx, COLUMNS["comida"]]
                    new_comida = st.text_input(
                        "🥘 Item Name",
                        value=current_comida if pd.notna(current_comida) else "",
                    )

                with col2:
                    current_cantidad = int(filtered_df.at[idx, COLUMNS["cantidad"]])
                    new_cantidad = st.number_input("🎯 Target", value=current_cantidad, min_value=0, step=1)

                    current_tenemos = int(filtered_df.at[idx, COLUMNS["tenemos"]])
                    new_tenemos = st.number_input("📦 Current", value=current_tenemos, min_value=0, step=1)

                    current_buscador = filtered_df.at[idx, COLUMNS["buscador"]]
                    new_buscador = st.text_input(
                        "🔗 URL",
                        value=current_buscador if pd.notna(current_buscador) else "",
                    )

                col_btn1, col_btn2 = st.columns(2)
                with col_btn1:
                    save_clicked = st.form_submit_button("💾 Save", type="primary", width="stretch")
                with col_btn2:
                    delete_clicked = st.form_submit_button("🗑️ Delete", type="secondary", width="stretch")

                if save_clicked:
                    snap = df.loc[idx].copy()
                    df.at[idx, COLUMNS["super"]] = new_super
                    df.at[idx, COLUMNS["lugar"]] = new_lugar
                    df.at[idx, COLUMNS["comida"]] = new_comida
                    df.at[idx, COLUMNS["cantidad"]] = new_cantidad
                    df.at[idx, COLUMNS["tenemos"]] = new_tenemos
                    df.at[idx, COLUMNS["buscador"]] = new_buscador
                    df.at[idx, COLUMNS["comprar"]] = max(0, new_cantidad - new_tenemos)

                    if save_inventory_data(df):
                        st.success(f"✅ Saved '{new_comida}'")
                        st.rerun()
                    else:
                        df.loc[idx] = snap

                if delete_clicked:
                    backup = df.copy()
                    df = df.drop(idx)
                    if save_inventory_data(df):
                        st.session_state.inventory_data = df
                        st.success(f"✅ Deleted '{item_name}'")
                        st.rerun()
                    else:
                        return backup

    return df
