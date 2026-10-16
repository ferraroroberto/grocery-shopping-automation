"""Shared uvicorn process lifecycle: the ``app.api:app`` command line and the
stop sequence every launcher that spawns it — the tray (``app/tray/manager.py``)
and the named-tunnel launcher (``scripts/run_named_tunnel.py``) — needs the
same way (#294). Each caller keeps its own Popen call (different cwd/env/
stdout handling), host and adopt-or-spawn policy.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from src.certs import cert_paths

logger = logging.getLogger(__name__)


def build_uvicorn_command(
    host: str,
    port: int,
    *,
    project_root: Optional[Path] = None,
    python: Optional[Path] = None,
) -> List[str]:
    """``<python> -m uvicorn app.api:app --host <host> --port <port>
    --log-level warning``, plus ``--ssl-keyfile``/``--ssl-certfile`` when a
    cert pair exists for ``project_root`` (``src.certs.cert_paths``)."""
    cmd: List[str] = [
        str(python or sys.executable),
        "-m",
        "uvicorn",
        "app.api:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    certs = cert_paths(project_root)
    if certs is not None:
        cert, key = certs
        cmd.extend(["--ssl-keyfile", str(key), "--ssl-certfile", str(cert)])
    return cmd


def stop_process(proc: subprocess.Popen, name: str) -> None:
    """Stop ``proc``: CTRL_BREAK (Windows) → terminate → kill after 5s.
    Best-effort; any failure is logged at debug, never raised."""
    try:
        logger.info(f"Stopping {name} (pid={proc.pid})")
        if sys.platform == "win32":
            try:
                proc.send_signal(signal.CTRL_BREAK_EVENT)
            except Exception:  # noqa: BLE001
                pass
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
    except Exception as exc:  # noqa: BLE001
        logger.debug(f"{name} stop failed: {exc}")
