"""API tests for the on-demand product search endpoints (issue #87).

The subprocess-backed search and the LLM parse are stubbed, so these run
offline against the fixture spreadsheet via the shared ``client`` fixture.
"""

import json

import pytest

import app.api as api
from src.voice_command import VoiceItem, VoiceParseResult


class FakeProc:
    """Stand-in for the search subprocess handle."""

    def __init__(self, alive, returncode=0):
        self._alive = alive
        self.returncode = returncode

    def poll(self):
        return None if self._alive else self.returncode


@pytest.fixture(autouse=True)
def _clear_search_run():
    api._SEARCH_RUN.clear()
    yield
    api._SEARCH_RUN.clear()


def test_start_parses_utterance_and_launches(client, monkeypatch):
    monkeypatch.setattr(
        api, "parse_voice_items",
        lambda *a, **k: VoiceParseResult(items=[VoiceItem(idx=None, name="sandia", qty=None)]),
    )
    seen = {}

    def fake_start(terms, limit):
        seen["terms"] = terms
        return FakeProc(alive=True), [], None

    monkeypatch.setattr(api.product_search_runner, "start", fake_start)
    resp = client.post("/api/product-search/start", json={"text": "añade sandia"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "running"
    assert seen["terms"] == ["sandia"]  # "añade" stripped by the parse
    assert body["items"][0]["term"] == "sandia"


def test_start_rejects_empty_text(client):
    assert client.post("/api/product-search/start", json={"text": "  "}).status_code == 400


def test_start_conflicts_when_already_running(client):
    api._SEARCH_RUN.update({"id": "x", "process": FakeProc(alive=True)})
    assert client.post("/api/product-search/start", json={"text": "sandia"}).status_code == 409


def test_status_done_merges_results(client):
    api._SEARCH_RUN.update({
        "id": "r1",
        "process": FakeProc(alive=False, returncode=0),
        "chunks": [
            json.dumps({"event": "progress", "message": "Buscando en Mercadona…"}) + "\n",
            json.dumps({"event": "result", "result": {"results": [{
                "query": "sandia",
                "candidates": [{
                    "store": "mercadona", "name": "Sandía", "product_url": "u",
                    "price_text": "5,15 €", "price_eur": 5.15, "thumbnail": "",
                    "native_rank": 0, "score": 0.9, "match": "strong",
                }],
                "errors": {},
            }]}}) + "\n",
        ],
        "items": [{"term": "sandia", "inventory_idx": None, "existing_super": ""}],
        "started_at": 0.0,
    })
    body = client.get("/api/product-search/status").json()
    assert body["state"] == "done"
    assert body["items"][0]["candidates"][0]["name"] == "Sandía"


def test_select_creates_new_row(client):
    before = client.get("/api/inventory").json()["summary"]["total_items"]
    resp = client.post("/api/product-search/select", json={
        "term": "kiwi de prueba", "store": "mercadona",
        "product_url": "https://tienda.mercadona.es/product/1/kiwi",
        "name": "Kiwi", "inventory_idx": None,
    })
    assert resp.status_code == 200
    body = resp.json()
    cols = body["columns"]
    assert body["summary"]["total_items"] == before + 1
    new = [it for it in body["items"] if it[cols["comida"]] == "kiwi de prueba"][0]
    assert new[cols["buscador"]] == "https://tienda.mercadona.es/product/1/kiwi"
    assert new[cols["super"]] == "mercadona"
    assert new[cols["comprar"]] == 1  # target 1 → lands on the shopping list


def test_select_creates_new_row_with_zone_and_quantities(client):
    """Issue #92: the staged confirm row sends zone + present/target explicitly."""
    resp = client.post("/api/product-search/select", json={
        "term": "mango de prueba", "store": "mercadona",
        "product_url": "https://tienda.mercadona.es/product/2/mango",
        "name": "Mango", "inventory_idx": None,
        "lugar": "nevera", "tenemos": 1, "cantidad": 3,
    })
    assert resp.status_code == 200
    body = resp.json()
    cols = body["columns"]
    new = [it for it in body["items"] if it[cols["comida"]] == "mango de prueba"][0]
    assert new[cols["lugar"]] == "nevera"
    assert new[cols["tenemos"]] == 1
    assert new[cols["cantidad"]] == 3
    assert new[cols["comprar"]] == 2  # need = target − present


def test_select_existing_row_applies_zone_and_quantities(client):
    """Issue #92: explicit add-time parameters override the keep-existing defaults."""
    inv = client.get("/api/inventory").json()
    cols = inv["columns"]
    idx = inv["items"][0]["id"]
    resp = client.post("/api/product-search/select", json={
        "term": inv["items"][0][cols["comida"]], "store": "mercadona",
        "product_url": "https://tienda.mercadona.es/product/3/x",
        "name": "X", "inventory_idx": idx,
        "lugar": "despensa de prueba", "tenemos": 2, "cantidad": 4,
    })
    assert resp.status_code == 200
    updated = [it for it in resp.json()["items"] if it["id"] == idx][0]
    assert updated[cols["lugar"]] == "despensa de prueba"
    assert updated[cols["tenemos"]] == 2
    assert updated[cols["cantidad"]] == 4


def test_select_rejects_negative_quantities(client):
    resp = client.post("/api/product-search/select", json={
        "term": "x", "store": "mercadona", "product_url": "https://x/1",
        "inventory_idx": None, "tenemos": -1,
    })
    assert resp.status_code == 422


def test_select_updates_existing_row(client):
    inv = client.get("/api/inventory").json()
    cols = inv["columns"]
    item = inv["items"][0]
    idx = item["id"]
    resp = client.post("/api/product-search/select", json={
        "term": item[cols["comida"]], "store": "ametller",
        "product_url": "https://www.ametllerorigen.com/es/x/9.html",
        "name": "X", "inventory_idx": idx,
    })
    assert resp.status_code == 200
    updated = [it for it in resp.json()["items"] if it["id"] == idx][0]
    assert updated[cols["buscador"]] == "https://www.ametllerorigen.com/es/x/9.html"
    assert updated[cols["super"]] == "ametller"
    assert updated[cols["cantidad"]] >= 1  # never lowered below buyable
