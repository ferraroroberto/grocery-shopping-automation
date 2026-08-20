"""Unit tests for the pure helpers in automation.email_check.

The live Gmail fetch + Telegram send path is covered by the manual
`tests/smoke_email_check.py` script, not here — these tests cover the
deterministic logic: subject similarity, processed-state persistence, and
summary formatting.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from automation import email_check
from automation.email_check import (
    STORE_SUBJECTS,
    ProcessedEntry,
    _load_processed_state,
    _summary_message,
    _write_processed_state,
    check_latest_confirmation,
    has_problem,
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
    _write_processed_state(state_path, {"ametller": ProcessedEntry("msg123")})
    assert _load_processed_state(state_path) == {"ametller": ProcessedEntry("msg123")}


def test_processed_state_missing_file_is_empty(tmp_path):
    assert _load_processed_state(tmp_path / "does_not_exist.json") == {}


def test_has_problem_false_for_clean_order():
    # Issue #73: a fully-matched confirmation must stay silent on the
    # notify-only-on-problem path.
    match = MatchResult(matched=[MatchedItem("Web Name", "comida", 1.0, "alias")])
    assert not has_problem(match)


def test_has_problem_true_for_dropped_or_unmatched():
    assert has_problem(MatchResult(dropped_comida=["fresas"]))
    assert has_problem(MatchResult(unmatched_website_names=["Mystery 500g"]))


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


# --- processed-state watermark (issue #134) -------------------------------
#
# Trashing the confirmation email we had just processed used to make the
# newest *remaining* email — three weeks old — look brand new, re-notifying
# with a stale confirmation matched against the current purchase log.


class _FakeEmail:
    def __init__(self, message_id, timestamp, subject="La comanda està preparada! 🛍️"):
        self.message_id = message_id
        self.timestamp = timestamp
        self.subject = subject
        self.body_text = f"body of {message_id}"


class _FakeMailbox:
    def __init__(self, emails):
        self._emails = emails
        self.closed = False

    def resolve_sources(self, **_kwargs):
        return (SimpleNamespace(search="q"),)

    def messages(self, _search, **_kwargs):
        return list(self._emails)

    def close(self):
        self.closed = True


class _RecordingNotifier:
    def __init__(self):
        self.sent = []

    def send_text(self, text):
        self.sent.append(text)


def _wire_check(monkeypatch, emails, notifier):
    """Point check_latest_confirmation at fake Gmail, parser, and notifier."""
    monkeypatch.setattr(email_check, "load_gmail_senders", lambda: (object(),))
    monkeypatch.setattr(email_check, "build_gmail_mailbox", lambda: _FakeMailbox(emails))
    monkeypatch.setattr(
        email_check,
        "STORE_PARSERS",
        {"ametller": SimpleNamespace(parse_confirmed_items=lambda body: [body])},
    )
    monkeypatch.setattr(email_check, "load_latest_purchase_log", lambda *a, **k: [])
    monkeypatch.setattr(email_check, "load_alias_table", lambda *a, **k: {})
    monkeypatch.setattr(
        email_check,
        "match_items",
        lambda names, *a, **k: MatchResult(dropped_comida=list(names)),
    )
    monkeypatch.setattr(email_check, "build_notify_notifier", lambda: notifier)


OLD = _FakeEmail("old1", "2026-07-31T06:01:33+00:00")
NEW = _FakeEmail("new1", "2026-08-20T06:02:06+00:00")


def _state_file(tmp_path, payload):
    path = tmp_path / "gmail_processed_state.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def test_processed_state_legacy_string_loads_without_watermark(tmp_path):
    # Pre-#134 files stored a bare message id with no watermark.
    path = _state_file(tmp_path, {"ametller": "msg123"})
    assert _load_processed_state(path) == {"ametller": ProcessedEntry("msg123", None)}


def test_processed_state_round_trip_with_watermark(tmp_path):
    path = tmp_path / "gmail_processed_state.json"
    entry = ProcessedEntry("msg123", "2026-08-20T06:02:06+00:00")
    _write_processed_state(path, {"ametller": entry})
    assert _load_processed_state(path) == {"ametller": entry}


def test_older_email_is_not_reprocessed_when_newest_disappears(tmp_path, monkeypatch):
    # The #134 repro: `new1` was processed, then trashed; Gmail now only
    # returns the three-week-old `old1`.
    path = _state_file(
        tmp_path, {"ametller": {"message_id": NEW.message_id, "timestamp": NEW.timestamp}}
    )
    notifier = _RecordingNotifier()
    _wire_check(monkeypatch, [OLD], notifier)

    result = check_latest_confirmation("ametller", processed_state_path=path)

    assert result.regressed
    assert not result.already_processed
    assert not result.notified
    assert notifier.sent == []
    assert result.reason and OLD.message_id in result.reason and NEW.message_id in result.reason
    # The watermark must not walk backwards.
    assert _load_processed_state(path)["ametller"] == ProcessedEntry(
        NEW.message_id, NEW.timestamp
    )


def test_legacy_state_adopts_a_watermark_then_blocks_the_regression(tmp_path, monkeypatch):
    path = _state_file(tmp_path, {"ametller": NEW.message_id})
    notifier = _RecordingNotifier()
    _wire_check(monkeypatch, [OLD, NEW], notifier)

    first = check_latest_confirmation("ametller", processed_state_path=path)
    assert first.already_processed
    assert _load_processed_state(path)["ametller"].timestamp == NEW.timestamp

    _wire_check(monkeypatch, [OLD], notifier)
    second = check_latest_confirmation("ametller", processed_state_path=path)
    assert second.regressed
    assert notifier.sent == []


def test_newer_email_still_processes_and_records_its_timestamp(tmp_path, monkeypatch):
    path = _state_file(
        tmp_path, {"ametller": {"message_id": OLD.message_id, "timestamp": OLD.timestamp}}
    )
    notifier = _RecordingNotifier()
    _wire_check(monkeypatch, [OLD, NEW], notifier)

    result = check_latest_confirmation("ametller", processed_state_path=path)

    assert not result.regressed
    assert result.notified
    assert len(notifier.sent) == 1
    assert _load_processed_state(path)["ametller"] == ProcessedEntry(
        NEW.message_id, NEW.timestamp
    )


def test_ignore_processed_still_reprocesses_an_older_email(tmp_path, monkeypatch):
    # The Auto tab's "Test last email" must stay a working dry run.
    path = _state_file(
        tmp_path, {"ametller": {"message_id": NEW.message_id, "timestamp": NEW.timestamp}}
    )
    notifier = _RecordingNotifier()
    _wire_check(monkeypatch, [OLD], notifier)

    result = check_latest_confirmation(
        "ametller", processed_state_path=path, ignore_processed=True
    )

    assert not result.regressed
    assert result.notified
    assert len(notifier.sent) == 1
    # ...but the watermark must not follow it backwards, or the guard is
    # disarmed for every email sent between the two.
    assert _load_processed_state(path)["ametller"] == ProcessedEntry(
        OLD.message_id, NEW.timestamp
    )
    mid = _FakeEmail("mid1", "2026-08-05T06:00:00+00:00")
    _wire_check(monkeypatch, [OLD, mid], notifier)
    assert check_latest_confirmation("ametller", processed_state_path=path).regressed
    assert len(notifier.sent) == 1
