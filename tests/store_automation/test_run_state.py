"""The cart-fill run's declared state: the derive table, and the endings through the API.

No store is touched and no browser opens: the endings are driven by stand-in child
processes that print and exit the way a real run does.
"""

import asyncio
import json
import sys
import time

import pytest

from app import automation_runner, subprocess_plumbing
from app.webapp.routers import automation
from src.automation.run_state import (
    EXIT_MISSING,
    EXIT_OK,
    EXIT_PROFILE_NOT_INITIALISED,
    EXIT_UNCONFIRMED,
    REASON_DRY_RUN,
    REASON_EXITED_BEFORE_SUMMARY,
    REASON_PROFILE_NOT_INITIALISED,
    REASON_UNEXPECTED_EXIT,
    SUMMARY_HEADER,
    RunObservation,
    RunState,
    derive_run_state,
)


def _obs(**facts) -> RunObservation:
    base = {"has_process": True, "running": False, "returncode": None}
    return RunObservation(**{**base, **facts})


# (name, observation, expected state, expected reason)
ROWS = [
    ("idle", RunObservation(has_process=False, running=False, returncode=None), RunState.IDLE, None),
    ("running", _obs(running=True), RunState.RUNNING, None),
    ("running after a stop request", _obs(running=True, stop_requested=True), RunState.RUNNING, None),
    ("stop requested, then exit 1", _obs(returncode=1, stop_requested=True), RunState.STOPPED, None),
    ("stop requested, terminated by signal", _obs(returncode=-15, stop_requested=True), RunState.STOPPED, None),
    ("exit 0, live run", _obs(returncode=EXIT_OK, summary_seen=True), RunState.SUCCEEDED, None),
    ("exit 0, dry run", _obs(returncode=EXIT_OK, dry_run=True, summary_seen=True), RunState.SUCCEEDED, REASON_DRY_RUN),
    ("exit 0, nothing to do", _obs(returncode=EXIT_OK), RunState.SUCCEEDED, None),
    ("exit 1 with the summary", _obs(returncode=EXIT_MISSING, summary_seen=True), RunState.MISSING, None),
    ("exit 1 without the summary", _obs(returncode=EXIT_MISSING), RunState.FAILED, REASON_EXITED_BEFORE_SUMMARY),
    ("exit 2", _obs(returncode=EXIT_PROFILE_NOT_INITIALISED), RunState.FAILED, REASON_PROFILE_NOT_INITIALISED),
    ("exit 3", _obs(returncode=EXIT_UNCONFIRMED, summary_seen=True), RunState.UNCONFIRMED, None),
    ("an exit code nobody declared", _obs(returncode=7), RunState.FAILED, REASON_UNEXPECTED_EXIT),
    ("killed by a signal on its own", _obs(returncode=-9), RunState.FAILED, REASON_UNEXPECTED_EXIT),
]


@pytest.mark.parametrize(("name", "obs", "state", "reason"), ROWS, ids=[row[0] for row in ROWS])
def test_derive_run_state_table(name, obs, state, reason):
    result = derive_run_state(obs)
    assert (result.state, result.reason) == (state, reason), name


def test_failed_always_carries_a_reason():
    failed = [derive_run_state(obs) for _, obs, _, _ in ROWS]
    assert all(r.reason for r in failed if r.state is RunState.FAILED)


# state-machine-proof: cart-fill-run
@pytest.mark.parametrize(
    "obs",
    [
        _obs(running=True, returncode=0),
        _obs(running=True, returncode=1, stop_requested=True),
        _obs(running=False, returncode=None),
        RunObservation(has_process=False, running=True, returncode=None),
        RunObservation(has_process=False, running=False, returncode=1),
        RunObservation(has_process=False, running=False, returncode=None, stop_requested=True),
    ],
    ids=["running-with-code", "running-stopped-with-code", "finished-without-code", "no-process-running",
         "no-process-with-code", "no-process-stopped"],
)
def test_an_impossible_observation_is_refused(obs):
    with pytest.raises(ValueError):
        derive_run_state(obs)


# state-machine-proof: cart-fill-run
def test_stop_and_crash_share_an_exit_code_but_not_a_state():
    """The collision the machine exists for: both endings leave returncode 1 on Windows."""
    stopped = _obs(returncode=1, stop_requested=True)
    crashed = _obs(returncode=1)
    assert derive_run_state(stopped).state is RunState.STOPPED
    assert derive_run_state(crashed).state is RunState.FAILED
    assert derive_run_state(crashed).reason == REASON_EXITED_BEFORE_SUMMARY


# ---------------------------------------------------------------- through the API


