"""Units per pack: the household counts units, the cart buys store packs.

Fixture xlsx throwaway copy only -- no live store, never the real spreadsheet.
"""

from __future__ import annotations

import pandas as pd
import pytest

import src.data as data
from automation.grocery_reader import read_cart_items
from src.data import COLUMNS, UNITS_PER_PACK_COLUMN, packs_for, units_per_pack_of
from tests.test_store_links import PAVO


def _item(body: dict, item_id: int = PAVO) -> dict:
    return next(i for i in body["items"] if i["id"] == item_id)


def _name(client) -> str:
    return _item(client.get("/api/inventory").json())[COLUMNS["comida"]]


def _put(client, **changes):
    """PUT the PAVO row as the sheet would, with `changes` over its current cells."""
    row = _item(client.get("/api/inventory").json())
    body = {
        "super": row[COLUMNS["super"]], "lugar": row[COLUMNS["lugar"]], "comida": row[COLUMNS["comida"]],
        "cantidad": row[COLUMNS["cantidad"]], "tenemos": row[COLUMNS["tenemos"]],
        "buscador": row[COLUMNS["buscador"]] or "",
    }
    return client.put(f"/api/items/{PAVO}", json={**body, **changes})


def _cart_quantity(name: str) -> int | None:
    """What the cart run would send for `name`, or None when it is not pending."""
    return next((i.comprar for i in read_cart_items() if i.comida == name), None)


@pytest.mark.parametrize(
    ("units", "per_pack", "packs"),
    [
        (14, 5, 3),      # the reported case: 14 apples at ~5 per 1 kg bag
        (0, 5, 0),       # nothing needed buys nothing
        (1, 5, 1),       # any shortfall buys a whole pack
        (5, 5, 1),
        (6, 5, 2),
        (3, 0.1, 30),    # 3 / 0.1 is 30.000000000000004 in floats
        (7, 2.5, 3),
        (4, None, 4),    # no conversion: units pass through
    ],
)
def test_packs_cover_the_needed_units_rounded_up(units, per_pack, packs):
    assert packs_for(units, per_pack) == packs


@pytest.mark.parametrize("cell", [None, float("nan"), "", "abc", 0, -2, float("inf")])
def test_an_unset_or_unusable_cell_reads_as_no_conversion(cell):
    assert units_per_pack_of(cell) is None


def test_a_numeric_cell_reads_as_its_value():
    assert units_per_pack_of(5) == 5.0
    assert units_per_pack_of("2.5") == 2.5


def test_a_sheet_without_the_column_sends_units_unchanged(client):
    assert UNITS_PER_PACK_COLUMN not in data.load_inventory_data().columns
    assert _put(client, cantidad=14, tenemos=0).status_code == 200
    assert _cart_quantity(_name(client)) == 14
    # Saving without the field never adds the column to the sheet.
    assert UNITS_PER_PACK_COLUMN not in data.load_inventory_data().columns


def test_the_cart_run_buys_packs_not_units(client):
    name = _name(client)
    assert _put(client, cantidad=14, tenemos=0, units_per_pack=5).status_code == 200
    assert _cart_quantity(name) == 3
    # One unit short still buys a pack; fully stocked is not pending at all.
    assert _put(client, cantidad=14, tenemos=13, units_per_pack=5).status_code == 200
    assert _cart_quantity(name) == 1
    assert _put(client, cantidad=14, tenemos=14, units_per_pack=5).status_code == 200
    assert _cart_quantity(name) is None


def test_the_value_survives_a_reload_and_the_payload_shows_units_and_packs(client):
    body = _put(client, cantidad=14, tenemos=0, units_per_pack=5).json()
    row = _item(body)
    assert (row[COLUMNS["comprar"]], row["units_per_pack"], row["packs"]) == (14, 5.0, 3)
    assert data.load_inventory_data().loc[PAVO][UNITS_PER_PACK_COLUMN] == 5.0
    # Other rows keep an empty cell and report no conversion.
    other = next(i for i in body["items"] if i["id"] != PAVO)
    assert other["units_per_pack"] is None and other["packs"] == other[COLUMNS["comprar"]]


def test_a_save_that_omits_the_field_keeps_it_and_null_clears_it(client):
    _put(client, units_per_pack=4)
    assert _item(_put(client, cantidad=9).json())["units_per_pack"] == 4.0
    assert _item(_put(client, units_per_pack=None).json())["units_per_pack"] is None
    assert pd.isna(data.load_inventory_data().loc[PAVO][UNITS_PER_PACK_COLUMN])


@pytest.mark.parametrize("bad", [0, -1])
def test_a_non_positive_value_is_rejected(client, bad):
    assert _put(client, units_per_pack=bad).status_code == 422
    assert UNITS_PER_PACK_COLUMN not in data.load_inventory_data().columns


def test_an_added_item_carries_its_conversion(client):
    new = {"super": "mercadona", "lugar": "nevera", "comida": "manzana test", "cantidad": 14, "tenemos": 0,
           "buscador": "", "units_per_pack": 5}
    assert client.post("/api/items", json=new).status_code == 200
    assert _cart_quantity("manzana test") == 3
