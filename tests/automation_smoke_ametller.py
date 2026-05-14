r"""Smoke test: add a single real product to the Ametller Origen cart.

Drives `ametller.add_to_cart` against one real product URL and asserts it
returns without raising. This is a LIVE test:

* Requires a bootstrapped Chrome profile (`python -m automation.bootstrap_session`).
* Requires `automation.ametller_postal_code` set in `src/config.json` (the
  delivery postal-code modal needs it the first time something is added).
* Opens a real headed Chrome window and really adds the item to the cart.
* Not a CI test — run it manually and watch the window.

Run from the repo root:
    & .\.venv\Scripts\python.exe tests\automation_smoke_ametller.py
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation import ametller  # noqa: E402
from automation.browser import launch_context  # noqa: E402
from automation.models import CartItem  # noqa: E402

# A real, stable Ametller Origen product from the grocery list.
ITEM = CartItem(
    super_name="ametller",
    comida="pollo (smoke test)",
    comprar=1,
    buscador="https://www.ametllerorigen.com/es/pechuga-de-pollo-fileteado-ametller-origen/p",
)


def run() -> int:
    print(f"[..] adding {ITEM.comida} ×{ITEM.comprar} to the Ametller cart")
    playwright, context, page = launch_context(headless=False)
    try:
        ametller.add_to_cart(page, ITEM)
        print("[OK] add_to_cart returned without raising")
    finally:
        context.close()
        playwright.stop()
    print("[PASS] automation_smoke_ametller")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
