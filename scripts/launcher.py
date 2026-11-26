"""Thin launcher — the entrypoint ``tray.bat`` invokes.

Usage:
    python scripts/launcher.py         # same as `tray` — day-to-day default
    python scripts/launcher.py tray

Standalone ``scripts/webapp.bat`` remains the "server only, no tray" alternative.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Run as a script, sys.path[0] is scripts/, so add the repo root that holds `app`.
REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="launcher.py")
    parser.add_argument("command", nargs="?", default="tray", choices=["tray"])
    parser.parse_args(argv)

    from app.tray.tray import run_tray  # noqa: E402 — deferred, optional deps

    return run_tray()


if __name__ == "__main__":
    sys.exit(main())
