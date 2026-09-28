"""Per-item store review: overrides, item detail, checks, stock conversion (#165).

Same data as ``test_store_links.py`` (the committed xlsx fixture and the
invented benchmark runs), but on a ``tmp_path`` copy of the runs directory so
tests that write ``_state/overrides.json`` never touch ``tests/fixtures/``.
"""

from __future__ import annotations

import json
import shutil
from datetime import date
from pathlib import Path

import pytest

import src.data as data
import src.store_links as store_links
from src.data import COLUMNS, load_inventory_data
from tests.test_store_links import AMONIACO, BURGUER, CF_BURGER_600, DORADA, PAVO, RUNS


@pytest.fixture()
def runs_copy(temp_env, monkeypatch, tmp_path: Path) -> Path:
    """A writable copy of the fixture runs, set as ``benchmark.runs_dir``."""
    dst = tmp_path / "runs"
    shutil.copytree(RUNS, dst)
    monkeypatch.setitem(data.CONFIG, "benchmark", {"runs_dir": str(dst)})
    return dst


@pytest.fixture()
def seeded_copy(runs_copy):
    store_links.import_latest(load_inventory_data())
    return load_inventory_data()


@pytest.fixture()
def api(client, runs_copy):
    """A TestClient on the fixture xlsx, pricing from a writable copy of the fixture runs."""
    client.post("/api/stores/import-latest")
    return client


def _write_overrides(runs: Path, doc: dict) -> None:
    (runs / "_state" / "overrides.json").write_text(json.dumps(doc), encoding="utf-8")


def _read_overrides(runs: Path) -> dict:
    return json.loads((runs / "_state" / "overrides.json").read_text(encoding="utf-8"))


def _store(detail: dict, store: str) -> dict:
    return next(s for s in detail["stores"] if s["store"] == store)


# ── Overrides → pricing ──────────────────────────────────────────────────────


def test_override_changes_simulate_and_item_prices(seeded_copy, runs_copy):
    """Regression (#165): an override is priced by the simulator and the per-item chips."""
    # Carrefour's burger offer is the 0.6 kg bulk pack at 7.20 € → 1.2 kg/month = 14.40 €.
    _write_overrides(runs_copy, {"items": {"burguer-ternera": {"carrefour": {"pack_price": 6.0}}}})
    res = store_links.simulate(seeded_copy, {str(BURGUER): "carrefour"}, 1.0)
    assert res["item_prices"][str(BURGUER)]["carrefour"] == 12.0  # 1.2 / 0.6 × 6.0
    assert res["picks"]["goods"] == round(49.6 - 20.0 + 12.0, 2)


def test_override_at_a_store_without_an_offer_is_priced(seeded_copy, runs_copy):
    # Carrefour has no filete-pavo (not_found); a full override prices it.
    _write_overrides(runs_copy, {"items": {"filete-pavo": {"carrefour": {"pack_size": 1.0, "pack_price": 10.0}}}})
    res = store_links.simulate(seeded_copy, {str(PAVO): "carrefour"}, 1.0)
    assert res["item_prices"][str(PAVO)]["carrefour"] == 15.0  # 1.5 kg × 10 €/kg
    assert res["picks"]["unpriced"] == []


def test_override_pack_drives_apply_preview(seeded_copy, runs_copy):
    _write_overrides(runs_copy, {"items": {"burguer-ternera": {"carrefour": {
        "name": "Burger 900 g", "pack_size": 0.9, "pack_price": 9.0}}}})
    [change] = store_links.apply_preview(seeded_copy, {str(BURGUER): "carrefour"})
    assert change["new_pack"] == {"size": 0.9, "unit": "kg"}  # unit falls back to the benchmark's
    assert change["new_name"] == "Burger 900 g"  # your name wins over the link's
    assert (change["old_cantidad"], change["cantidad"]) == (3, 1)  # ceil(3 × 0.3 / 0.9)
    assert (change["tenemos_from"], change["tenemos"]) == (2, 1)  # round(2 × 0.3 / 0.9 = 0.67)


# ── Stock conversion on apply (#160) ────────────────────────────────────────


