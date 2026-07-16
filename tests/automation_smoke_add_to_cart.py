r"""Smoke test: add a single real product to a store's cart (issue #95).

Drives ``<store>.add_to_cart`` against one real product URL and asserts it
returns without raising. This is a LIVE test:

* Requires a bootstrapped Chrome profile (`python -m automation.bootstrap_session`).
* Opens a real headed Chrome window and really adds the item to the cart.
* Not a CI test — run it manually and watch the window.

Consolidates what used to be two near-identical scripts
(``automation_smoke_ametller.py`` and ``automation_smoke_single.py``), which
differed only in the store import, the ``CartItem`` values, and the print
labels — this one takes the store as a CLI arg instead.

Run from the repo root, picking the store with ``--store`` (default: mercadona):
    & .\.venv\Scripts\python.exe tests\automation_smoke_add_to_cart.py --store mercadona
    & .\.venv\Scripts\python.exe tests\automation_smoke_add_to_cart.py --store ametller
"""

import argparse
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from automation import ametller, mercadona  # noqa: E402
from automation.browser import launch_context  # noqa: E402
from automation.models import CartItem  # noqa: E402

# One real, stable product per store, taken from the grocery list.
ITEMS = {
    "mercadona": CartItem(
        super_name="mercadona",
        comida="copos avena (smoke test)",
        comprar=1,
        buscador="https://tienda.mercadona.es/product/86368/copos-avena-hacendado-paquete",
    ),
    "ametller": CartItem(
        super_name="ametller",
        comida="pollo (smoke test)",
        comprar=1,
        buscador="https://www.ametllerorigen.com/es/pechuga-de-pollo-fileteado-ametller-origen/p",
    ),
}
MODULES = {"mercadona": mercadona, "ametller": ametller}


def run(store: str) -> int:
    item = ITEMS[store]
    module = MODULES[store]
    print(f"[..] adding {item.comida} ×{item.comprar} to the {store} cart")
    playwright, context, page = launch_context(headless=False)
    try:
        module.add_to_cart(page, item)
        print("[OK] add_to_cart returned without raising")
    finally:
        context.close()
        playwright.stop()
    print(f"[PASS] automation_smoke_add_to_cart ({store})")
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Add one real product to a store cart.")
    parser.add_argument("--store", choices=sorted(ITEMS), default="mercadona")
    return parser.parse_args(argv)


if __name__ == "__main__":
    raise SystemExit(run(parse_args().store))
