"""Generate Grocery's PWA, tray, and Stream Deck icon family.

The canonical master is ``project-scaffolding/brand/shopping-basket.svg``.
Generated assets are committed, so ``resvg-py`` is only needed when this
development script is rerun.

Usage:
    & .\\.venv\\Scripts\\python.exe scripts\\gen_icons.py
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCAFFOLDING_ROOT = Path(
    os.environ.get("PROJECT_SCAFFOLDING_ROOT", r"E:\automation\project-scaffolding")
)
sys.path.insert(0, str(SCAFFOLDING_ROOT / "scripts"))

from brand_gen import render_set  # noqa: E402

STATIC_DIR = PROJECT_ROOT / "app" / "static"

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("gen_icons")


def main() -> None:
    """Render every external Grocery icon from the canonical Lucide master."""
    render_set(
        master=SCAFFOLDING_ROOT / "brand" / "shopping-basket.svg",
        out_dir=STATIC_DIR,
        tray_out_dir=PROJECT_ROOT / "assets" / "tray",
        stream_deck_out_dir=PROJECT_ROOT / "assets" / "stream-deck",
        project_slug="grocery-shopping-automation",
    )
    log.info("✅ wrote canonical icon family to %s", STATIC_DIR)


if __name__ == "__main__":
    main()
