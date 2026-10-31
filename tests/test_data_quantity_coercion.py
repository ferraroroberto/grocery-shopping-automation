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
from src.data import (
    COLUMNS,
    InventoryFileError,
    SpreadsheetLockedError,
    comprar_for,
    load_inventory_data,
    recompute_comprar,
    rollback_on_save_failure,
)


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


def test_comprar_is_never_negative():
    assert comprar_for(3, 1) == 2
    assert comprar_for(1, 4) == 0


def _one_row_frame() -> pd.DataFrame:
    return pd.DataFrame([{COLUMNS["cantidad"]: 4, COLUMNS["tenemos"]: 1, COLUMNS["comprar"]: 3}])


def test_rollback_on_save_failure_restores_cells_and_reraises():
    df = _one_row_frame()
    with pytest.raises(SpreadsheetLockedError):
        with rollback_on_save_failure(df, [0], (COLUMNS["tenemos"], COLUMNS["comprar"])):
            df.at[0, COLUMNS["tenemos"]] = 4
            recompute_comprar(df, 0)
            assert df.at[0, COLUMNS["comprar"]] == 0
            raise SpreadsheetLockedError("open in Excel")
    assert (df.at[0, COLUMNS["tenemos"]], df.at[0, COLUMNS["comprar"]]) == (1, 3)


def test_rollback_on_save_failure_keeps_edits_on_success():
    df = _one_row_frame()
    with rollback_on_save_failure(df, [0], (COLUMNS["tenemos"], COLUMNS["comprar"])):
        df.at[0, COLUMNS["tenemos"]] = 2
        recompute_comprar(df, 0)
    assert (df.at[0, COLUMNS["tenemos"]], df.at[0, COLUMNS["comprar"]]) == (2, 2)
