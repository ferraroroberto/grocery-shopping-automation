"""Per-product purchase options a store's product page asks for (issue #176).

Some store product pages carry a choice that is not a different product — the
same product id, the same URL, one option picked on the page before "Añadir".
The first case is Carrefour's fresh-fish **cut** ("Selecciona el tipo de
corte": Entero · Entero limpio · Rodajas · Filetes · …). The option travels
with the add request and comes back on the cart line, so a handler can select
it and then verify it.

The preferences live in ``config/product_options.json``, keyed by store and
then by the store's **product id** — the option belongs to that exact product,
so it follows the product through list edits and store switches, and a
different product (even for the same item) never inherits it::

    {
      "carrefour": {
        "628108203": {"cut": "Entero limpio", "note": "dorada: whole, cleaned, not cut"}
      }
    }

``cut`` is the option's visible label on the product page, matched exactly.
``note`` is for people only.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OPTIONS_PATH = _REPO_ROOT / "config" / "product_options.json"


def load_product_options(path: Path = DEFAULT_OPTIONS_PATH) -> dict[str, dict[str, dict]]:
    """Return ``{store: {product_id: options}}``, or ``{}`` when the file is absent.

    Raises:
        ValueError: the file exists but is not valid JSON of that shape. A
            broken preferences file must stop the run rather than silently
            buying every product with its default option.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except ValueError as err:
        raise ValueError(f"{path} is not valid JSON: {err}") from err
    if not isinstance(data, dict) or not all(
        isinstance(products, dict) and all(isinstance(opts, dict) for opts in products.values())
        for products in data.values()
    ):
        raise ValueError(f"{path} must map store → product id → options object")
    return {str(store).lower(): products for store, products in data.items()}


def preferred_cut(
    store: str, product_id: str, *, path: Path = DEFAULT_OPTIONS_PATH
) -> Optional[str]:
    """The cut label to select for ``product_id`` at ``store``, or ``None``."""
    if not product_id:
        return None
    options = load_product_options(path).get(store.lower(), {}).get(str(product_id), {})
    cut = str(options.get("cut") or "").strip()
    return cut or None
