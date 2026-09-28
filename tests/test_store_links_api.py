"""Store-links API routes (issue #148), via TestClient on the fixture data.

Same fixtures as ``test_store_links.py``: the committed xlsx fixture and the
invented benchmark run under ``tests/fixtures/store_links/``.
"""

from __future__ import annotations

import pytest

import src.data as data
import src.store_links as store_links
from src.data import COLUMNS, SpreadsheetLockedError
from tests.test_store_links import BURGUER, CF_BURGER_600, PAVO, RUNS


@pytest.fixture()
def api(client, monkeypatch):
    """A TestClient on the fixture xlsx, pricing from the fixture benchmark runs."""
    monkeypatch.setitem(data.CONFIG, "benchmark", {"runs_dir": str(RUNS)})
    return client


def test_api_import_and_payload_urls(api):
    before = api.get("/api/inventory").json()
    assert next(i for i in before["items"] if i["id"] == BURGUER)["urls"] == {}

    resp = api.post("/api/stores/import-latest")
    assert resp.status_code == 200
    body = resp.json()
    assert body["import"]["unmatched"] == 1 and body["import"]["run_date"] == "2026-01-15"
    urls = next(i for i in body["items"] if i["id"] == BURGUER)["urls"]
    assert set(urls) == {"ametller", "carrefour", "mercadona"}
    assert urls["carrefour"] == CF_BURGER_600


def test_api_store_url_and_pick(api):
    resp = api.put(f"/api/items/{PAVO}/store-url",
                   json={"store": "carrefour", "url": "https://www.carrefour.es/pavo/p"})
    assert resp.status_code == 200
    assert next(i for i in resp.json()["items"] if i["id"] == PAVO)["urls"] == {
        "carrefour": "https://www.carrefour.es/pavo/p"}
    # An id-less Carrefour URL is flagged, so the UI won't pose it as a product link.
    assert next(i for i in resp.json()["items"] if i["id"] == PAVO)["url_kinds"] == {"carrefour": "suspect"}

    assert api.put(f"/api/items/{PAVO}/store-url", json={"store": "carrefour", "url": "nope"}).status_code == 400
    assert api.put("/api/items/99999/store-url", json={"store": "carrefour", "url": ""}).status_code == 404

    picked = api.post(f"/api/items/{PAVO}/pick", json={"store": "carrefour"})
    assert picked.status_code == 200
    row = next(i for i in picked.json()["items"] if i["id"] == PAVO)
    assert (row[COLUMNS["super"]], row[COLUMNS["buscador"]]) == ("carrefour", "https://www.carrefour.es/pavo/p")

    missing = api.post(f"/api/items/{PAVO}/pick", json={"store": "dia"})
    assert missing.status_code == 400 and "no dia URL" in missing.json()["detail"]


def test_api_pick_locked_spreadsheet_is_423(api, monkeypatch):
    api.post("/api/stores/import-latest")

    def locked(*_a, **_k):
        raise SpreadsheetLockedError(data.SPREADSHEET_LOCKED_HINT)

    monkeypatch.setattr(store_links, "save_inventory_data", locked)
    resp = api.post(f"/api/items/{BURGUER}/pick", json={"store": "carrefour"})
    assert resp.status_code == 423
    assert resp.json()["detail"] == data.SPREADSHEET_LOCKED_HINT


def test_api_stores_registry(api):
    body = api.get("/api/stores").json()
    assert body["run_date"] == "2026-01-15"
    stores = {s["key"]: s for s in body["stores"]}
    assert stores["mercadona"]["has_handler"] and stores["ametller"]["has_handler"]
    assert stores["carrefour"]["has_handler"] and not stores["dia"]["has_handler"]
    assert body["default_frequency"] == "weekly" and "2-weekly" in body["frequencies"]


def test_api_recommended_is_keyed_by_item_id(api):
    body = api.get("/api/stores/recommended").json()
    assert body["picks"][str(BURGUER)] == "carrefour"
    assert body["picks_by_key"]["burguer-ternera"] == "carrefour"


def test_api_simulate(api):
    api.post("/api/stores/import-latest")
    resp = api.post("/api/stores/simulate", json={"picks": {str(BURGUER): "carrefour"}, "frequency": "monthly"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["orders_per_month"] == 1.0
    assert body["picks"]["goods"] == round(49.6 - 20 + 14.4, 2)
    default = api.post("/api/stores/simulate", json={"picks": {}}).json()
    assert default["orders_per_month"] == round(52 / 12, 4)
    assert api.post("/api/stores/simulate", json={"frequency": "daily"}).status_code == 400
    assert api.post("/api/stores/simulate", json={"orders_per_month": 0}).status_code == 422


def test_api_simulate_without_a_run_is_404(api, monkeypatch, tmp_path):
    monkeypatch.setitem(data.CONFIG, "benchmark", {"runs_dir": str(tmp_path / "none")})
    assert api.post("/api/stores/simulate", json={"picks": {}}).status_code == 404
    assert api.get("/api/stores/recommended").status_code == 404
    assert api.get("/api/stores").json()["run_date"] is None


def test_api_apply_preview_then_apply(api):
    api.post("/api/stores/import-latest")
    preview = api.post("/api/stores/apply-preview", json={"picks": {str(BURGUER): "carrefour"}}).json()
    [change] = preview["changes"]
    assert (change["id"], change["to"], change["cantidad"]) == (BURGUER, "carrefour", 2)

    resp = api.post("/api/stores/apply",
                    json={"changes": [{"id": change["id"], "store": change["to"], "cantidad": change["cantidad"]}]})
    assert resp.status_code == 200 and resp.json()["applied"] == 1
    row = next(i for i in resp.json()["items"] if i["id"] == BURGUER)
    assert (row[COLUMNS["super"]], row[COLUMNS["buscador"]], row[COLUMNS["cantidad"]]) == (
        "carrefour", CF_BURGER_600, 2)

    bad = api.post("/api/stores/apply", json={"changes": [{"id": PAVO, "store": "carrefour", "cantidad": 1}]})
    assert bad.status_code == 400