def test_apply_preview_converts_stock(seeded_copy):
    preview = {c["id"]: c for c in store_links.apply_preview(
        seeded_copy, {str(BURGUER): "carrefour", str(DORADA): "carrefour"})}
    assert (preview[BURGUER]["tenemos_from"], preview[BURGUER]["tenemos"]) == (2, 1)  # 2 × 0.3 / 0.6
    # The products behind the old and new links, for the review dialog.
    assert (preview[BURGUER]["old_name"], preview[BURGUER]["new_name"]) == (
        "Fixture burguer ternera", "Fixture R-FIX-burger-600")
    # No conversion when the target isn't converted (kg → ud).
    assert preview[DORADA]["flags"] == ["unit_mismatch"]
    assert preview[DORADA]["tenemos"] == preview[DORADA]["tenemos_from"]


def test_apply_changes_writes_converted_stock(seeded_copy):
    store_links.apply_changes(seeded_copy, [{"row": BURGUER, "store": "carrefour", "cantidad": 2, "tenemos": 1}])
    df = load_inventory_data()
    assert (df.at[BURGUER, COLUMNS["cantidad"]], df.at[BURGUER, COLUMNS["tenemos"]]) == (2, 1)
    assert df.at[BURGUER, COLUMNS["comprar"]] == 1


def test_apply_changes_rejects_negative_stock(seeded_copy):
    with pytest.raises(store_links.StoreLinkError, match="tenemos"):
        store_links.apply_changes(seeded_copy, [{"row": BURGUER, "store": "carrefour", "cantidad": 2,
                                                 "tenemos": -1}])
    assert load_inventory_data().at[BURGUER, COLUMNS["super"]] == "ametller"


# ── Item detail ──────────────────────────────────────────────────────────────


def test_item_detail_shape(seeded_copy):
    d = store_links.item_detail(seeded_copy, BURGUER)
    assert (d["id"], d["key"], d["in_basket"], d["run_date"]) == (BURGUER, "burguer-ternera", True, "2026-01-15")
    assert d["list"]["store"] == "ametller" and (d["list"]["cantidad"], d["list"]["tenemos"]) == (3, 2)
    assert d["list"]["unidades"] == 220.0
    assert d["list"]["urls"]["carrefour"] == CF_BURGER_600 and d["list"]["url_kinds"]["carrefour"] == "product"
    assert d["before"] == {"store": "ametller", "name": "Fixture burguer ternera",
                           "url": "https://ametller.example/burguer-ternera", "pack_size": 0.3, "unit": "kg",
                           "pack_price": 5.0, "unit_price": 16.67, "target": 2, "monthly_packs": 4}
    assert d["monthly"] == {"qty": 1.2, "unit": "kg", "packs_at_list_store": 4.0}
    assert d["tier"] == "B" and d["spec"] is None

    assert [s["store"] for s in d["stores"]] == ["ametller", "carrefour", "mercadona"]
    cf = _store(d, "carrefour")
    assert (cf["name"], cf["pack_size"], cf["unit"], cf["pack_price"]) == ("Fixture R-FIX-burger-600", 0.6, "kg", 7.2)
    assert (cf["price_per_unit"], cf["monthly_cost"], cf["status"], cf["source"]) == (12.0, 14.4, "equivalent",
                                                                                      "benchmark")
    assert (cf["confidence"], cf["evidence"], cf["url"]) == ("high", "invented fixture record", CF_BURGER_600)
    assert cf["override"] is None and cf["benchmark_changed"] is False
    am = _store(d, "ametller")
    assert am["is_list_store"] and am["is_basket_store"] and am["status"] == "baseline"
    # The low-confidence Mercadona match is shown, but not priced.
    me = _store(d, "mercadona")
    assert (me["status"], me["priced"], me["monthly_cost"]) == ("unverified", False, None)

    q = d["quantity"]
    assert q["before"] == {"store": "ametller", "packs": 2, "pack_size": 0.3, "unit": "kg", "total": 0.6}
    assert q["now"] == {"store": "ametller", "packs": 3, "pack_size": 0.3, "unit": "kg", "total": 0.9,
                        "pack_source": "link"}
    assert (q["pack_ratio"], q["delta_pct"]) == (1.0, 50.0)
    assert d["flags"] == [] and d["checked"] is None


