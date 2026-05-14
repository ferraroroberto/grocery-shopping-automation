"""Subprocess plumbing for running the cart automation from the Streamlit app.

UI-agnostic on purpose: this module knows how to spawn
``python -m automation.run_automation``, drain its merged stdout/stderr into a
bounded buffer on a background thread, and stop it cleanly. It imports no
``streamlit`` — all widgets and the rerun loop live in ``app/shopping.py``.

The reader thread only ever *appends to* the ``deque`` object handed back to the
caller; it never touches ``st.session_state``. The caller stores that same
deque in session state, so the Streamlit script and the thread share one
object without the thread going through the (thread-unsafe) session-state API.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

# The automation package and config.json resolve relative to the repo root;
# the subprocess must run from there so relative paths behave like a terminal run.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Keep only the most recent lines — a long run can print thousands.
MAX_OUTPUT_LINES = 500

# How long to wait after terminate() before escalating to kill().
_STOP_GRACE_S = 5.0


def build_command(store: str, dry_run: bool) -> list[str]:
    """Return the argv for an automation run.

    Args:
        store: Store key (e.g. ``"mercadona"``) or ``"all"`` for every store.
        dry_run: When true, append ``--dry-run`` (no browser is opened).

    The child Python runs with ``-u`` so its stdout is unbuffered and the
    reader thread sees each line as it is printed.
    """
    cmd = [sys.executable, "-u", "-m", "automation.run_automation"]
    if store and store != "all":
        cmd += ["--store", store]
    if dry_run:
        cmd.append("--dry-run")
    return cmd


def start_run(
    store: str, dry_run: bool
) -> tuple[subprocess.Popen, "deque[str]", threading.Thread]:
    """Spawn the automation subprocess and a thread draining its output.

    Returns ``(process, output_lines, reader_thread)``. ``output_lines`` is a
    ``deque`` bounded to :data:`MAX_OUTPUT_LINES`; the reader thread appends to
    it line by line and exits when the process closes its stdout.
    """
    process = subprocess.Popen(
        build_command(store, dry_run),
        cwd=str(_REPO_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
    )
    output_lines: "deque[str]" = deque(maxlen=MAX_OUTPUT_LINES)

    def _drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            output_lines.append(line.rstrip("\n"))
        process.stdout.close()

    reader_thread = threading.Thread(target=_drain, daemon=True)
    reader_thread.start()
    return process, output_lines, reader_thread


def is_running(process: "subprocess.Popen | None") -> bool:
    """True when `process` exists and has not exited yet."""
    return process is not None and process.poll() is None


def stop_run(process: "subprocess.Popen | None") -> None:
    """Terminate a run, escalating to kill() after a short grace period."""
    if not is_running(process):
        return
    assert process is not None
    process.terminate()
    try:
        process.wait(timeout=_STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
