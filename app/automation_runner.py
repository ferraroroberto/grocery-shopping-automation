"""Subprocess plumbing for running the cart automation from the app.

UI-agnostic on purpose: this module knows how to spawn
``python -m automation.run_automation``, drain its merged stdout/stderr into a
bounded buffer on a background thread, and stop it cleanly. It imports no UI
framework and is driven by both front ends — the FastAPI app (``app/api.py``)
and the legacy Streamlit mode (``app/shopping.py``).

The reader thread only ever *appends to* the ``deque`` object handed back to the
caller; it never reaches into any UI state. The caller holds that same deque,
so the request/script side and the thread share one object without the thread
going through a UI framework's (thread-unsafe) state API.
"""

from __future__ import annotations

import subprocess
import sys
import threading
from collections import deque
from pathlib import Path

from app import subprocess_plumbing

# The automation package and config.json resolve relative to the repo root;
# the subprocess must run from there so relative paths behave like a terminal run.
_REPO_ROOT = Path(__file__).resolve().parent.parent

# Keep only the most recent lines — a long run can print thousands.
MAX_OUTPUT_LINES = 500

# Re-exported so existing callers (app/api.py, app/app.py, app/shopping.py)
# keep working unchanged.
is_running = subprocess_plumbing.is_running


def build_command(store: str, dry_run: bool, cart_mode: str = "keep") -> list[str]:
    """Return the argv for an automation run.

    Args:
        store: Store key (e.g. ``"mercadona"``) or ``"all"`` for every store.
        dry_run: When true, append ``--dry-run`` (no browser is opened).
        cart_mode: ``"keep"`` (add on top of the existing cart) or ``"clean"``
            (empty the cart first). Always passed through as ``--cart-mode`` so
            the chosen mode is explicit in the command preview.

    The child Python runs with ``-u`` so its stdout is unbuffered and the
    reader thread sees each line as it is printed.
    """
    cmd = [sys.executable, "-u", "-m", "automation.run_automation"]
    if store and store != "all":
        cmd += ["--store", store]
    if dry_run:
        cmd.append("--dry-run")
    cmd += ["--cart-mode", cart_mode]
    return cmd


def start_run(
    store: str, dry_run: bool, cart_mode: str = "keep"
) -> tuple[subprocess.Popen, "deque[str]", threading.Thread]:
    """Spawn the automation subprocess and a thread draining its output.

    Returns ``(process, output_lines, reader_thread)``. ``output_lines`` is a
    ``deque`` bounded to :data:`MAX_OUTPUT_LINES`; the reader thread appends to
    it line by line and exits when the process closes its stdout.
    """
    output_lines: "deque[str]" = deque(maxlen=MAX_OUTPUT_LINES)
    process, reader_thread = subprocess_plumbing.spawn_and_drain(
        build_command(store, dry_run, cart_mode),
        cwd=str(_REPO_ROOT),
        stderr=subprocess.STDOUT,
        bufsize=1,
        on_line=lambda line: output_lines.append(line.rstrip("\n")),
    )
    return process, output_lines, reader_thread


def stop_run(process: "subprocess.Popen | None") -> None:
    """Terminate a run, escalating to kill() after a short grace period."""
    subprocess_plumbing.stop(process)
