"""Voice-command endpoint + pure-logic tests (issue #86).

The LLM parse is monkeypatched at the API boundary (`api.parse_voice_items`)
— same stubbing pattern as the audio-match tests. Pure functions
(clean_parsed / apply_* / build_*_speech) are tested directly on in-memory
frames, no I/O.
"""

from __future__ import annotations

import pandas as pd
import pytest

import app.api_common as api_common
import app.routers.voice as voice_api
from src.data import COLUMNS, SpreadsheetLockedError
from src.voice_command import (
    SPEECH_BUSY,
    VoiceItem,
    VoiceParseResult,
    apply_add,
    apply_set,
    build_add_speech,
    build_query_speech,
    build_set_speech,
    clean_parsed,
)


def _frame(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=list(COLUMNS.values()))


def _row(comida: str, cantidad: int = 0, tenemos: int = 0, super_: str = "mercadona") -> dict:
    return {
        COLUMNS["super"]: super_,
        COLUMNS["buscador"]: "",
        COLUMNS["lugar"]: "despensa",
        COLUMNS["comida"]: comida,
        COLUMNS["cantidad"]: cantidad,
        COLUMNS["tenemos"]: tenemos,
        COLUMNS["comprar"]: max(0, cantidad - tenemos),
    }


# --------------------------------------------------------------------------- #
# clean_parsed — LLM output validation
# --------------------------------------------------------------------------- #
def test_clean_parsed_demotes_invented_idx_to_new_item():
    items, _ = clean_parsed({"items": [{"idx": 99, "name": "leche", "qty": 2}]}, {0, 1})
    assert items == [VoiceItem(idx=None, name="leche", qty=2)]


def test_clean_parsed_drops_unnamed_unmatched_and_bad_qty():
    items, ambiguous = clean_parsed(
        {
            "items": [
                {"idx": None, "name": "", "qty": 1},
                {"idx": 1, "name": "pan", "qty": "dos"},
                {"idx": 0, "name": "leche", "qty": True},
            ],
            "ambiguous": [{"phrase": "algunos yogures", "note": "vague"}, "not-a-dict"],
        },
        {0, 1},
    )
    assert items == [
        VoiceItem(idx=1, name="pan", qty=None),
        VoiceItem(idx=0, name="leche", qty=None),
    ]
    assert ambiguous == [{"phrase": "algunos yogures", "note": "vague"}]


# --------------------------------------------------------------------------- #
# apply_add / apply_set — in-memory mutations
# --------------------------------------------------------------------------- #
def test_apply_add_bumps_target_and_creates_new_rows():
    df = _frame([_row("leche", cantidad=2, tenemos=2)])
    df, outcome = apply_add(
        df,
        [VoiceItem(idx=0, name="leche", qty=2), VoiceItem(idx=None, name="aguacates", qty=None)],
    )
    assert outcome.bumped == [("leche", 2)]
    assert outcome.created == [("aguacates", 1)]
    assert int(df.at[0, COLUMNS["cantidad"]]) == 4
    assert int(df.at[0, COLUMNS["comprar"]]) == 2
    new = df.iloc[-1]
    assert new[COLUMNS["comida"]] == "aguacates"
    assert int(new[COLUMNS["cantidad"]]) == 1
    assert int(new[COLUMNS["comprar"]]) == 1
    assert new[COLUMNS["super"]] == ""
    assert new[COLUMNS["buscador"]] == ""


def test_apply_set_target_stock_not_found_and_no_value():
    df = _frame([_row("leche", cantidad=2, tenemos=1), _row("aceite", cantidad=3, tenemos=3)])

    df, target = apply_set(df, [VoiceItem(idx=0, name="leche", qty=4)], "cantidad")
    assert target.set_items == [("leche", 4)]
    assert int(df.at[0, COLUMNS["comprar"]]) == 3

    df, stock = apply_set(
        df,
        [
            VoiceItem(idx=1, name="aceite", qty=0),
            VoiceItem(idx=None, name="flurbos", qty=2),
            VoiceItem(idx=0, name="leche", qty=None),
        ],
        "tenemos",
    )
    assert stock.set_items == [("aceite", 0)]
    assert int(df.at[1, COLUMNS["comprar"]]) == 3
    assert stock.not_found == ["flurbos"]
    assert stock.no_value == ["leche"]


# --------------------------------------------------------------------------- #
# speech builders
# --------------------------------------------------------------------------- #
def test_build_add_speech_variants():
    from src.voice_command import AddOutcome

    speech = build_add_speech(
        AddOutcome(bumped=[("leche", 1), ("huevos", 2)], created=[("aguacates", 1)]),
        [{"phrase": "algunos yogures"}],
    )
    assert "leche y 2 huevos" in speech
    assert "he creado aguacates como nuevo" in speech
    assert "No he entendido la cantidad de algunos yogures" in speech

    assert build_add_speech(AddOutcome(), []) == "No he entendido qué añadir — inténtalo otra vez."


