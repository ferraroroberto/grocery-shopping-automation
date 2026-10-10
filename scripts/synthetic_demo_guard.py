"""Runtime guard for the design-review demo instance (#251): stdlib only.

The demo's stubs (``synthetic_demo_app``) replace every route that would
launch a process, reach a store, mail, the LLM hub or whisper. This guard is
the second line: a ``sys.addaudithook`` that *refuses* (and logs) anything a
stub missed, so a forgotten side effect fails loudly instead of acting.

Refused: any process launch, ``os.startfile`` / ``webbrowser.open``, a
connection to anything but a loopback address, a connection to a port a real
service owns on this machine (hub, whisper, voice transcriber, the live
webapp), a lookup of a non-loopback host name, opening a *protected* path (the
real spreadsheet and the household's config/state), and writing outside the
instance's temp root.

The refusal is a ``PermissionError`` and one JSON line in ``SYNTHETIC_GUARD_LOG``
(read by the test and by anyone debugging a walk).
"""
from __future__ import annotations

import json
import os
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional, Tuple

ENV_ROOT = "SYNTHETIC_ROOT"
ENV_PROTECT = "SYNTHETIC_PROTECT"       # os.pathsep-joined real paths that must never be opened
ENV_LOG = "SYNTHETIC_GUARD_LOG"

LOOPBACK = {"127.0.0.1", "::1", "localhost", ""}
# Real services on this machine the demo must never reach, even over loopback:
# local-llm-hub, whisper, voice-transcriber, session-host, the live webapp and the legacy app.
REAL_SERVICE_PORTS = frozenset({8000, 8090, 8091, 8443, 8446, 8501, 8502})
LAUNCH_EVENTS = frozenset({
    "subprocess.Popen", "os.system", "os.startfile", "os.spawn", "os.posix_spawn", "os.exec", "webbrowser.open",
})
_WRITE_MODE = set("wax+")
_WRITE_FLAGS = os.O_WRONLY | os.O_RDWR | os.O_APPEND | os.O_CREAT | os.O_TRUNC


@dataclass(frozen=True)
class Policy:
    root: Path                          # the instance's temp tree: the only place it may write
    protected: Tuple[Path, ...] = ()    # real data: never opened, read or write


def _norm(path: object) -> Optional[Path]:
    if isinstance(path, bytes):
        path = os.fsdecode(path)
    if not isinstance(path, (str, os.PathLike)) or isinstance(path, int):
        return None
    try:
        return Path(os.path.realpath(os.path.abspath(os.fspath(path))))
    except (OSError, ValueError):
        return None


def _within(path: Path, parent: Path) -> bool:
    return path == parent or parent in path.parents


def _writes(mode: object, flags: object) -> bool:
    if isinstance(mode, str) and _WRITE_MODE & set(mode):
        return True
    return isinstance(flags, int) and bool(flags & _WRITE_FLAGS)


def _address_problem(address: object) -> Optional[str]:
    host, port = (address[0], address[1]) if isinstance(address, tuple) and len(address) >= 2 else (address, None)
    host = os.fsdecode(host) if isinstance(host, bytes) else str(host)
    if host not in LOOPBACK:
        return f"connection to non-loopback address {host}"
    if port in REAL_SERVICE_PORTS:
        return f"connection to a real service port {port}"
    return None


def decide(event: str, args: tuple, policy: Policy) -> Optional[str]:
    """Why ``event`` must be refused under ``policy``, or None to let it through."""
    if event in LAUNCH_EVENTS:
        return f"{event} (no process, window or browser may start)"
    if event == "socket.connect":
        return _address_problem(args[1]) if len(args) > 1 else None
    if event == "socket.getaddrinfo":
        host = args[0]
        host = os.fsdecode(host) if isinstance(host, bytes) else str(host or "")
        return None if host in LOOPBACK else f"name lookup of {host}"
    if event == "open":
        path = _norm(args[0]) if args else None
        if path is None:
            return None
        if any(_within(path, p) for p in policy.protected):
            return f"open of protected real data {path}"
        mode, flags = (args[1], args[2]) if len(args) > 2 else (None, None)
        if _writes(mode, flags) and not _within(path, policy.root) and path.name.lower() not in {"nul", "conout$"}:
            return f"write outside the instance tree {path}"
    return None


def policy_from_env(env: Optional[dict] = None) -> Policy:
    env = os.environ if env is None else env
    protected = tuple(p for p in (_norm(x) for x in env.get(ENV_PROTECT, "").split(os.pathsep) if x) if p)
    root = _norm(env[ENV_ROOT])
    if root is None:
        raise RuntimeError(f"{ENV_ROOT} is not set")
    return Policy(root=root, protected=protected)


def install(policy: Policy, log_path: Optional[Path] = None) -> None:
    """Add the audit hook (irreversible for the life of the process, which is the point)."""
    busy = threading.local()

    def hook(event: str, args: tuple) -> None:
        if getattr(busy, "on", False):
            return
        busy.on = True
        try:
            reason = decide(event, args, policy)
            if reason is None:
                return
            if log_path is not None:
                with open(log_path, "a", encoding="utf-8") as fh:
                    where = [f"{f.filename}:{f.lineno} {f.name}" for f in traceback.extract_stack()[-8:-2]]
                    fh.write(json.dumps({"event": event, "reason": reason, "where": where}) + "\n")
            raise PermissionError(f"synthetic demo guard refused: {reason}")
        finally:
            busy.on = False

    sys.addaudithook(hook)


def install_from_env() -> Policy:
    policy = policy_from_env()
    log = os.environ.get(ENV_LOG)
    install(policy, Path(log) if log else None)
    return policy


def read_log(log_path: Path) -> Iterable[dict]:
    if not log_path.exists():
        return []
    return [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
