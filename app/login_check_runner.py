"""Subprocess plumbing for the read-only store-login check (issue #217).

Runs ``python -m automation.check_logins --json`` to completion and returns its
per-store result. Out of process for the same reason as the product search:
sync Playwright cannot run inside the async uvicorn worker, and the child
serialises against the cart automation via
``browser.launch_context(wait_for_profile=True)``.

The child's stderr (its ``logging`` lines) is written to :data:`LOG_PATH` — the
webapp runs with no console, so a failed check stays diagnosable.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from src.no_window import NO_WINDOW

_REPO_ROOT = Path(__file__).resolve().parent.parent
# Last run's log (gitignored via ``*.log``), overwritten each run.
LOG_PATH = _REPO_ROOT / "logs" / "check_logins.log"
# Generous per-store page-load allowance once the shared profile is free
# (bot-challenge retries included).
_PAGE_LOAD_ALLOWANCE_S = 60
# Only used if automation.run_automation.HANDLERS can't be imported:
# mercadona, ametller, carrefour.
_FALLBACK_STORE_COUNT = 3


def _timeout_s() -> int:
    """Profile wait-backoff schedule + one page-load allowance per store.

    Derived from ``automation.browser``'s actual backoff schedule, rather than
    a literal copy of it, so the two cannot drift apart again (issue #339):
    this timed out at 360s while the schedule it was meant to cover grew to
    900s.
    """
    from automation import browser
    try:
        from automation.run_automation import HANDLERS
        store_count = len(HANDLERS)
    except ImportError:
        store_count = _FALLBACK_STORE_COUNT
    return sum(browser._PROFILE_WAIT_BACKOFF_S) + _PAGE_LOAD_ALLOWANCE_S * store_count


class LoginCheckError(RuntimeError):
    """The login check did not produce a result."""


def build_command() -> list[str]:
    return [sys.executable, "-u", "-m", "automation.check_logins", "--json"]


def run() -> dict[str, dict[str, str]]:
    """Run the check and return ``{store: {"state", "detail"}}``.

    Raises:
        LoginCheckError: the check timed out, or exited without JSON on stdout.
    """
    timeout_s = _timeout_s()
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("w", encoding="utf-8") as log:
        try:
            proc = subprocess.run(
                build_command(), cwd=str(_REPO_ROOT), stdout=subprocess.PIPE, stderr=log,
                text=True, encoding="utf-8", errors="replace", timeout=timeout_s,
                env={**os.environ, "PYTHONUTF8": "1"}, creationflags=NO_WINDOW,
            )
        except subprocess.TimeoutExpired as exc:
            raise LoginCheckError(
                f"login check did not finish within {timeout_s}s — see {LOG_PATH.name}"
            ) from exc
    try:
        result = json.loads(proc.stdout.strip().splitlines()[-1])
    except (IndexError, ValueError) as exc:
        raise LoginCheckError(
            f"login check exited {proc.returncode} without a result — see {LOG_PATH.name}"
        ) from exc
    if not isinstance(result, dict):
        raise LoginCheckError(f"login check printed no per-store result — see {LOG_PATH.name}")
    return result
