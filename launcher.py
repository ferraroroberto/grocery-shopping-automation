"""Thin launcher — the entrypoint ``tray.bat`` invokes.

Usage:
    python launcher.py                 # same as `tray` — day-to-day default
    python launcher.py tray

Standalone ``webapp.bat`` remains the "server only, no tray" alternative.
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="launcher.py")
    parser.add_argument("command", nargs="?", default="tray", choices=["tray"])
    args = parser.parse_args(argv)

    from app.tray.tray import run_tray  # noqa: E402 — deferred, optional deps

    return run_tray()


if __name__ == "__main__":
    sys.exit(main())
