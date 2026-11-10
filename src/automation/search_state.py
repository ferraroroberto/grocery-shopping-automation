"""The spoken product search's lifecycle, declared once.

A search is a child process that streams one event per store as each changes state
and prints a final result line. Two lifecycles ride on it, and this module is the
only place either is spelled out:

* the **run** (:class:`SearchState`): nothing registered, in flight, finished with a
  result, finished without one. :func:`derive_search_state` maps one observation of
  the process and its output onto it.
* each **store** within a run (:class:`StoreState`): waiting for the shared browser,
  searching, done, failed. The child process emits these; :func:`derive_store_state`
  settles one that never reached an ending once the run is over.

The status route, the PWA and the child process all read these types, so a client
never compares a state string of its own. ``docs/product-search-state.md`` has the
state tables and the rule for readers.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Optional

# Failure reasons a store carries (the UI words each one).
REASON_SESSION = "session"  # the store login expired
REASON_TIMEOUT = "timeout"  # the site was too slow
REASON_ERROR = "error"  # any other network or site error
REASON_STOPPED = "stopped"  # the run ended before this store finished

NO_RESULT_ERROR = "the search did not return any result"
STOPPED_ERROR = "the search stopped before this store finished"


class SearchState(str, Enum):
    """Every state a product-search run can be in. The set is closed."""

    IDLE = "idle"  # no run registered yet
    RUNNING = "running"  # the process is alive
    DONE = "done"  # finished and printed a result
    ERROR = "error"  # finished without a usable result; carries a message


class StoreState(str, Enum):
    """Every state one store can be in during a run. The set is closed."""

    WAITING = "waiting"  # the browser is not up yet
    SEARCHING = "searching"  # the store's search is in flight
    DONE = "done"  # answered; an empty list is a genuine no-match
    FAILED = "failed"  # could not search; carries a reason


# A store in one of these has not answered yet.
IN_FLIGHT_STORE_STATES = frozenset({StoreState.WAITING, StoreState.SEARCHING})


@dataclass(frozen=True)
class SearchObservation:
    """One reading of the run's facts, taken once and passed whole to the derive."""

    has_run: bool
    running: bool
    result_seen: bool = False
    result_error: Optional[str] = None


@dataclass(frozen=True)
class SearchStateResult:
    state: SearchState
    error: Optional[str] = None


def derive_search_state(obs: SearchObservation) -> SearchStateResult:
    """The state for one observation; a combination that cannot happen raises ``ValueError``."""
    if not obs.has_run:
        if obs.running or obs.result_seen or obs.result_error:
            raise ValueError(f"no run registered, yet it reports activity: {obs}")
        return SearchStateResult(SearchState.IDLE)
    if obs.result_error and not obs.result_seen:
        raise ValueError(f"a result error needs a result line: {obs}")
    if obs.running:
        # The result line can land a moment before the process exits; the run is
        # still in flight until it does.
        return SearchStateResult(SearchState.RUNNING)
    if not obs.result_seen:
        return SearchStateResult(SearchState.ERROR, NO_RESULT_ERROR)
    if obs.result_error:
        return SearchStateResult(SearchState.ERROR, obs.result_error)
    return SearchStateResult(SearchState.DONE)


@dataclass(frozen=True)
class StoreStateResult:
    state: StoreState
    reason: Optional[str] = None
    error: Optional[str] = None

    @property
    def in_flight(self) -> bool:
        return self.state in IN_FLIGHT_STORE_STATES

    @property
    def waiting(self) -> bool:
        return self.state is StoreState.WAITING

    @property
    def failed(self) -> bool:
        return self.state is StoreState.FAILED


def derive_store_state(
    event_state: str, reason: Optional[str], error: Optional[str], *, run_finished: bool
) -> StoreStateResult:
    """A store's state from its latest event; an unknown event state raises ``ValueError``.

    A store still in flight once the run has ended (crash, cancel) is ``failed`` with
    reason ``stopped``, so it never reads as still searching.
    """
    state = StoreState(event_state)
    if run_finished and state in IN_FLIGHT_STORE_STATES:
        return StoreStateResult(StoreState.FAILED, REASON_STOPPED, STOPPED_ERROR)
    return StoreStateResult(state, reason, error)
