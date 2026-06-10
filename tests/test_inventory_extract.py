"""Unit tests for the LLM extraction parsing/validation (no hub required)."""

import pandas as pd
import pytest

import src.inventory_extract as ie
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

    monkeypatch.setattr(ie, "Anthropic", _FakeClient)
    result = extract("tres yogures", df, base_url="http://x", model="gemini_pro")

    idxs = {it["idx"]: it for it in result.items}
    assert 99 not in idxs  # invalid idx dropped
    assert idxs[0]["count"] == 3
    assert idxs[1]["count"] == 0  # negative clamped to 0
