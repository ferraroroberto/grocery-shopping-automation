"""`cantidad`/`tenemos` coercion on load (issue #339).

A blank or non-numeric cell in either column is an ordinary result of editing
the sheet through "Open spreadsheet". Before this, `.astype(int)` on such a
column raised `pandas.errors.IntCastingNaNError`, which is neither
`SpreadsheetLockedError` nor `InventoryFileError`, so `load_inventory_or_error`
never translated it and every inventory endpoint returned a bare 500 with no
message. A blank cell now defaults to 0; a cell that holds text raises
`InventoryFileError` naming the column, caught and reported like every other
data-layer error.
"""

import pandas as pd
import pytest

import src.data as data
from src.data import COLUMNS, InventoryFileError, load_inventory_data


def _overwrite_cell(xlsx_path: str, col: str, value) -> None:
    df = pd.read_excel(xlsx_path, engine="openpyxl")
    df[col] = df[col].astype(object)  # a hand-typed cell is mixed-type, same as Excel would save it
    df.loc[0, col] = value
    df.to_excel(xlsx_path, index=False, engine="openpyxl")


def test_blank_quantity_cell_defaults_to_zero(temp_env):
    xlsx = data.CONFIG["data"]["xlsx_file"]
    _overwrite_cell(xlsx, COLUMNS["tenemos"], None)

    loaded = load_inventory_data()

    assert loaded.loc[0, COLUMNS["tenemos"]] == 0


def test_non_numeric_quantity_cell_raises_inventory_file_error(temp_env):
    xlsx = data.CONFIG["data"]["xlsx_file"]
    _overwrite_cell(xlsx, COLUMNS["cantidad"], "two")

    with pytest.raises(InventoryFileError, match=COLUMNS["cantidad"]):
        load_inventory_data()
