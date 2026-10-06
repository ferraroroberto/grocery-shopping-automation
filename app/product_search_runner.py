"""Subprocess plumbing for the on-demand product search (issue #87).

Runs ``python -m automation.product_search`` in the background and captures its
JSON stdout. Kept UI-agnostic and separate from ``automation_runner`` (which
streams the cart automation's *line* output) because this one wants the whole
stdout parsed as a single JSON document at the end.

The search drives real Chrome on the shared profile, so it must run out of
process — sync Playwright cannot run inside the async uvicorn worker, and a
subprocess also serialises cleanly against the cart automation via
``browser.launch_context(wait_for_profile=True)``.

The child's stderr (its ``logging`` output: per-store phase + elapsed) is
appended to :data:`LOG_PATH`, as are the router's own parse-phase notes via
:func:`note` — the webapp runs with no console, so without this file a hung
search leaves nothing to read (issue #211).
"""

from __future__ import annotations

import json
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from app import subprocess_plumbing

_REPO_ROOT = Path(__file__).resolve().parent.parent

# Run log (gitignored via ``*.log``). Rolled to ``.1`` past the cap so it never
# grows without bound but still holds the last few runs.
LOG_PATH = _REPO_ROOT / "logs" / "product_search.log"
_LOG_MAX_BYTES = 1_000_000

# Re-exported so existing callers (app/api.py) keep working unchanged.
is_running = subprocess_plumbing.is_running
stop = subprocess_plumbing.stop


def build_command(queries: list[str], limit: int) -> list[str]:
    """Return the argv for a search over ``queries`` (one ``--query`` each)."""
    cmd = [sys.executable, "-u", "-m", "automation.product_search", "--json", "--limit", str(limit)]
    for q in queries:
        cmd += ["--query", q]
    return cmd


def _rotate_log() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    if LOG_PATH.exists() and LOG_PATH.stat().st_size > _LOG_MAX_BYTES:
        LOG_PATH.replace(LOG_PATH.with_suffix(".log.1"))


def note(message: str) -> None:
    """Append one timestamped line to :data:`LOG_PATH` (best effort)."""
    try:
        _rotate_log()
        with LOG_PATH.open("a", encoding="utf-8") as fh:
            fh.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - app.product_search - {message}\n")
    except OSError:
        pass  # a log write must never fail the search


def start(queries: list[str], limit: int) -> tuple[subprocess.Popen, list[str], threading.Thread]:
    """Spawn the search subprocess, draining its stdout into a list of chunks.

    Returns ``(process, stdout_chunks, reader_thread)``. ``stdout_chunks`` is a
    plain list the reader thread appends to; join it to recover the full JSON
    document once the process exits. The child's stderr goes to
    :data:`LOG_PATH`; stdout stays pure NDJSON.
    """
    chunks: list[str] = []
    _rotate_log()
    # The child inherits its own handle; ours closes once Popen has duplicated it.
    with LOG_PATH.open("a", encoding="utf-8") as log:
        process, reader = subprocess_plumbing.spawn_and_drain(
            build_command(queries, limit),
            cwd=str(_REPO_ROOT),
            stderr=log.fileno(),
            on_line=chunks.append,
        )
    return process, chunks, reader


def _events(chunks: list[str]):
    """Yield the parsed NDJSON event objects from the collected stdout lines."""
    for line in "".join(chunks).splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def parse_result(chunks: list[str]) -> Optional[dict[str, Any]]:
    """Return the engine's final result dict, or None if not emitted yet.

    The engine streams NDJSON: ``{"event":"progress",…}`` lines while it runs,
    then one ``{"event":"result","result":{…}}`` line at the end. None means the
    result line hasn't arrived (still running, or the process crashed before
    printing), so the caller can report an error rather than a partial parse.
    """
    result = None
    for event in _events(chunks):
        if event.get("event") == "result":
            result = event.get("result")
    return result


def store_states(chunks: list[str]) -> dict[str, dict[str, dict[str, Any]]]:
    """Return ``{query: {store: latest store event}}`` from the streamed events.

    Store order follows first appearance (the engine's own run order), so the
    caller can list stores without knowing the engine's store set (issue #211).
    """
    states: dict[str, dict[str, dict[str, Any]]] = {}
    for event in _events(chunks):
        if event.get("event") == "store" and event.get("store"):
            states.setdefault(str(event.get("query") or ""), {})[event["store"]] = event
    return states


def latest_progress(chunks: list[str]) -> Optional[str]:
    """Return the most recent progress message emitted so far, or None."""
    message = None
    for event in _events(chunks):
        if event.get("event") == "progress":
            message = event.get("message")
    return message
