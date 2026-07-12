"""Unit tests for the pure helpers in automation.email_check.

The live Gmail fetch + Telegram send path is covered by the manual
`tests/smoke_email_check.py` script, not here — these tests cover the
deterministic logic: subject similarity, processed-state persistence, and
summary formatting.
"""

from __future__ import annotations

from automation.email_check import (
    STORE_SUBJECTS,
    _load_processed_state,
    _summary_message,
    _write_processed_state,
    subject_matches,
)
from automation.item_matching import MatchedItem, MatchResult


def test_subject_matches_exact():
    assert subject_matches("La comanda està preparada!", STORE_SUBJECTS["ametller"])


def test_subject_matches_with_emoji_added():
    assert subject_matches("La comanda està preparada! 🛍️", STORE_SUBJECTS["ametller"])


def test_subject_matches_minor_punctuation_drift():
    assert subject_matches("¡La comanda ja està preparada!", STORE_SUBJECTS["ametller"])


def test_subject_does_not_match_promotional_email():
    assert not subject_matches("Descomptes exclusius aquesta setmana! 🎉", STORE_SUBJECTS["ametller"])


def test_subject_does_not_match_order_confirmed_email():
    # "Comanda confirmada!" is a different email (order placed, not order
    # prepared) — must not be treated as the same signal.
    assert not subject_matches("Comanda confirmada!", STORE_SUBJECTS["ametller"])


def test_processed_state_round_trip(tmp_path):
    state_path = tmp_path / "gmail_processed_state.json"
    assert _load_processed_state(state_path) == {}
    _write_processed_state(state_path, {"ametller": "msg123"})
    assert _load_processed_state(state_path) == {"ametller": "msg123"}


def test_processed_state_missing_file_is_empty(tmp_path):
    assert _load_processed_state(tmp_path / "does_not_exist.json") == {}


def test_summary_message_all_matched():
    match = MatchResult(matched=[MatchedItem("Web Name", "comida", 1.0, "alias")])
    text = _summary_message("ametller", match)
    assert "1/1 items matched" in text
    assert "⚠️" not in text


def test_summary_message_reports_dropped_items():
    match = MatchResult(
        matched=[MatchedItem("Web Name", "comida", 1.0, "alias")],
        dropped_comida=["fresas"],
    )
    text = _summary_message("ametller", match)
    assert "fresas" in text
    assert "⚠️" in text


def test_summary_message_reports_unmatched_website_names():
    match = MatchResult(unmatched_website_names=["Mystery Product 500g"])
    text = _summary_message("ametller", match)
    assert "Mystery Product 500g" in text
    assert "❓" in text
