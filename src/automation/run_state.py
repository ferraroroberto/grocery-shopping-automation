"""The cart-fill run's lifecycle, declared once.

A run is a child process (``python -m src.automation.run_automation``), and the
facts anyone can observe about it are few: is there a process, is it still
running, what exit code did it leave, did the user ask to stop it, was it a dry
run, and did it print its end-of-run summary. :func:`derive_run_state` is the only
code that turns those facts into a :class:`RunState`; the API, the PWA and the
legacy Streamlit mode all read the result instead of re-deriving a mode from an
exit code. The exit code alone cannot say which ending it was: a run stopped by
the user (``Popen.terminate()`` on Windows), an uncaught crash and "some item is
not in the cart" all leave ``1``.

See ``docs/cart-fill-run-state.md`` for the state table and the rule for readers.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import Enum
from typing import Optional

# The run's exit codes, defined here so the writer (run_automation) and every
# reader share one meaning for each.
EXIT_OK = 0  # every item verified in the cart (or, in a dry run, listed)
EXIT_MISSING = 1  # finished, some item is not in the cart
EXIT_PROFILE_NOT_INITIALISED = 2  # the shared Chrome profile has no login yet
EXIT_UNCONFIRMED = 3  # added, but the store cart could not be read

# The first line of the report the run prints last. Seeing it means the run got
# as far as judging the cart, which is what tells exit 1 "missing" from a crash.
SUMMARY_HEADER = "── Cart automation summary ──"


class RunState(str, Enum):
    """Every state a cart-fill run can be in. The set is closed."""

    IDLE = "idle"  # no run registered
    RUNNING = "running"  # the process is alive
    STOPPED = "stopped"  # the user stopped it
    SUCCEEDED = "succeeded"  # every item verified; a dry run listed what it would add
    MISSING = "missing"  # finished, some item is not in the cart
    UNCONFIRMED = "unconfirmed"  # finished, added, but the cart could not be read
    FAILED = "failed"  # crashed or could not finish; carries a reason


# Reasons. `failed` always carries one; `succeeded` carries DRY_RUN for a dry run.
REASON_DRY_RUN = "dry-run"
REASON_PROFILE_NOT_INITIALISED = "profile-not-initialised"
REASON_EXITED_BEFORE_SUMMARY = "exited-before-summary"
REASON_UNEXPECTED_EXIT = "unexpected-exit"

# States a run ends in: nothing more will happen until the user dismisses it.
FINISHED_STATES = frozenset(
    {RunState.STOPPED, RunState.SUCCEEDED, RunState.MISSING, RunState.UNCONFIRMED, RunState.FAILED}
)


@dataclass(frozen=True)
class RunObservation:
    """One reading of the run's facts, taken once and passed whole to the derive."""

    has_process: bool
    running: bool
    returncode: Optional[int]
    stop_requested: bool = False
    dry_run: bool = False
    summary_seen: bool = False


@dataclass(frozen=True)
class RunStateResult:
    state: RunState
    reason: Optional[str] = None


def derive_run_state(obs: RunObservation) -> RunStateResult:
    """The state for one observation; a combination that cannot happen raises ``ValueError``."""
    if not obs.has_process:
        if obs.running or obs.returncode is not None or obs.stop_requested:
            raise ValueError(f"no process, yet the run reports activity: {obs}")
        return RunStateResult(RunState.IDLE)
    if obs.running:
        if obs.returncode is not None:
            raise ValueError(f"a running process cannot have a return code: {obs}")
        return RunStateResult(RunState.RUNNING)
    if obs.returncode is None:
        raise ValueError(f"a finished process has no return code: {obs}")
    if obs.returncode == EXIT_OK:
        # A stop that arrived as the run finished cleanly changes nothing.
        return RunStateResult(RunState.SUCCEEDED, REASON_DRY_RUN if obs.dry_run else None)
    if obs.stop_requested:
        return RunStateResult(RunState.STOPPED)
    if obs.returncode == EXIT_MISSING:
        if obs.summary_seen:
            return RunStateResult(RunState.MISSING)
        return RunStateResult(RunState.FAILED, REASON_EXITED_BEFORE_SUMMARY)
    if obs.returncode == EXIT_PROFILE_NOT_INITIALISED:
        return RunStateResult(RunState.FAILED, REASON_PROFILE_NOT_INITIALISED)
    if obs.returncode == EXIT_UNCONFIRMED:
        return RunStateResult(RunState.UNCONFIRMED)
    return RunStateResult(RunState.FAILED, REASON_UNEXPECTED_EXIT)


def summary_printed(lines: Iterable[str]) -> bool:
    """True when the run's output carries its end-of-run summary header."""
    return any(line == SUMMARY_HEADER for line in lines)
