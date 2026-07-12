"""Unit + API tests for the Auto-tab email poller (issue #73).

The live Gmail/Telegram path is covered by the manual
`tests/smoke_email_check.py` script and the Auto tab's "Test last email"
button — here the #72 seam is stubbed, and the tests cover the poller's own
logic: config load/save/merge, outcome formatting, log trimming, and the
FastAPI routes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import app.email_poller as email_poller
import src.gmail_config as gmail_config
from automation.email_check import ConfirmationCheckResult
from automation.item_matching import MatchedItem, MatchResult
from src.gmail_config import (
    PollerSettings,
    load_monitored_senders,
    load_poller_settings,
    save_gmail_monitor_config,
)


@pytest.fixture()
def config_path(tmp_path: Path, monkeypatch) -> Path:
    """Point the gmail config + check log at a throwaway temp dir."""
    path = tmp_path / "gmail_config.json"
    path.write_text(
        json.dumps(
            {
                "senders": [
                    {
                        "address": "noreply@news.ametllerorigen.cat",
                        "name": "Ametller Origen",
                        "store": "ametller",
                        "enabled": True,
                    }
                ],
                "poller": {"enabled": False, "interval_minutes": 60},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gmail_config, "DEFAULT_WHITELIST_PATH", path)
    monkeypatch.setattr(email_poller, "CHECK_LOG_PATH", tmp_path / "email_check_log.json")
    return path


def test_load_monitored_senders(config_path):
    senders = load_monitored_senders()
    assert len(senders) == 1
    assert senders[0].store == "ametller"
    assert senders[0].enabled is True


def test_load_monitored_senders_legacy_schema(config_path):
    # Pre-#73 config without store/enabled keys still loads (enabled defaults on).
    config_path.write_text(
        json.dumps({"senders": [{"address": "a@b.c", "name": "X"}]}), encoding="utf-8"
    )
    senders = load_monitored_senders()
    assert senders[0].store == ""
    assert senders[0].enabled is True
    assert load_poller_settings() == PollerSettings(enabled=False, interval_minutes=60)


def test_load_poller_settings_clamps_interval(config_path):
    config_path.write_text(
        json.dumps({"senders": [], "poller": {"enabled": True, "interval_minutes": 1}}),
        encoding="utf-8",
    )
    assert load_poller_settings().interval_minutes == 5


def test_save_gmail_monitor_config_round_trip(config_path):
    senders = load_monitored_senders()
    senders[0].enabled = False
    save_gmail_monitor_config(senders, PollerSettings(enabled=True, interval_minutes=30))
    assert load_monitored_senders()[0].enabled is False
    assert load_poller_settings() == PollerSettings(enabled=True, interval_minutes=30)


def test_update_config_merges_sender_flags(config_path):
    email_poller.update_config(
        enabled=True,
        interval_minutes=15,
        sender_flags={"noreply@news.ametllerorigen.cat": False},
    )
    senders = load_monitored_senders()
    assert senders[0].enabled is False
    assert senders[0].store == "ametller"  # untouched by the merge
    assert load_poller_settings() == PollerSettings(enabled=True, interval_minutes=15)


def test_outcome_text_variants():
    assert "skipped" in email_poller.outcome_text(
        ConfirmationCheckResult("ametller", checked=False, reason="no Gmail sender whitelisted")
    )
    assert "already processed" in email_poller.outcome_text(
        ConfirmationCheckResult("ametller", checked=True, message_id="x", already_processed=True)
    )
    match = MatchResult(
        matched=[MatchedItem("Web", "comida", 1.0, "alias")], dropped_comida=["fresas"]
    )
    text = email_poller.outcome_text(
        ConfirmationCheckResult("ametller", checked=True, message_id="x", match=match)
    )
    assert "1/1 items matched" in text
    assert "fresas" in text


def test_append_log_trims_to_limit(tmp_path):
    path = tmp_path / "log.json"
    for i in range(email_poller.CHECK_LOG_LIMIT + 5):
        email_poller._append_log([{"ts": f"t{i}"}], path)
    log = email_poller._load_log(path)
    assert len(log) == email_poller.CHECK_LOG_LIMIT
    assert log[-1]["ts"] == f"t{email_poller.CHECK_LOG_LIMIT + 4}"


def test_run_checks_logs_and_is_forceable(config_path, monkeypatch):
    calls = []

    def fake_check(store, *, ignore_processed=False, notify_only_on_problem=False):
        calls.append((store, ignore_processed, notify_only_on_problem))
        return ConfirmationCheckResult(store, checked=True, message_id="m1", already_processed=True)

    monkeypatch.setattr(email_poller, "check_latest_confirmation", fake_check)
    entries = email_poller.run_checks(force=True, trigger="manual")
    # force=True → re-process and always notify; a scheduled run is the inverse.
    assert calls == [("ametller", True, False)]
    assert entries[0]["store"] == "ametller"
    assert entries[0]["trigger"] == "manual"
    assert entries[0]["ok"] is True
    # The entry landed in the persisted log, newest last.
    assert email_poller._load_log()[-1]["outcome"] == entries[0]["outcome"]


def test_run_checks_without_enabled_senders(config_path, monkeypatch):
    email_poller.update_config(
        enabled=False, interval_minutes=60,
        sender_flags={"noreply@news.ametllerorigen.cat": False},
    )
    entries = email_poller.run_checks()
    assert entries[0]["ok"] is False
    assert "No monitored sender enabled" in entries[0]["outcome"]


def test_api_status_config_and_check(client, config_path, monkeypatch):
    monkeypatch.setattr(
        email_poller,
        "check_latest_confirmation",
        lambda store, *, ignore_processed=False, notify_only_on_problem=False: ConfirmationCheckResult(
            store, checked=True, message_id="m2", already_processed=True
        ),
    )
    status = client.get("/api/email-monitor/status")
    assert status.status_code == 200
    body = status.json()
    assert body["poller"]["interval_minutes"] == 60
    assert body["senders"][0]["store"] == "ametller"

    updated = client.put(
        "/api/email-monitor/config",
        json={
            "enabled": True,
            "interval_minutes": 30,
            "senders": [{"address": "noreply@news.ametllerorigen.cat", "enabled": True}],
        },
    )
    assert updated.status_code == 200
    assert updated.json()["poller"] == {"enabled": True, "interval_minutes": 30}

    checked = client.post("/api/email-monitor/check", json={"force": False})
    assert checked.status_code == 200
    checks = checked.json()["checks"]
    assert checks[0]["store"] == "ametller"
    assert checks[0]["outcome"] == "No new email — latest already processed"


def test_api_config_rejects_out_of_range_interval(client, config_path):
    resp = client.put(
        "/api/email-monitor/config",
        json={"enabled": True, "interval_minutes": 1, "senders": []},
    )
    assert resp.status_code == 422
