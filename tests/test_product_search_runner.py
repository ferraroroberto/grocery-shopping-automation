"""Unit tests for the product-search subprocess runner's NDJSON parsing (#87)."""

import json

from app import product_search_runner as psr


def _lines(*events):
    return [json.dumps(e) + "\n" for e in events]


def test_parse_result_returns_last_result_event():
    chunks = _lines(
        {"event": "progress", "message": "Abriendo…"},
        {"event": "progress", "message": "Buscando en Mercadona…"},
        {"event": "result", "result": {"results": [{"query": "sandia", "candidates": []}]}},
    )
    result = psr.parse_result(chunks)
    assert result["results"][0]["query"] == "sandia"


def test_parse_result_none_before_result_event():
    # Only progress so far → no result yet.
    assert psr.parse_result(_lines({"event": "progress", "message": "…"})) is None
    assert psr.parse_result([]) is None
    assert psr.parse_result(["not json\n"]) is None


def test_latest_progress_returns_most_recent():
    chunks = _lines(
        {"event": "progress", "message": "Abriendo…"},
        {"event": "progress", "message": "Mercadona: 3 resultado(s)"},
        {"event": "result", "result": {"results": []}},
    )
    assert psr.latest_progress(chunks) == "Mercadona: 3 resultado(s)"
    assert psr.latest_progress([]) is None


def test_build_command_has_one_query_flag_per_term():
    cmd = psr.build_command(["sandia", "melon"], 6)
    assert cmd.count("--query") == 2
    assert "--json" in cmd and "sandia" in cmd and "melon" in cmd