def test_item_detail_after_a_move(seeded_copy):
    store_links.pick_store(seeded_copy, BURGUER, "carrefour")
    d = store_links.item_detail(load_inventory_data(), BURGUER)
    q = d["quantity"]
    # Before: 2 × 0.3 kg at Ametller · Now: 3 × 0.6 kg at Carrefour (target not converted).
    assert q["now"] == {"store": "carrefour", "packs": 3, "pack_size": 0.6, "unit": "kg", "total": 1.8,
                        "pack_source": "link"}
    assert (q["pack_ratio"], q["delta_pct"]) == (2.0, 200.0)
    assert q["suggested"] == {"cantidad": 1, "tenemos": 1}
    assert d["flags"] == ["moved", "pack_x2", "stock_unconverted"]


def test_item_detail_outside_the_basket(seeded_copy):
    d = store_links.item_detail(seeded_copy, AMONIACO)
    assert (d["in_basket"], d["before"], d["monthly"]) == (False, None, None)
    assert [(s["store"], s["source"]) for s in d["stores"]] == [("mercadona", "link-only")]
    with pytest.raises(store_links.StoreLinkError):
        store_links.item_detail(seeded_copy, 99999)


# ── Override set / reset ─────────────────────────────────────────────────────


def test_set_and_reset_override(seeded_copy, runs_copy):
    d = store_links.set_override(seeded_copy, BURGUER, "carrefour", pack_price=6.0, note="checked the shelf")
    cf = _store(d, "carrefour")
    assert (cf["source"], cf["status"], cf["pack_price"], cf["pack_size"], cf["monthly_cost"]) == (
        "override", "override", 6.0, 0.6, 12.0)
    assert cf["benchmark"] == {"name": "Fixture R-FIX-burger-600", "pack_size": 0.6, "unit": "kg", "pack_price": 7.2}
    assert cf["override"]["note"] == "checked the shelf" and cf["benchmark_changed"] is False
    assert "override" in d["flags"]

    saved = _read_overrides(runs_copy)["items"]["burguer-ternera"]["carrefour"]
    assert saved["pack_price"] == 6.0 and saved["pack_size"] is None
    assert saved["benchmark_snapshot"] == cf["benchmark"]

    d = store_links.reset_override(seeded_copy, BURGUER, "carrefour")
    assert _store(d, "carrefour")["source"] == "benchmark" and "override" not in d["flags"]
    assert _read_overrides(runs_copy)["items"] == {}


def test_override_flags_a_changed_benchmark(seeded_copy, runs_copy):
    snapshot = {"name": "Fixture R-FIX-burger-600", "pack_size": 0.6, "unit": "kg", "pack_price": 6.5}
    _write_overrides(runs_copy, {"items": {"burguer-ternera": {"carrefour": {
        "pack_price": 6.0, "benchmark_snapshot": snapshot}}}})
    d = store_links.item_detail(seeded_copy, BURGUER)
    assert _store(d, "carrefour")["benchmark_changed"] is True
    assert d["flags"] == ["override", "benchmark_changed"]


@pytest.mark.parametrize(
    ("row", "kwargs", "match"),
    [
        (BURGUER, {"store": "carrefour", "pack_size": 0}, "pack_size"),
        (BURGUER, {"store": "carrefour", "pack_price": -1}, "pack_price"),
        (BURGUER, {"store": "carrefour", "pack_size": 1, "unit": "lb"}, "unit"),
        (BURGUER, {"store": "carrefour", "note": "only a note"}, "at least"),
        (BURGUER, {"store": "nosuchstore", "pack_price": 1}, "unknown store"),
        (AMONIACO, {"store": "mercadona", "pack_price": 1}, "not in the"),
    ],
)
def test_set_override_validation(seeded_copy, runs_copy, row, kwargs, match):
    store = kwargs.pop("store")
    with pytest.raises(store_links.StoreLinkError, match=match):
        store_links.set_override(seeded_copy, row, store, **kwargs)
    assert not (runs_copy / "_state" / "overrides.json").exists()


