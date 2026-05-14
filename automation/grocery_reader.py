"""Read the grocery inventory spreadsheet into :class:`CartItem` objects.

UI-free. The actual Excel I/O and config-driven column names live in
``src/data.py`` — this module reuses them and only filters and shapes the rows
into :class:`CartItem` instances.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Optional

# ``src/`` is a sibling package at the repo root; make it importable whether
# this module is run as ``python -m automation.grocery_reader`` or imported.
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src.data import COLUMNS, load_inventory_data  # noqa: E402

from automation.models import CartItem  # noqa: E402

logger = logging.getLogger(__name__)


def _clean_url(raw: object) -> str:
    """Return a usable product URL, or ``""`` for missing / non-URL values.

    Some inventory rows store an uppercase product description in the
    ``buscador`` column instead of a link (e.g. ``"HIELO CUBITOS"``). Only
    ``http(s)`` values are treated as usable URLs; everything else collapses
    to an empty string.
    """
    if raw is None:
        return ""
    text = str(raw).strip()
    if not text or text.lower() == "nan":
        return ""
    if not text.lower().startswith(("http://", "https://")):
        return ""
    return text


def read_cart_items(store: Optional[str] = None) -> list[CartItem]:
    """Load the inventory and return the items that still need to be bought.

    Args:
        store: When given, only rows whose ``super`` matches (case-insensitive)
            are returned. When ``None``, every store's pending items come back.

    Returns:
        :class:`CartItem` objects with ``comprar > 0``, in spreadsheet order.
        An empty list if the inventory file is missing or unreadable.
    """
    df = load_inventory_data()
    if df is None:
        logger.error("❌ Inventory data unavailable — returning no cart items")
        return []

    col_super = COLUMNS["super"]
    col_comida = COLUMNS["comida"]
    col_comprar = COLUMNS["comprar"]
    col_buscador = COLUMNS["buscador"]

    pending = df[df[col_comprar] > 0]
    if store is not None:
        pending = pending[pending[col_super].astype(str).str.lower() == store.lower()]

    items: list[CartItem] = []
    for _, row in pending.iterrows():
        items.append(
            CartItem(
                super_name=str(row[col_super]).strip(),
                comida=str(row[col_comida]).strip(),
                comprar=int(row[col_comprar]),
                buscador=_clean_url(row[col_buscador]),
            )
        )

    logger.info(
        "✅ Read %d cart item(s)%s",
        len(items),
        f" for store '{store}'" if store else "",
    )
    return items
