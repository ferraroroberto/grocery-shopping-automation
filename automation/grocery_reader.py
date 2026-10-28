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

from src.data import (  # noqa: E402
    COLUMNS,
    FALLBACK_SUPER_COLUMN,
    FALLBACK_URL_COLUMN,
    UNITS_PER_PACK_COLUMN,
    clean_url,
    fallback_pair,
    load_inventory_data,
    packs_for,
    store_url_columns,
    units_per_pack_of,
)

from automation.models import CartItem, Fallback  # noqa: E402

logger = logging.getLogger(__name__)


def _fallback(row, super_name: str, buscador: str) -> Optional[Fallback]:
    """The row's fallback from its optional cells, or ``None`` (blank, invalid, or the primary itself)."""
    pair = fallback_pair(
        row.get(FALLBACK_SUPER_COLUMN), row.get(FALLBACK_URL_COLUMN),
        super_name, buscador, label=row[COLUMNS["comida"]],
    )
    return Fallback(*pair) if pair else None


def read_cart_items(store: Optional[str] = None) -> list[CartItem]:
    """Load the inventory and return the items that still need to be bought.

    Args:
        store: When given, only rows whose ``super`` matches (case-insensitive)
            are returned. When ``None``, every store's pending items come back.

    Returns:
        :class:`CartItem` objects for rows short of their target, in spreadsheet
        order. ``comprar`` is in store packs: the counted shortfall converted by
        the row's ``units_per_pack`` (unset means 1, so units pass through).
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

    url_cols = store_url_columns(df)
    items: list[CartItem] = []
    for _, row in pending.iterrows():
        super_name = str(row[col_super]).strip()
        buscador = clean_url(row[col_buscador])
        comida = str(row[col_comida]).strip()
        units = int(row[col_comprar])
        per_pack = units_per_pack_of(row.get(UNITS_PER_PACK_COLUMN))
        packs = packs_for(units, per_pack)
        if per_pack is not None:
            logger.info("📦 %s: %d unit(s) needed at %g per pack → %d pack(s)", comida, units, per_pack, packs)
        items.append(
            CartItem(
                super_name=super_name,
                comida=comida,
                comprar=packs,
                buscador=buscador,
                alt_stores=tuple(
                    s for s, col in url_cols.items()
                    if s != super_name.lower() and clean_url(row[col])
                ),
                fallback=_fallback(row, super_name, buscador),
            )
        )

    logger.info(
        "✅ Read %d cart item(s)%s",
        len(items),
        f" for store '{store}'" if store else "",
    )
    return items
