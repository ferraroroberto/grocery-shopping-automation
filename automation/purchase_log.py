"""Persist what was actually ordered after a live cart-automation run.

Writes one JSON log per store that had at least one item added — the "what we
bought" source of truth a later step diffs against a parsed order-confirmation
email (issue #70; email reading/matching is out of scope here). Each item
carries its `buscador` product URL alongside the name/quantity, so a later
step can resolve straight back to the actual product (and, for Ametller, the
numeric `productId` embedded in that URL) instead of matching on name alone.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date as date_cls
from pathlib import Path
from typing import Optional

from automation.models import CartItem

logger = logging.getLogger(__name__)


def write_purchase_logs(
    added: list[CartItem], logs_dir: Path, *, today: Optional[date_cls] = None
) -> list[Path]:
    """Write one JSON purchase-log entry per store with >=1 added item.

    Groups `added` by `CartItem.super_name`. Returns the paths written; an
    empty `added` produces no files and returns `[]`.
    """
    if not added:
        return []

    by_store: dict[str, list[CartItem]] = defaultdict(list)
    for item in added:
        by_store[item.super_name].append(item)

    day = (today or date_cls.today()).isoformat()
    logs_dir.mkdir(parents=True, exist_ok=True)

    written: list[Path] = []
    for store, items in by_store.items():
        payload = {
            "date": day,
            "store": store,
            "items": [
                {
                    "comida": item.comida,
                    "comprar": item.comprar,
                    "buscador": item.buscador,
                }
                for item in items
            ],
        }
        path = logs_dir / f"{day}_{store}.json"
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("📝 [%s] purchase log written to %s", store, path)
        written.append(path)

    return written
