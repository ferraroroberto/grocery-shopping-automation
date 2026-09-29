"""Per-store links, store picks and the cost simulator (issue #148).

Runs against the committed xlsx fixture (via ``temp_env``) and an invented
benchmark run under ``tests/fixtures/store_links/``: four items (burguer
ternera, dorada, filete pavo, guisantes congelados) priced at Mercadona,
Ametller and Carrefour, plus one mapping key no inventory row matches.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

import src.data as data
import src.store_links as store_links
from automation.grocery_reader import read_cart_items
from src.data import COLUMNS, SpreadsheetLockedError, load_inventory_data, store_url_columns

RUNS = Path(__file__).resolve().parent / "fixtures" / "store_links"
CF_BURGER_600 = "https://www.carrefour.es/supermercado/fixture-burger-600/R-FIX-burger-600/p"
CF_GUISANTES_1KG = "https://www.carrefour.es/supermercado/fixture-guisantes-1kg/R-FIX-guisantes-1kg/p"
BURGUER, DORADA, PAVO, GUISANTES, AMONIACO = 1, 2, 3, 4, 0
# Mapping URLs that land on a fixture row: carrefour ×3, mercadona ×2, ametller ×1.
MAPPING_SEEDS = 6


@pytest.fixture()
def runs(temp_env, monkeypatch):
    """Point the store-links layer at the fixture benchmark runs."""
    monkeypatch.setitem(data.CONFIG, "benchmark", {"runs_dir": str(RUNS)})
    return RUNS


@pytest.fixture()
def seeded(runs):
    """The fixture inventory after one import (persisted to the temp xlsx)."""
    store_links.import_latest(load_inventory_data())
    return load_inventory_data()


def _http_buscador_rows(df: pd.DataFrame) -> int:
    return int(df[COLUMNS["buscador"]].astype(str).str.lower().str.startswith("http").sum())


# ── Column-optional load ─────────────────────────────────────────────────────


def test_load_without_url_columns(temp_env):
    df = load_inventory_data()
    assert df is not None
    assert store_url_columns(df) == {}
    assert store_links.store_url(df, BURGUER, "carrefour") == ""


def test_load_with_url_columns_including_an_empty_one(temp_env):
    df = load_inventory_data()
    df["url_carrefour"] = None
    df.loc[BURGUER, "url_carrefour"] = CF_BURGER_600
    df["url_dia"] = None  # all-empty: reads back as float NaN
    data.save_inventory_data(df)

    df = load_inventory_data()
    assert df is not None
    assert store_url_columns(df) == {"carrefour": "url_carrefour", "dia": "url_dia"}
    assert store_links.store_url(df, BURGUER, "carrefour") == CF_BURGER_600
    assert store_links.store_url(df, BURGUER, "dia") == ""
    # An all-NaN column still accepts a URL write.
    store_links.set_store_url(df, BURGUER, "dia", "https://www.dia.es/fixture/p")
    assert store_links.store_url(load_inventory_data(), BURGUER, "dia") == "https://www.dia.es/fixture/p"


def test_latest_run_skips_unscored_runs(runs):
    assert store_links.latest_run_dir().name == "2026-01-15"
    assert store_links.latest_run_dir(runs / "_state") is None


# ── Import ───────────────────────────────────────────────────────────────────


def test_import_seeds_then_is_idempotent(runs):
    df = load_inventory_data()
    n_http = _http_buscador_rows(df)
    first = store_links.import_latest(df)
    assert first["seeded"] == n_http + MAPPING_SEEDS
    assert first["skipped_existing"] == 0
    assert first["unmatched"] == 1 and first["unmatched_keys"] == ["producto-inexistente"]
    assert first["stores"] == ["ametller", "carrefour", "mercadona"]
    # The ghost mapping's Carrefour URL has no /R-<id>/p product id.
    assert first["suspect_urls"] == {"carrefour": 1}

    df = load_inventory_data()
    # The bulk pack the benchmark priced wins over the mapping's main URL.
    assert df.at[BURGUER, "url_carrefour"] == CF_BURGER_600
    # buscador backfills the chosen store's column.
    assert df.at[BURGUER, "url_ametller"] == df.at[BURGUER, COLUMNS["buscador"]]
    assert "url_dia" not in df.columns  # no URL for dia → no column

    second = store_links.import_latest(df)
    assert second["seeded"] == 0
    assert second["skipped_existing"] == first["seeded"]
    assert load_inventory_data().equals(df)


def test_import_never_overwrites_a_user_url(runs):
    df = load_inventory_data()
    store_links.set_store_url(df, BURGUER, "carrefour", "https://www.carrefour.es/mine/p")
    counts = store_links.import_latest(load_inventory_data())
    assert counts["seeded"] == _http_buscador_rows(df) + MAPPING_SEEDS - 1
    assert counts["skipped_existing"] == 1
    assert load_inventory_data().at[BURGUER, "url_carrefour"] == "https://www.carrefour.es/mine/p"


def test_import_rolls_back_when_the_save_fails(runs, monkeypatch):
    def locked(*_a, **_k):
        raise SpreadsheetLockedError(data.SPREADSHEET_LOCKED_HINT)

    monkeypatch.setattr(store_links, "save_inventory_data", locked)
    df = load_inventory_data()
    before = df.copy()
    with pytest.raises(SpreadsheetLockedError):
        store_links.import_latest(df)
    assert df.equals(before)


# ── Pick / set URL ───────────────────────────────────────────────────────────


def test_pick_switches_store_and_cart_automation_follows(seeded):
    assert [i.comida for i in read_cart_items("carrefour")] == []
    store_links.pick_store(seeded, BURGUER, "carrefour")

    df = load_inventory_data()
    assert df.at[BURGUER, COLUMNS["super"]] == "carrefour"
    assert df.at[BURGUER, COLUMNS["buscador"]] == CF_BURGER_600
    cart = read_cart_items("carrefour")
    assert [(i.comida, i.buscador) for i in cart] == [("burguer ternera", CF_BURGER_600)]
    assert "burguer ternera" not in [i.comida for i in read_cart_items("ametller")]


def test_pick_without_a_url_is_refused(seeded):
    with pytest.raises(store_links.StoreLinkError, match="no carrefour URL"):
        store_links.pick_store(seeded, PAVO, "carrefour")
    assert load_inventory_data().at[PAVO, COLUMNS["super"]] == "ametller"


def test_pick_rolls_back_when_the_save_fails(seeded, monkeypatch):
    def locked(*_a, **_k):
        raise SpreadsheetLockedError(data.SPREADSHEET_LOCKED_HINT)

    monkeypatch.setattr(store_links, "save_inventory_data", locked)
    with pytest.raises(SpreadsheetLockedError):
        store_links.pick_store(seeded, BURGUER, "carrefour")
    assert seeded.at[BURGUER, COLUMNS["super"]] == "ametller"


def test_set_url_for_the_chosen_store_updates_buscador(seeded):
    store_links.set_store_url(seeded, BURGUER, "ametller", "https://www.ametllerorigen.com/es/new/p")
    df = load_inventory_data()
    assert df.at[BURGUER, COLUMNS["buscador"]] == "https://www.ametllerorigen.com/es/new/p"
    with pytest.raises(store_links.StoreLinkError):
        store_links.set_store_url(df, BURGUER, "carrefour", "not a url")
    with pytest.raises(store_links.StoreLinkError):
        store_links.set_store_url(df, BURGUER, "nosuchstore", "https://x.example/p")


# ── Simulation ───────────────────────────────────────────────────────────────


def test_simulate_prices_picks_against_today(seeded):
    picks = {str(BURGUER): "carrefour", str(PAVO): "carrefour", "dorada": "carrefour"}
    res = store_links.simulate(seeded, picks, store_links.FREQUENCIES["weekly"])

    assert res["run_date"] == "2026-01-15"
    today, picked = res["today"], res["picks"]
    # Today: 20 + 8 + 18 + 3.6 (pro-rata, basket quantities × today's prices).
    assert today["goods"] == 49.6 and today["unpriced"] == []
    # Picks: burger at Carrefour's 12 €/kg bulk pack (1.2 kg → 14.4), dorada
    # 4.0, guisantes stays at Mercadona 3.6; filete pavo has no Carrefour offer.
    assert picked["goods"] == 22.0
    assert picked["unpriced"] == [{"key": "filete-pavo", "store": "carrefour", "reason": "no_offer",
                                   "id": PAVO, "comida": "filete pavo"}]
    assert "filete-pavo" not in picked["items"]
    assert res["comparable"] is False
    assert picked["total"] == round(picked["goods"] + picked["delivery"], 2)
    assert picked["per_store"]["carrefour"]["orders"] == round(52 / 12, 2)
    assert res["delta"]["goods"] == round(22.0 - 49.6, 2)

    not_in = {r["id"] for r in res["not_in_benchmark"]}
    assert AMONIACO in not_in and not not_in & {BURGUER, DORADA, PAVO, GUISANTES}

    # Per-item monthly goods at every store that prices it (the low-confidence
    # Mercadona burger match is not a price).
    assert res["item_prices"][str(BURGUER)] == {"ametller": 20.0, "carrefour": 14.4}
    assert res["item_prices"][str(PAVO)] == {"ametller": 18.0, "mercadona": 16.5}
    assert str(AMONIACO) not in res["item_prices"]


def test_simulate_today_stays_at_the_benchmark_status_quo(seeded):
    # After a switch is applied, `super` holds the new store — but "today" is
    # the setup the benchmark measured, so the saving stays visible.
    store_links.pick_store(seeded, BURGUER, "carrefour")
    df = load_inventory_data()
    res = store_links.simulate(df, {}, store_links.FREQUENCIES["weekly"])
    assert res["today"]["items"]["burguer-ternera"] == "ametller"
    assert res["today"]["goods"] == 49.6
    # The what-if starts from the list as it is now.
    assert res["picks"]["items"]["burguer-ternera"] == "carrefour"
    assert res["delta"]["goods"] == round(14.4 - 20.0, 2)


def test_simulate_leaves_target_zero_items_out_of_both_sides(seeded):
    # #178: an item at target 0 is not bought, so neither side may price it.
    weekly = store_links.FREQUENCIES["weekly"]
    before = store_links.simulate(seeded, {}, weekly)
    assert before["excluded"] == []
    seeded.loc[GUISANTES, "cantidad"] = 0
    res = store_links.simulate(seeded, {str(GUISANTES): "carrefour"}, weekly)
    assert res["excluded"] == [{"id": GUISANTES, "comida": "guisantes congelados",
                                "key": "guisantes-congelados", "store": "mercadona"}]
    for side in ("picks", "today"):
        assert "guisantes-congelados" not in res[side]["items"]
        assert res[side]["goods"] == round(before[side]["goods"] - 3.6, 2)  # its Mercadona 3.6 €/month
    assert res["comparable"] is True
    # Its per-item price is still shown for review.
    assert str(GUISANTES) in res["item_prices"]


def test_simulate_store_kept_only_by_target_zero_items_costs_nothing(seeded):
    # The live bug: a store whose only basket item has target 0 still got a
    # delivery fee. Burger and filete pavo are the basket's Ametller items.
    for row in (BURGUER, PAVO):
        seeded.loc[row, "cantidad"] = 0
    res = store_links.simulate(seeded, {}, store_links.FREQUENCIES["weekly"])
    assert "ametller" not in res["picks"]["per_store"]
    assert "ametller" not in res["today"]["per_store"]


def test_simulate_low_confidence_offer_is_unpriced_not_free(seeded):
    res = store_links.simulate(seeded, {str(BURGUER): "mercadona"}, 1.0)
    assert res["picks"]["unpriced"] == [{"key": "burguer-ternera", "store": "mercadona", "reason": "no_offer",
                                         "id": BURGUER, "comida": "burguer ternera"}]
    assert res["picks"]["per_store"]["ametller"]["orders"] == 1.0


def test_simulate_unknown_item_is_an_error(seeded):
    with pytest.raises(store_links.StoreLinkError):
        store_links.simulate(seeded, {"no-such-item": "carrefour"}, 4.0)


def test_recommended_picks(runs):
    rec = store_links.recommended_picks()
    assert rec["stores"] == ["ametller", "carrefour"]
    assert rec["picks"]["burguer-ternera"] == "carrefour"
    assert rec["total_optimised"] == 57.1


# ── Apply ────────────────────────────────────────────────────────────────────


def test_apply_preview_converts_pack_sizes(seeded):
    picks = {str(BURGUER): "carrefour", str(DORADA): "carrefour", str(GUISANTES): "carrefour",
             str(PAVO): "carrefour", str(AMONIACO): "mercadona"}  # amoniaco: no change
    preview = {c["id"]: c for c in store_links.apply_preview(seeded, picks)}
    assert set(preview) == {BURGUER, DORADA, GUISANTES, PAVO}

    burger = preview[BURGUER]
    assert (burger["from"], burger["to"]) == ("ametller", "carrefour")
    assert burger["old_pack"] == {"size": 0.3, "unit": "kg"}
    assert burger["new_pack"] == {"size": 0.6, "unit": "kg"}
    assert (burger["old_cantidad"], burger["cantidad"]) == (3, 2)  # ceil(3 × 0.3 / 0.6)
    assert burger["url"] == CF_BURGER_600 and burger["flags"] == []

    assert preview[GUISANTES]["cantidad"] == 1  # ceil(2 × 0.3 / 1.0)
    assert preview[GUISANTES]["url"] == CF_GUISANTES_1KG
    assert preview[DORADA]["flags"] == ["unit_mismatch"]
    assert preview[DORADA]["cantidad"] == preview[DORADA]["old_cantidad"]
    assert preview[PAVO]["flags"] == ["no_url", "pack_unknown"] and preview[PAVO]["url"] is None


def test_apply_changes_sets_store_url_and_target(seeded):
    store_links.apply_changes(seeded, [{"row": BURGUER, "store": "carrefour", "cantidad": 2}])
    df = load_inventory_data()
    assert df.at[BURGUER, COLUMNS["super"]] == "carrefour"
    assert df.at[BURGUER, COLUMNS["buscador"]] == CF_BURGER_600
    assert df.at[BURGUER, COLUMNS["cantidad"]] == 2
    assert df.at[BURGUER, COLUMNS["comprar"]] == 0  # tenemos is 2


def test_apply_changes_validates_the_whole_batch_first(seeded):
    with pytest.raises(store_links.StoreLinkError):
        store_links.apply_changes(seeded, [
            {"row": BURGUER, "store": "carrefour", "cantidad": 2},
            {"row": PAVO, "store": "carrefour", "cantidad": 1},  # no Carrefour URL
        ])
    assert load_inventory_data().at[BURGUER, COLUMNS["super"]] == "ametller"


@pytest.mark.parametrize(
    ("store", "url", "kind"),
    [
        ("bonpreu", "https://www.compraonline.bonpreuesclat.cat/products/search?q=oli+oliva+verge+extra", "search"),
        ("alcampo", "https://www.compraonline.alcampo.es/search?q=aceite%20oliva", "search"),
        ("carrefour", "https://www.carrefour.es/supermercado/copos-de-avena-carrefour-500-g/p", "suspect"),
        ("carrefour", "https://www.carrefour.es/supermercado/x/R-VC4AECOMM-081271/p", "product"),
        ("mercadona", "https://tienda.mercadona.es/product/5507/arandanos-tarrina", "product"),
        ("condis", "https://compraonline.condis.es/aceite-oliv-condis-virgen-extra-3-l/p/800468/es_ES", "product"),
    ],
)
def test_link_kind(store, url, kind):
    assert store_links.link_kind(store, url) == kind
