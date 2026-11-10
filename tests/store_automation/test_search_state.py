"""The product search's declared state: the derive tables, and the status route's derived flags.

No store is touched and no browser opens; the status route is fed stand-in child
processes and canned stream events.
"""

import json

import pytest

import app.webapp.routers.product_search as search_api
from src.automation.search_state import (
    NO_RESULT_ERROR,
    REASON_STOPPED,
    SearchObservation,
    SearchState,
    StoreState,
    derive_search_state,
    derive_store_state,
)

# (name, observation, state, error)
RUN_ROWS = [
    ("nothing registered", SearchObservation(False, False), SearchState.IDLE, None),
    ("in flight", SearchObservation(True, True), SearchState.RUNNING, None),
    ("in flight, result line already printed", SearchObservation(True, True, result_seen=True), SearchState.RUNNING, None),
    ("finished with a result", SearchObservation(True, False, result_seen=True), SearchState.DONE, None),
    ("finished without a result", SearchObservation(True, False), SearchState.ERROR, NO_RESULT_ERROR),
    ("finished with a reported error", SearchObservation(True, False, True, "not logged in"), SearchState.ERROR, "not logged in"),
]


@pytest.mark.parametrize(("name", "obs", "state", "error"), RUN_ROWS, ids=[row[0] for row in RUN_ROWS])
def test_derive_search_state_table(name, obs, state, error):
    result = derive_search_state(obs)
    assert (result.state, result.error) == (state, error), name


def test_an_error_state_always_carries_a_message():
    assert all(r.error for r in (derive_search_state(row[1]) for row in RUN_ROWS) if r.state is SearchState.ERROR)


# state-machine-proof: product-search-run
@pytest.mark.parametrize(
    "obs",
    [
        SearchObservation(False, True),
        SearchObservation(False, False, result_seen=True),
        SearchObservation(False, False, False, "stray error"),
        SearchObservation(True, False, result_seen=False, result_error="error without a result"),
    ],
    ids=["no-run-running", "no-run-with-result", "no-run-with-error", "error-without-result"],
)
def test_an_impossible_run_observation_is_refused(obs):
    with pytest.raises(ValueError):
        derive_search_state(obs)


# state-machine-proof: product-search-run
def test_an_unknown_store_state_is_refused():
    with pytest.raises(ValueError):
        derive_store_state("loading", None, None, run_finished=False)


@pytest.mark.parametrize("event_state", [StoreState.WAITING.value, StoreState.SEARCHING.value])
def test_a_store_in_flight_when_the_run_ended_is_failed_stopped(event_state):
    got = derive_store_state(event_state, None, None, run_finished=True)
    assert (got.state, got.reason) == (StoreState.FAILED, REASON_STOPPED)
    assert got.error


@pytest.mark.parametrize("finished", [False, True])
def test_a_store_that_ended_keeps_its_own_outcome(finished):
    done = derive_store_state(StoreState.DONE.value, None, None, run_finished=finished)
    failed = derive_store_state(StoreState.FAILED.value, "timeout", "slow", run_finished=finished)
    assert (done.state, done.reason) == (StoreState.DONE, None)
    assert (failed.state, failed.reason, failed.error) == (StoreState.FAILED, "timeout", "slow")


def test_store_flags_follow_the_state():
    flags = {
        s: (r.in_flight, r.waiting, r.failed)
        for s in StoreState
        for r in [derive_store_state(s.value, None, None, run_finished=False)]
    }
    assert flags == {
        StoreState.WAITING: (True, True, False),
        StoreState.SEARCHING: (True, False, False),
        StoreState.DONE: (False, False, False),
        StoreState.FAILED: (False, False, True),
    }


class _Proc:
    def __init__(self, alive):
        self._alive = alive

    def poll(self):
        return None if self._alive else 0


def _store_event(store, state):
    return json.dumps({"event": "store", "query": "milk", "store": store, "state": state,
                       "candidates": [], "error": None, "reason": None, "elapsed_s": 0.0}) + "\n"


@pytest.fixture()
def _run(monkeypatch):
    def put(*, alive, chunks):
        search_api._SEARCH_RUN.clear()
        search_api._SEARCH_RUN.update({"id": "r1", "process": _Proc(alive), "chunks": chunks,
                                       "items": [{"term": "milk"}], "started_at": 1.0})

    yield put
    search_api._SEARCH_RUN.clear()


def test_the_status_carries_the_derived_flags(_run):
    assert search_api._search_status()["idle"] is True
    _run(alive=True, chunks=[_store_event("mercadona", "waiting"), _store_event("dia", "searching")])
    status = search_api._search_status()
    assert (status["state"], status["idle"], status["running"], status["failed"]) == ("running", False, True, False)
    assert [(r["in_flight"], r["waiting"], r["failed"]) for r in status["items"][0]["stores"]] == [
        (True, True, False), (True, False, False)]


def test_a_crashed_run_reports_unfinished_stores_stopped_and_the_run_failed(_run):
    _run(alive=False, chunks=[_store_event("mercadona", "searching")])
    status = search_api._search_status()
    assert (status["state"], status["failed"], status["error"]) == ("error", True, NO_RESULT_ERROR)
    row = status["items"][0]["stores"][0]
    assert (row["state"], row["reason"], row["failed"], row["in_flight"]) == ("failed", REASON_STOPPED, True, False)
