"""Save / Export mode — manual save, CSV download, and summary metrics."""

import pandas as pd
import streamlit as st

from app.ui_helpers import render_save_error
from src.data import (
    COLUMNS,
    InventoryFileError,
    SpreadsheetLockedError,
    save_inventory_data,
)


def main(df: pd.DataFrame) -> None:
    """Render the save/export interface."""
    col1, col2 = st.columns(2)

    with col1:
        if st.button("💾 Save to File", type="primary", width="stretch"):
            try:
                save_inventory_data(df)
            except (SpreadsheetLockedError, InventoryFileError) as e:
                render_save_error(e)
            else:
                st.success("✅ Saved!")

    with col2:
        st.download_button(
            "📥 Download CSV",
            df.to_csv(index=False),
            "inventory_updated.csv",
            "text/csv",
            width="stretch",
        )

    st.subheader("📊 Summary")
    total_items = len(df)
    stocked_items = len(df[df[COLUMNS["comprar"]] == 0])
    shopping_items = len(df[df[COLUMNS["comprar"]] > 0])

    c1, c2, c3 = st.columns(3)
    with c1:
        st.metric("Total", total_items)
    with c2:
        st.metric("Stocked", stocked_items)
    with c3:
        st.metric("Need Buy", shopping_items)
