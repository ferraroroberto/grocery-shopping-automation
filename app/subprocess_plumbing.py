"""Shared spawn/drain/stop plumbing for background subprocess-backed runners.

Factored out of ``app/automation_runner.py`` and ``app/product_search_runner.py``
(issue #95): both modules build a ``python -m <module>`` argv, spawn it with
``PYTHONUTF8`` forced and ``cwd`` set to the repo root, drain its stdout on a
background thread into a buffer the caller owns, expose an ``is_running()``
check, and stop the process with the same terminate/wait-5s/kill escalation.
This module owns that common shape; call-site-specific bits (argv assembly,
buffer type — a bounded deque of stripped lines vs. a plain list of raw
chunks — and stderr routing) stay in the callers.
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
from typing import Callable

# How long to wait after terminate() before escalating to kill().
_STOP_GRACE_S = 5.0


def spawn_and_drain(
    cmd: list[str],
    *,
    cwd: str,
    stderr: int,
    on_line: Callable[[str], None],
    bufsize: int = -1,
) -> tuple[subprocess.Popen, threading.Thread]:
    """Spawn ``cmd`` and drain its stdout on a daemon thread.

    Forces UTF-8 on both sides: ``PYTHONUTF8`` makes the child encode its
    output as UTF-8 even though stdout is a pipe (not a console), and reading
    with ``encoding="utf-8"`` lets the reader thread decode it without
    mojibake; ``errors="replace"`` keeps a stray byte from ever killing the
    drain thread.

    Calls ``on_line(line)`` for each line read (as yielded by iterating the
    pipe, newline included) until the process closes stdout, then closes the
    pipe on this side too. Returns ``(process, reader_thread)``; the thread is
    already started.
    """
    child_env = {**os.environ, "PYTHONUTF8": "1"}
    process = subprocess.Popen(
        cmd,
        cwd=cwd,
        stdout=subprocess.PIPE,
        stderr=stderr,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=bufsize,
        env=child_env,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )

    def _drain() -> None:
        assert process.stdout is not None
        for line in process.stdout:
            on_line(line)
        process.stdout.close()

    reader_thread = threading.Thread(target=_drain, daemon=True)
    reader_thread.start()
    return process, reader_thread


def is_running(process: "subprocess.Popen | None") -> bool:
    """True when `process` exists and has not exited yet."""
    return process is not None and process.poll() is None


def stop(process: "subprocess.Popen | None") -> None:
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
