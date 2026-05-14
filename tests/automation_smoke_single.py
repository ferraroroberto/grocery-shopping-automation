r"""Smoke test: add a single real product to the Mercadona cart.

Drives `mercadona.add_to_cart` against one real product URL and asserts it
returns without raising. This is a LIVE test:

* Requires a bootstrapped Chrome profile (`python -m automation.bootstrap_session`).
* Opens a real headed Chrome window and really adds the item to the cart.
* Not a CI test — run it manually and watch the window.

Run from the repo root:
    & .\.venv\Scripts\python.exe tests\automation_smoke_single.py
"""

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation import mercadona  # noqa: E402
from automation.browser import launch_context  # noqa: E402
from automation.models import CartItem  # noqa: E402

# A real, stable Mercadona product from the grocery list.
ITEM = CartItem(
    super_name="mercadona",
    comida="copos avena (smoke test)",
    comprar=1,
    buscador="https://tienda.mercadona.es/product/86368/copos-avena-hacendado-paquete",
)


def run() -> int:
    print(f"[..] adding {ITEM.comida} ×{ITEM.comprar} to the Mercadona cart")
    playwright, context, page = launch_context(headless=False)
    try:
        mercadona.add_to_cart(page, ITEM)
        print("[OK] add_to_cart returned without raising")
    finally:
        context.close()
        playwright.stop()
    print("[PASS] automation_smoke_single")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