# ── Checks + checked ─────────────────────────────────────────────────────────


def test_checks_flags_moves_links_and_overrides(seeded_copy, runs_copy):
    df = seeded_copy
    store_links.pick_store(df, BURGUER, "carrefour")
    store_links.set_store_url(df, PAVO, "ametller", "https://www.ametllerorigen.com/es/search?q=pavo")
    # A non-basket row still gets link flags: an id-less Carrefour URL.
    store_links.set_store_url(df, AMONIACO, "carrefour", "https://www.carrefour.es/supermercado/amoniaco/p")
    store_links.pick_store(df, AMONIACO, "carrefour")
    store_links.set_override(df, DORADA, "carrefour", pack_size=0.4, unit="kg")

    res = store_links.checks(load_inventory_data())
    assert res["run_date"] == "2026-01-15"
    assert res["checks"] == {
        str(BURGUER): ["moved", "pack_x2", "stock_unconverted"],
        str(PAVO): ["search_link"],
        str(AMONIACO): ["suspect_link"],
        str(DORADA): ["override"],
    }
    assert res["counts"]["moved"] == 1 and res["counts"]["flagged"] == 4
    assert res["counts"]["needs_checking"] == 4 and res["counts"]["checked"] == 0

    assert store_links.set_item_checked(df, BURGUER, True) == {"key": "burguer-ternera",
                                                              "checked": date.today().isoformat()}
    res = store_links.checks(load_inventory_data())
    # Checked: stock is taken as reviewed, the other flags stay visible.
    assert res["checks"][str(BURGUER)] == ["moved", "pack_x2"]
    assert res["checked"] == {str(BURGUER): date.today().isoformat()}
    assert (res["counts"]["needs_checking"], res["counts"]["checked"]) == (3, 1)

    assert store_links.set_item_checked(df, BURGUER, False)["checked"] is None
    assert store_links.checks(load_inventory_data())["checked"] == {}


def test_corrupt_overrides_file_is_tolerated_and_set_aside(seeded_copy, runs_copy):
    path = runs_copy / "_state" / "overrides.json"
    path.write_text("{not json", encoding="utf-8")
    assert store_links.load_overrides() == {"items": {}, "checked": {}}
    res = store_links.simulate(seeded_copy, {str(BURGUER): "carrefour"}, 1.0)
    assert res["item_prices"][str(BURGUER)]["carrefour"] == 14.4
    assert store_links.checks(seeded_copy)["checks"] == {}

    store_links.set_item_checked(seeded_copy, BURGUER, True)
    assert _read_overrides(runs_copy)["checked"] == {"burguer-ternera": date.today().isoformat()}
    [aside] = list(path.parent.glob("overrides.corrupt-*.json"))
    assert aside.read_text(encoding="utf-8") == "{not json"
    assert not list(path.parent.glob(".overrides.*.tmp"))


def test_run_status(runs_copy):
    _write_overrides(runs_copy, {"items": {"dorada": {"carrefour": {"pack_price": 1}, "ametller": {"pack_price": 2}}}})
    status = store_links.run_status()
    assert status["run_date"] == "2026-01-15" and status["next_due"] == "2026-04-15"
    assert status["age_days"] == (date.today() - date(2026, 1, 15)).days
    assert status["stores_covered"] == ["ametller", "carrefour", "mercadona"]
    assert status["overrides"] == 2


# ── API ──────────────────────────────────────────────────────────────────────


