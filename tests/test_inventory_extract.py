"""Unit tests for the LLM extraction parsing/validation (no hub required)."""

import pandas as pd
import pytest

import src.hub_client as hub_client
from src.inventory_extract import ExtractionError, _parse_strict_json, extract


def test_parse_strict_json_plain():
    assert _parse_strict_json('{"a": 1}') == {"a": 1}


def test_parse_strict_json_strips_markdown_fence():
    assert _parse_strict_json('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_strict_json_extracts_brace_block():
    assert _parse_strict_json('blah {"a": 1} trailing') == {"a": 1}


def test_parse_strict_json_raises_on_garbage():
    with pytest.raises(ExtractionError):
        _parse_strict_json("not json at all")


class _FakeBlock:
    def __init__(self, text):
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, text):
        self.content = [_FakeBlock(text)]


def test_extract_validates_idx_and_clamps_negative(monkeypatch):
    df = pd.DataFrame({"comida": ["yogur", "leche"], "lugar": ["nevera", "nevera"]}, index=[0, 1])

    payload = (
        '{"items": ['
        '{"idx": 0, "count": 3, "zone": "nevera", "evidence": "tres"},'
        '{"idx": 99, "count": 1, "zone": "nevera", "evidence": "bad idx"},'
        '{"idx": 1, "count": -5, "zone": "nevera", "evidence": "negative"}'
        '], "zones_mentioned": ["nevera"], "unmatched_mentions": []}'
    )

    class _FakeClient:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, *a, **k):
            return _FakeMessage(payload)

    monkeypatch.setattr(hub_client, "Anthropic", _FakeClient)
    result = extract("tres yogures", df, base_url="http://x", model="gemini_pro")

    idxs = {it["idx"]: it for it in result.items}
    assert 99 not in idxs  # invalid idx dropped
    assert idxs[0]["count"] == 3
    assert idxs[1]["count"] == 0  # negative clamped to 0


def _extract_with_payload(monkeypatch, payload: str, df):
    class _FakeClient:
        def __init__(self, *a, **k):
            self.messages = self

        def create(self, *a, **k):
            return _FakeMessage(payload)

    monkeypatch.setattr(hub_client, "Anthropic", _FakeClient)
    return extract("transcripción", df, base_url="http://x", model="gemini_pro")


@pytest.fixture()
def small_df():
    return pd.DataFrame(
        {"comida": ["yogur griego", "leche semidesnatada", "garbanzos"],
         "lugar": ["nevera", "nevera", "estante"]},
        index=[0, 1, 2],
    )


def test_unmatched_mention_keeps_the_idx_the_llm_resolved(monkeypatch, small_df):
    """The whole point of issue #132: a mention that names a row comes back with
    that row attached, not buried in prose."""
    payload = (
        '{"items": [], "zones_mentioned": ["nevera"], "unmatched_mentions": ['
        '{"phrase": "Yogurt griego", "idx": 0, "approx_count": null, "note": "no count"}]}'
    )
    (mention,) = _extract_with_payload(monkeypatch, payload, small_df).unmatched_mentions
    assert mention["idx"] == 0
    assert mention["resolved_by"] == "llm"
    assert mention["comida"] == "yogur griego"


def test_unresolved_mention_falls_back_to_the_fuzzy_resolver(monkeypatch, small_df):
    payload = (
        '{"items": [], "zones_mentioned": ["estante"], "unmatched_mentions": ['
        '{"phrase": "Ganzos", "idx": null, "approx_count": null, "note": "?"}]}'
    )
    (mention,) = _extract_with_payload(monkeypatch, payload, small_df).unmatched_mentions
    assert mention["idx"] == 2
    assert mention["resolved_by"] == "fuzzy"
    assert mention["match_score"] > 0


def test_unmatched_idx_is_validated_like_item_idx(monkeypatch, small_df):
    payload = (
        '{"items": [], "zones_mentioned": ["nevera"], "unmatched_mentions": ['
        '{"phrase": "zzzz qqqq", "idx": 99, "approx_count": "2", "note": "off the end"}]}'
    )
    (mention,) = _extract_with_payload(monkeypatch, payload, small_df).unmatched_mentions
    assert mention["idx"] is None
    assert mention["resolved_by"] == ""
    assert mention["approx_count"] == 2  # coerced from the string the LLM sent


def test_a_row_already_counted_cannot_be_claimed_twice(monkeypatch, small_df):
    """An item with a real count must not also appear as a count-missing mention —
    it would render in two buckets and apply twice."""
    payload = (
        '{"items": [{"idx": 0, "count": 2, "zone": "nevera", "evidence": "dos"}],'
        '"zones_mentioned": ["nevera"], "unmatched_mentions": ['
        '{"phrase": "Yogurt griego", "idx": 0, "approx_count": null, "note": "dup"}]}'
    )
    result = _extract_with_payload(monkeypatch, payload, small_df)
    assert result.items[0]["idx"] == 0
    assert result.unmatched_mentions[0]["idx"] is None


def test_malformed_mentions_are_dropped_not_fatal(monkeypatch, small_df):
    payload = (
        '{"items": [], "zones_mentioned": [], "unmatched_mentions": ['
        '"just a string", {"phrase": "sin nada", "note": "ok"}]}'
    )
    mentions = _extract_with_payload(monkeypatch, payload, small_df).unmatched_mentions
    assert len(mentions) == 1
    assert mentions[0]["phrase"] == "sin nada"
    assert mentions[0]["idx"] is None