def test_build_set_speech_variants():
    from src.voice_command import SetOutcome

    speech = build_set_speech(
        SetOutcome(set_items=[("leche", 4)], not_found=["flurbos"], no_value=["pan"]),
        "target",
    )
    assert "El objetivo de leche es ahora 4" in speech
    assert "Necesito un número para pan" in speech
    assert "No encuentro flurbos en la lista" in speech

    speech = build_set_speech(SetOutcome(set_items=[("aceite", 0)]), "stock")
    assert "El stock de aceite es ahora 0" in speech

    assert build_set_speech(SetOutcome(), "stock") == "No he entendido nada — inténtalo otra vez."


def test_build_query_speech_counts_and_caps():
    df = _frame(
        [_row(f"item{i}", cantidad=1, tenemos=0) for i in range(10)]
        + [_row("stocked", cantidad=1, tenemos=1)]
    )
    speech = build_query_speech(df)
    assert speech.startswith("10 cosas por comprar")
    assert "10 en mercadona" in speech
    assert "y 2 más" in speech
    assert "stocked" not in speech

    assert build_query_speech(_frame([_row("leche", 1, 1)])).startswith("La lista está vacía")


# --------------------------------------------------------------------------- #
# endpoint — LLM stubbed, real fixture spreadsheet
# --------------------------------------------------------------------------- #
def _stub_parse(items, ambiguous=None):
    def fake(text, df, *, base_url, model, max_tokens, timeout):
        return VoiceParseResult(items=items, ambiguous=ambiguous or [], raw_text="{}")

    return fake


def _item_by_name(client, name):
    inventory = client.get("/api/inventory").json()
    matches = [it for it in inventory["items"] if it[COLUMNS["comida"]] == name]
    return matches[0] if matches else None


def test_voice_add_bumps_existing_and_creates_new(client, monkeypatch):
    before = _item_by_name(client, "dorada")
    monkeypatch.setattr(
        voice_api,
        "parse_voice_items",
        _stub_parse(
            [VoiceItem(idx=before["id"], name="dorada", qty=2), VoiceItem(idx=None, name="aguacates", qty=None)]
        ),
    )
    resp = client.post("/api/voice/command", json={"intent": "add", "text": "dorada y aguacates"})
    assert resp.status_code == 200
    body = resp.json()
    assert "2 dorada" in body["speech"] and "aguacates como nuevo" in body["speech"]
    assert {"name": "dorada", "qty": 2, "action": "bumped"} in body["applied"]

    after = _item_by_name(client, "dorada")
    assert after[COLUMNS["cantidad"]] == before[COLUMNS["cantidad"]] + 2
    created = _item_by_name(client, "aguacates")
    assert created is not None and created[COLUMNS["comprar"]] == 1


def test_voice_target_sets_value(client, monkeypatch):
    row = _item_by_name(client, "hielo")
    monkeypatch.setattr(
        voice_api, "parse_voice_items", _stub_parse([VoiceItem(idx=row["id"], name="hielo", qty=3)])
    )
    resp = client.post("/api/voice/command", json={"intent": "target", "text": "pon el hielo a tres"})
    assert resp.status_code == 200
    assert "El objetivo de hielo es ahora 3" in resp.json()["speech"]
    assert _item_by_name(client, "hielo")[COLUMNS["cantidad"]] == 3


def test_voice_stock_sets_value(client, monkeypatch):
    row = _item_by_name(client, "nuggets")
    monkeypatch.setattr(
        voice_api, "parse_voice_items", _stub_parse([VoiceItem(idx=row["id"], name="nuggets", qty=0)])
    )
    resp = client.post("/api/voice/command", json={"intent": "stock", "text": "no quedan nuggets"})
    assert resp.status_code == 200
    assert "El stock de nuggets es ahora 0" in resp.json()["speech"]
    after = _item_by_name(client, "nuggets")
    assert after[COLUMNS["tenemos"]] == 0
    assert after[COLUMNS["comprar"]] == after[COLUMNS["cantidad"]]


def test_voice_query_needs_no_llm(client, monkeypatch):
    monkeypatch.setattr(voice_api, "parse_voice_items", _stub_parse([]))  # must not be called

    def boom(*args, **kwargs):
        raise AssertionError("query must not call the LLM")

    monkeypatch.setattr(voice_api, "parse_voice_items", boom)
    resp = client.post("/api/voice/command", json={"intent": "query"})
    assert resp.status_code == 200
    assert "cosas por comprar" in resp.json()["speech"]


def test_voice_empty_text_400_and_bad_intent_422(client):
    assert client.post("/api/voice/command", json={"intent": "add", "text": " "}).status_code == 400
    assert client.post("/api/voice/command", json={"intent": "nuke"}).status_code == 422


def test_voice_locked_spreadsheet_speaks_busy(client, monkeypatch):
    def locked():
        raise SpreadsheetLockedError("locked")

    monkeypatch.setattr(api_common, "load_inventory_data", locked)
    resp = client.post("/api/voice/command", json={"intent": "query"})
    assert resp.status_code == 200
    assert resp.json()["speech"] == SPEECH_BUSY
