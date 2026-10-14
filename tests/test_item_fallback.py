"""The item sheet's fallback (#284): the record the PWA reads, the write behind
its form, and that a fallback saved there reaches the cart run (#283).

Fixture xlsx throwaway copy only -- no live store, never the real spreadsheet.
"""

from __future__ import annotations

import pandas as pd
import pytest

import src.data as data
from automation import grocery_reader
from automation.models import Fallback
from src.data import COLUMNS, FALLBACK_SUPER_COLUMN, FALLBACK_URL_COLUMN, fallback_pair
from tests.test_store_links import BURGUER, PAVO

CF_PRODUCT = "https://www.carrefour.es/supermercado/pavo-fileteado/R-FIX-pavo/p"
CF_SEARCH = "https://www.carrefour.es/search?q=pavo"
CF_NO_ID = "https://www.carrefour.es/supermercado/pavo-fileteado/p"


def _item(body: dict, item_id: int) -> dict:
    return next(i for i in body["items"] if i["id"] == item_id)


def _sheet() -> pd.DataFrame:
    return data.load_inventory_data()


def _put(client, item_id: int, store: str, url: str):
    return client.put(f"/api/items/{item_id}/fallback", json={"store": store, "url": url})


def test_a_sheet_without_the_columns_reads_no_fallback_and_gains_none_on_clear(client):
    assert FALLBACK_SUPER_COLUMN not in _sheet().columns
    assert _item(client.get("/api/inventory").json(), PAVO)["fallback"] is None

    assert _put(client, PAVO, "", "").status_code == 200
    # Clearing what was never set writes nothing, so no empty columns appear.
    assert FALLBACK_SUPER_COLUMN not in _sheet().columns and FALLBACK_URL_COLUMN not in _sheet().columns


def test_setting_a_fallback_writes_both_cells_and_clearing_blanks_both(client):
    resp = _put(client, PAVO, " Carrefour ", f" {CF_PRODUCT} ")
    assert resp.status_code == 200
    assert _item(resp.json(), PAVO)["fallback"] == {"store": "carrefour", "url": CF_PRODUCT, "kind": "product"}
    saved = _sheet().loc[PAVO]
    assert (saved[FALLBACK_SUPER_COLUMN], saved[FALLBACK_URL_COLUMN]) == ("carrefour", CF_PRODUCT)
    # Only that row: its neighbours' cells stay blank.
    assert _item(resp.json(), BURGUER)["fallback"] is None

    cleared = _put(client, PAVO, "", "")
    assert cleared.status_code == 200 and _item(cleared.json(), PAVO)["fallback"] is None
    saved = _sheet().loc[PAVO]
    assert data.cell_text(saved[FALLBACK_SUPER_COLUMN]) == "" and data.cell_text(saved[FALLBACK_URL_COLUMN]) == ""


def test_a_link_that_is_not_a_product_page_is_flagged_with_its_kind(client):
    search = _item(_put(client, PAVO, "carrefour", CF_SEARCH).json(), PAVO)["fallback"]
    no_id = _item(_put(client, PAVO, "carrefour", CF_NO_ID).json(), PAVO)["fallback"]
    # Saved as the user typed it, but never posed as a product link.
    assert (search["url"], search["kind"]) == (CF_SEARCH, "search")
    assert (no_id["url"], no_id["kind"]) == (CF_NO_ID, "suspect")


@pytest.mark.parametrize(
    ("store", "url", "why"),
    [
        ("carrefour", "", "both a store and a product link"),
        ("", CF_PRODUCT, "both a store and a product link"),
        ("nowhere", CF_PRODUCT, "unknown store"),
        ("carrefour", "carne de pavo", "http"),
    ],
)
def test_a_half_filled_or_invalid_fallback_is_rejected_and_writes_nothing(client, store, url, why):
    resp = _put(client, PAVO, store, url)
    assert resp.status_code == 400 and why in resp.json()["detail"]
    assert FALLBACK_SUPER_COLUMN not in _sheet().columns


def test_the_items_own_buy_link_is_not_a_fallback(client):
    row = _sheet().loc[PAVO]
    resp = _put(client, PAVO, row[COLUMNS["super"]], row[COLUMNS["buscador"]])
    assert resp.status_code == 400 and "own buy link" in resp.json()["detail"]


def test_an_unknown_item_is_404(client):
    assert _put(client, 99999, "carrefour", CF_PRODUCT).status_code == 404


def test_the_record_follows_the_same_rule_as_the_cart_run(client):
    """A cell the cart run would ignore must not show as a fallback in the sheet."""
    df = _sheet()
    df[FALLBACK_SUPER_COLUMN] = pd.Series([None] * len(df), index=df.index, dtype=object)
    df[FALLBACK_URL_COLUMN] = pd.Series([None] * len(df), index=df.index, dtype=object)
    df.loc[PAVO, [FALLBACK_SUPER_COLUMN, FALLBACK_URL_COLUMN]] = ["carrefour", "NOT A URL"]
    own = df.loc[BURGUER]
    df.loc[BURGUER, [FALLBACK_SUPER_COLUMN, FALLBACK_URL_COLUMN]] = [own[COLUMNS["super"]], own[COLUMNS["buscador"]]]
    data.save_inventory_data(df)

    body = client.get("/api/inventory").json()
    assert _item(body, PAVO)["fallback"] is None and _item(body, BURGUER)["fallback"] is None
    assert fallback_pair("carrefour", "NOT A URL", "ametller", "") is None


def test_the_cart_run_picks_up_a_fallback_saved_from_the_sheet(client):
    assert _put(client, PAVO, "Carrefour", CF_PRODUCT).status_code == 200

    items = {i.comida: i for i in grocery_reader.read_cart_items()}
    pavo = items["filete pavo"]
    assert pavo.fallback == Fallback("carrefour", CF_PRODUCT)
    # A row nobody set stays without one.
    assert items["burguer ternera"].fallback is None

    assert _put(client, PAVO, "", "").status_code == 200
    assert {i.comida: i for i in grocery_reader.read_cart_items()}["filete pavo"].fallback is None