def test_api_store_detail(api):
    resp = api.get(f"/api/items/{BURGUER}/store-detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["key"] == "burguer-ternera" and _store(body, "carrefour")["monthly_cost"] == 14.4
    assert api.get("/api/items/99999/store-detail").status_code == 404


def test_api_override_moves_the_simulator_then_resets(api, runs_copy):
    def carrefour_price() -> float:
        body = api.post("/api/stores/simulate", json={"picks": {str(BURGUER): "carrefour"}, "frequency": "monthly"})
        return body.json()["item_prices"][str(BURGUER)]["carrefour"]

    assert carrefour_price() == 14.4
    resp = api.put(f"/api/items/{BURGUER}/store-override",
                   json={"store": "carrefour", "pack_size": 1.0, "unit": "kg", "pack_price": 10.0})
    assert resp.status_code == 200
    assert _store(resp.json(), "carrefour")["source"] == "override"
    assert carrefour_price() == 12.0  # 1.2 kg × 10 €/kg
    assert "burguer-ternera" in _read_overrides(runs_copy)["items"]

    resp = api.delete(f"/api/items/{BURGUER}/store-override", params={"store": "carrefour"})
    assert resp.status_code == 200 and _store(resp.json(), "carrefour")["source"] == "benchmark"
    assert carrefour_price() == 14.4


@pytest.mark.parametrize(
    "body",
    [
        {"store": "carrefour", "pack_size": 0},
        {"store": "carrefour", "pack_size": -1},
        {"store": "carrefour", "pack_price": -0.01},
        {"store": "carrefour", "pack_size": 1, "unit": "lb"},
        {"store": "carrefour"},
        {"store": "nosuchstore", "pack_price": 1},
    ],
)
def test_api_override_validation_is_400(api, body):
    assert api.put(f"/api/items/{BURGUER}/store-override", json=body).status_code == 400


def test_api_override_item_errors(api):
    not_in_basket = api.put(f"/api/items/{AMONIACO}/store-override", json={"store": "mercadona", "pack_price": 1})
    assert not_in_basket.status_code == 400 and "basket" in not_in_basket.json()["detail"]
    assert api.put("/api/items/99999/store-override", json={"store": "carrefour", "pack_price": 1}).status_code == 404
    assert api.delete("/api/items/99999/store-override", params={"store": "carrefour"}).status_code == 404


def test_api_checked_round_trip_and_checks(api):
    api.post(f"/api/items/{BURGUER}/pick", json={"store": "carrefour"})
    checks = api.get("/api/stores/checks").json()
    assert checks["checks"] == {str(BURGUER): ["moved", "pack_x2", "stock_unconverted"]}
    assert checks["counts"]["needs_checking"] == 1

    resp = api.put(f"/api/items/{BURGUER}/checked", json={"checked": True})
    assert resp.status_code == 200
    assert resp.json() == {"key": "burguer-ternera", "checked": date.today().isoformat()}
    checks = api.get("/api/stores/checks").json()
    assert checks["checked"] == {str(BURGUER): date.today().isoformat()}
    assert checks["counts"]["needs_checking"] == 0
    assert api.get(f"/api/items/{BURGUER}/store-detail").json()["checked"] == date.today().isoformat()

    assert api.put(f"/api/items/{BURGUER}/checked", json={"checked": False}).json()["checked"] is None
    assert api.put("/api/items/99999/checked", json={"checked": True}).status_code == 404


def test_api_stores_status_fields(api):
    api.put(f"/api/items/{BURGUER}/store-override", json={"store": "carrefour", "pack_price": 6.0})
    body = api.get("/api/stores").json()
    assert body["run_date"] == "2026-01-15" and body["next_due"] == "2026-04-15"
    assert body["age_days"] == (date.today() - date(2026, 1, 15)).days
    assert body["stores_covered"] == ["ametller", "carrefour", "mercadona"]
    assert body["overrides"] == 1


def test_api_apply_with_stock(api):
    [change] = api.post("/api/stores/apply-preview", json={"picks": {str(BURGUER): "carrefour"}}).json()["changes"]
    assert (change["tenemos_from"], change["tenemos"]) == (2, 1)
    resp = api.post("/api/stores/apply", json={"changes": [
        {"id": BURGUER, "store": "carrefour", "cantidad": change["cantidad"], "tenemos": change["tenemos"]}]})
    assert resp.status_code == 200
    row = next(i for i in resp.json()["items"] if i["id"] == BURGUER)
    assert (row[COLUMNS["cantidad"]], row[COLUMNS["tenemos"]], row[COLUMNS["comprar"]]) == (2, 1, 1)
    bad = api.post("/api/stores/apply", json={"changes": [
        {"id": BURGUER, "store": "carrefour", "cantidad": 2, "tenemos": -1}]})
    assert bad.status_code == 422
