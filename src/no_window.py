"""Windows console-suppression flag for subprocess spawns.

Shared by every module that shells out to an external executable (git,
ffmpeg, tailscale, ...). Parents with no console of their own — the
tray-owned uvicorn, a scheduled task — otherwise get a new console window
flashed on screen for every spawn. Import this instead of re-inlining the
``sys.platform == "win32"`` ternary at each call site (global CLAUDE.md,
"Subprocess spawns must suppress the console window (Windows)").
"""

from __future__ import annotations

import subprocess
import sys

NO_WINDOW: int = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0

# For a long-lived child that later needs CTRL_BREAK_EVENT or graceful
# termination — combine with a process group. DETACHED_PROCESS and
# CREATE_NO_WINDOW are mutually exclusive; never combine those two.
NEW_PROCESS_GROUP_NO_WINDOW: int = (
    subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    if sys.platform == "win32"
    else 0
)
