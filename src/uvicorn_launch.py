"""Shared webapp process lifecycle: the ``app.webapp.serve`` command line and the
stop sequence every launcher that spawns it — the tray (``app/tray/manager.py``),
the named-tunnel launcher (``scripts/run_named_tunnel.py``) and the manual
``webapp.bat`` (through :func:`main`) — needs the same way. Each caller keeps
its own spawn call (different cwd/env/stdout handling), host and adopt-or-spawn
policy.
"""

from __future__ import annotations

import logging
import signal
import subprocess
import sys
from pathlib import Path
from typing import List, Optional

from src.certs import PROJECT_ROOT, cert_paths
from src.webapp_config import load_webapp_config

logger = logging.getLogger(__name__)


def build_webapp_command(
    host: str,
    port: int,
    *,
    project_root: Optional[Path] = None,
    python: Optional[Path] = None,
    server: str = "hypercorn",
) -> List[str]:
    """``<python> -m app.webapp.serve --app app.api:app --host <host> --port <port>
    --log-level warning --server <server>``, plus ``--ssl-keyfile``/``--ssl-certfile`` when a
    cert pair exists for ``project_root`` (``src.certs.cert_paths``)."""
    cmd: List[str] = [
        str(python or sys.executable),
        "-m",
        "app.webapp.serve",
        "--app",
        "app.api:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "warning",
        "--server",
        server,
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


def main() -> int:
    """Serve the webapp in the foreground on the host, port and server that
    ``config/webapp_config.json`` names, over HTTPS when a cert pair exists.

    This is ``webapp.bat``'s serve step: the same command the tray builds, so
    the manual launcher cannot drift from it. The server inherits this console,
    which is the point of a manual launch.
    """
    cfg = load_webapp_config()
    cmd = build_webapp_command(cfg.host, cfg.port, project_root=PROJECT_ROOT, server=cfg.server)
    scheme = "HTTPS" if "--ssl-certfile" in cmd else "HTTP"
    print(f"[INFO] Starting {scheme} FastAPI webapp on {cfg.host}:{cfg.port} ({cfg.server}).", flush=True)
    try:
        # no-window-exempt: a manual launch serves in the operator's own console
        # subprocess-timeout-exempt: the server runs until the operator stops it
        return subprocess.call(cmd, cwd=str(PROJECT_ROOT))
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
