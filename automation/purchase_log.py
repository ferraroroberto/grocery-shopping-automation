"""Persist what was actually ordered after a live cart-automation run.

Writes one JSON log per store that had at least one item added — the "what we
bought" source of truth a later step diffs against a parsed order-confirmation
email (issue #70; email reading/matching is out of scope here). Each item
carries its `buscador` product URL alongside the name/quantity, so a later
step can resolve straight back to the actual product (and, for Ametller, the
numeric `productId` embedded in that URL) instead of matching on name alone.

A second run on the same day merges into that day's log rather than replacing
it. Beside the purchase logs, :func:`write_run_records` keeps one record per
store *per run* under ``runs/`` (issue #247): every intended item with its
outcome, the verified cart snapshot, suggestions, and the run's console output.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import date as date_cls
from datetime import datetime
from pathlib import Path
from typing import Optional

from automation.models import CartItem
from automation.report import ADDED_UNVERIFIED, ADDED_VERIFIED, WOULD_ADD, RunReport

logger = logging.getLogger(__name__)

RUNS_SUBDIR = "runs"


def _item_key(entry: dict) -> str:
    return str(entry.get("buscador") or entry.get("comida") or "")


def write_purchase_logs(
    added: list[CartItem], logs_dir: Path, *, today: Optional[date_cls] = None
) -> list[Path]:
    """Write one JSON purchase-log entry per store with >=1 added item.

    Groups `added` by `CartItem.super_name`. When that day's log already exists
    (an earlier run today), the new items merge into it — a re-added item takes
    the new quantity, earlier items are kept. Lines for one product within
    ``added`` are summed. Returns the paths written; an
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
        path = logs_dir / f"{day}_{store}.json"
        merged: dict[str, dict] = {}
        if path.exists():
            try:
                for entry in json.loads(path.read_text(encoding="utf-8")).get("items", []):
                    merged[_item_key(entry)] = entry
            except (OSError, ValueError) as err:
                logger.warning("⚠️ [%s] earlier purchase log %s unreadable, replacing it: %s", store, path, err)
        fresh: dict[str, dict] = {}
        for item in items:
            entry = {"comida": item.comida, "comprar": item.comprar, "buscador": item.buscador}
            key = _item_key(entry)
            if key in fresh:
                # One product bought twice this run (a primary and another item's fallback).
                fresh[key]["comprar"] += item.comprar
            else:
                fresh[key] = entry
        merged.update(fresh)
        payload = {"date": day, "store": store, "items": list(merged.values())}
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.info("📝 [%s] purchase log written to %s (%d item(s))", store, path, len(merged))
        written.append(path)

    return written


def write_run_records(
    report: RunReport, logs_dir: Path, console_text: str, *, now: Optional[datetime] = None
) -> list[Path]:
    """Write this run's per-store records and console output under ``runs/``.

    Files are stamped to the second (``2026-10-06T191701_carrefour.json`` plus
    one ``2026-10-06T191701.log``), so two runs on one day never overwrite each
    other. Returns the paths written.
    """
    stamp = (now or datetime.now()).strftime("%Y-%m-%dT%H%M%S")
    runs_dir = logs_dir / RUNS_SUBDIR
    runs_dir.mkdir(parents=True, exist_ok=True)

    by_store: dict[str, list] = defaultdict(list)
    for outcome in report.outcomes:
        by_store[outcome.item.super_name.lower()].append(outcome)

    written: list[Path] = []
    for store, outcomes in by_store.items():
        path = runs_dir / f"{stamp}_{store}.json"
        payload = {
            "run": stamp,
            "store": store,
            "cart_mode": report.mode,
            "cart_before": report.cart_before.get(store),
            "cart_after": report.cart_after.get(store),
            "cart_lines": report.cart_lines.get(store),
            "items": [o.to_dict() for o in outcomes],
        }
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    log_path = runs_dir / f"{stamp}.log"
    log_path.write_text(console_text, encoding="utf-8")
    written.append(log_path)
    logger.info("📝 run record written to %s (%d store(s))", runs_dir, len(by_store))
    return written


def read_last_fill(logs_dir: Path) -> Optional[dict]:
    """The latest fill day's outcome per store, from the run records under ``runs/``.

    A fill day is every record stamped with the newest record's date; a store
    filled twice that day counts its later run. Per store: ``in_cart`` (verified),
    ``unconfirmed`` (added, the cart unreadable), ``missing`` (not in the cart)
    and that run's ``cart_mode``. ``run`` is the newest stamp, so a reader can
    tell one fill from the next. ``None`` when no live run was ever recorded.
    """
    latest: dict[str, dict] = {}
    for path in (logs_dir / RUNS_SUBDIR).glob("*_*.json"):
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
            stamp, store = str(record["run"]), str(record["store"])
        except (OSError, ValueError, KeyError, TypeError) as err:
            logger.warning("⚠️ run record %s unreadable, skipped: %s", path.name, err)
            continue
        if store not in latest or stamp > latest[store]["run"]:
            latest[store] = record
    if not latest:
        return None
    newest = max(record["run"] for record in latest.values())
    day = newest[:10]
    stores = {}
    for store, record in latest.items():
        if record["run"][:10] != day:
            continue
        statuses = [str(item.get("status")) for item in record.get("items") or []]
        stores[store] = {
            "in_cart": statuses.count(ADDED_VERIFIED),
            "unconfirmed": statuses.count(ADDED_UNVERIFIED),
            "missing": sum(s not in (ADDED_VERIFIED, ADDED_UNVERIFIED, WOULD_ADD) for s in statuses),
            "cart_mode": record.get("cart_mode", "keep"),
        }
    return {"run": newest, "date": day, "stores": stores}
