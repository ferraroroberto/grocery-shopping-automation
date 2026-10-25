"""The email poller is opt-in per process: only the tray starts it (#301).

Every ``start_poller`` here is a recorder — no test reaches the mailbox.
"""

from __future__ import annotations

import logging
import subprocess
from typing import Any

import pytest
from fastapi.testclient import TestClient

import app.api as api
import app.email_poller as email_poller
import app.tray.manager as manager

# The documented name (README "Email poller"); pinned literally so a rename is deliberate.
FLAG = "GROCERY_EMAIL_POLLER"


@pytest.fixture()
def started(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(email_poller, "start_poller", lambda: calls.append(1))
    return calls


def _boot() -> None:
    with TestClient(api.app):
        pass


def test_non_tray_boot_never_starts_the_poller(
    started: list[int], monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    # A real (non-pytest) boot: no pytest marker, no tray flag.
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.delenv(FLAG, raising=False)
    with caplog.at_level(logging.INFO):
        _boot()
    assert started == []
    assert [r.getMessage() for r in caplog.records if "poller is off" in r.getMessage()] == [
        "ℹ️ Email poller is off (not launched by the tray; set GROCERY_EMAIL_POLLER=1 to opt in)"
    ]


def test_tray_flag_starts_the_poller(started: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    monkeypatch.setenv(FLAG, "1")
    _boot()
    assert started == [1]


def test_pytest_guard_wins_over_the_flag(started: list[int], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(FLAG, "1")  # PYTEST_CURRENT_TEST is set by pytest itself
    _boot()
    assert started == []


def test_tray_spawn_sets_the_flag_in_the_webapp_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, Any] = {}

    class _Proc:
        pid = 1

    def _popen(cmd: list[str], **kwargs: Any) -> _Proc:
        captured["env"] = kwargs["env"]
        return _Proc()

    mgr = manager.WebappManager(manager.WebappManagerConfig(host="127.0.0.1", port=8502))
    monkeypatch.delenv(FLAG, raising=False)
    monkeypatch.setattr(manager, "_renew_tailscale_cert", lambda: None)
    monkeypatch.setattr(mgr, "status", lambda: manager.WebappStatus(
        running=False, ownership=manager.OWNERSHIP_NONE, pid=None, port=8502,
        base_url=mgr.base_url, detail="not running",
    ))
    monkeypatch.setattr(subprocess, "Popen", _popen)
    mgr.start(wait=False)
    assert captured["env"][FLAG] == "1"