@pytest.fixture()
def stand_in(client, monkeypatch):
    """Start/stop/poll the cart-fill run over the API, with a stand-in child in place of the automation."""
    automation._AUTOMATION_RUN.clear()
    _a_command = {"argv": [sys.executable, "-c", "pass"]}
    monkeypatch.setattr(automation_runner, "build_command", lambda *_a, **_k: _a_command["argv"])

    class Driver:
        def start(self, script: str, dry_run: bool = False) -> dict:
            _a_command["argv"] = [sys.executable, "-u", "-c", script]
            resp = client.post("/api/automation/start", json={"store": "all", "dry_run": dry_run})
            assert resp.status_code == 200, resp.text
            return resp.json()

        def stop(self) -> dict:
            return client.post("/api/automation/stop").json()

        def status(self) -> dict:
            return client.get("/api/automation/status").json()

        def finished(self, timeout: float = 20.0) -> dict:
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                body = self.status()
                if body["state"] != RunState.RUNNING.value:
                    return body
                time.sleep(0.1)
            raise AssertionError("the stand-in run did not finish")

    yield Driver()
    automation_runner.stop_run(automation._AUTOMATION_RUN.get("process"))
    automation._AUTOMATION_RUN.clear()


def _summary_then_exit(code: int) -> str:
    return f"print({SUMMARY_HEADER!r}); raise SystemExit({code})"


def test_status_is_idle_with_no_run(stand_in):
    body = stand_in.status()
    assert (body["state"], body["reason"], body["running"], body["returncode"]) == ("idle", None, False, None)


def test_a_running_run_reports_running_with_both_old_and_new_fields(stand_in):
    body = stand_in.start("import time; time.sleep(60)")
    assert (body["state"], body["running"], body["returncode"]) == ("running", True, None)


def test_a_stopped_run_is_stopped_not_missing(stand_in):
    stand_in.start("import time; time.sleep(60)")
    stand_in.stop()
    body = stand_in.finished()
    assert (body["state"], body["reason"]) == ("stopped", None)
    assert body["running"] is False and body["returncode"] is not None


def test_a_crash_before_the_summary_is_failed_not_missing(stand_in):
    stand_in.start("raise RuntimeError('boom')")
    body = stand_in.finished()
    assert (body["state"], body["reason"]) == ("failed", REASON_EXITED_BEFORE_SUMMARY)
    assert body["returncode"] == EXIT_MISSING


def test_a_miss_after_the_summary_is_missing(stand_in):
    stand_in.start(_summary_then_exit(EXIT_MISSING))
    assert stand_in.finished()["state"] == "missing"


def test_an_unconfirmed_cart_is_unconfirmed(stand_in):
    stand_in.start(_summary_then_exit(EXIT_UNCONFIRMED))
    assert stand_in.finished()["state"] == "unconfirmed"


def test_an_uninitialised_profile_is_failed_with_its_reason(stand_in):
    stand_in.start(f"raise SystemExit({EXIT_PROFILE_NOT_INITIALISED})")
    body = stand_in.finished()
    assert (body["state"], body["reason"]) == ("failed", REASON_PROFILE_NOT_INITIALISED)


def test_a_finished_dry_run_is_succeeded_with_the_dry_run_reason(stand_in):
    stand_in.start(_summary_then_exit(EXIT_OK), dry_run=True)
    body = stand_in.finished()
    assert (body["state"], body["reason"], body["dry_run"]) == ("succeeded", REASON_DRY_RUN, True)


def test_a_new_start_clears_an_earlier_stop(stand_in):
    stand_in.start("import time; time.sleep(60)")
    stand_in.stop()
    assert stand_in.finished()["state"] == "stopped"
    stand_in.start(_summary_then_exit(EXIT_MISSING))
    assert stand_in.finished()["state"] == "missing"


def test_stopping_a_finished_run_does_not_rewrite_its_ending(stand_in):
    stand_in.start(_summary_then_exit(EXIT_MISSING))
    assert stand_in.finished()["state"] == "missing"
    assert stand_in.stop()["state"] == "missing"


def test_the_event_stream_carries_the_state(stand_in):
    stand_in.start(_summary_then_exit(EXIT_UNCONFIRMED))
    stand_in.finished()

    async def first_event() -> dict:
        response = await automation.automation_events()
        chunk = await anext(response.body_iterator)
        return json.loads(chunk.removeprefix("data: "))

    event = asyncio.run(first_event())
    assert (event["state"], event["running"], event["returncode"]) == ("unconfirmed", False, EXIT_UNCONFIRMED)


def test_the_plumbing_alone_cannot_tell_a_stop_from_a_crash():
    """The pre-change reader had only the exit code; a stop and a crash left the same one here."""
    sleeper, _ = subprocess_plumbing.spawn_and_drain(
        [sys.executable, "-c", "import time; time.sleep(60)"], cwd=".", stderr=-2, on_line=lambda _l: None
    )
    subprocess_plumbing.stop(sleeper)
    crasher, _ = subprocess_plumbing.spawn_and_drain(
        [sys.executable, "-c", "raise RuntimeError('boom')"], cwd=".", stderr=-2, on_line=lambda _l: None
    )
    crasher.wait(timeout=20)
    if sys.platform == "win32":
        assert sleeper.returncode == crasher.returncode == 1
    else:
        assert sleeper.returncode != crasher.returncode
