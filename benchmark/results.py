"""Validated, incremental writer for per-store benchmark results (issue #145).

Store-research agents never hand-edit JSON: every finding goes through this
CLI, which validates it against ``basket.json`` (known item key, same unit as
the baseline, price/size present for a match) and upserts it into
``<run>/stores/<store>.json``. Writing one item at a time means an agent that
runs out of context loses nothing — the next one resumes from ``status``.

    python -m benchmark.results status   --run benchmark_runs/2026-09-27 --store dia
    python -m benchmark.results item     --run … --store dia --key pavo-90 --json '{…}'
    python -m benchmark.results delivery --run … --store dia --json '{…}'
    python -m benchmark.results access   --run … --store dia --json '{"method": "chrome", "notes": "…"}'
    python -m benchmark.results alt      --run … --store dia --key pavo-90 --json '{…}'   # add another qualifying product

Item record (``--json``)::

    status        exact | equivalent | not_found | unknown
    name, brand, url, ean
    pack_size     float, in the BASKET item's unit (convert g→kg, ml→l, eggs→ud)
    unit          must equal the basket item's current unit
    pack_price    float EUR (the price you'd pay today)
    regular_price float|null — non-promo price when ``promo`` is true
    promo         bool
    evidence      verbatim ingredient list / brand+variant line from the page
    confidence    high | medium | low
    quality_vs_current  same | better   (for exact/equivalent)
    upgrade       optional {name,url,pack_size,unit,pack_price,evidence} —
                  tier B only: cheapest option meeting the spec's UPGRADE bar
    rejected      optional [{name,url,reason}] — near misses and why
    alternatives  optional [record, …] — other QUALIFYING products at this store
                  (e.g. a bigger pack). The scorer compares every candidate on
                  €/unit and uses the cheapest; `alt` appends one.
    notes         free text

Delivery record: ``{"delivers": "yes|no|unknown", "fee_tiers": [{"min_order":
0, "fee": 7.5}, {"min_order": 80, "fee": 0}], "min_order": 50, "source_url":
"…", "notes": "…"}`` — ``fee_tiers`` sorted by ``min_order``; the fee of the
highest tier whose ``min_order`` ≤ the order value applies.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

STATUSES = {"exact", "equivalent", "not_found", "unknown"}
MATCHED = {"exact", "equivalent"}
CONFIDENCE = {"high", "medium", "low"}
DELIVERS = {"yes", "no", "unknown"}


class ResultError(ValueError):
    """A record failed validation — the message says what to fix."""


def load_basket(run_dir: Path) -> dict:
    return json.loads((run_dir / "basket.json").read_text(encoding="utf-8"))


def basket_units(basket: dict) -> dict[str, str]:
    """Item key → the unit its baseline pack size is expressed in."""
    return {
        it["key"]: str((it.get("current") or {}).get("unit") or "")
        for it in basket["items"]
    }


def validate_item(key: str, record: dict, units: dict[str, str]) -> dict:
    """Validate one item record; returns it normalised or raises ResultError."""
    if key not in units:
        raise ResultError(f"unknown item key '{key}' — use the keys in basket.json")
    status = record.get("status")
    if status not in STATUSES:
        raise ResultError(f"status must be one of {sorted(STATUSES)}")
    if record.get("confidence") and record["confidence"] not in CONFIDENCE:
        raise ResultError(f"confidence must be one of {sorted(CONFIDENCE)}")
    if status in MATCHED:
        for field in ("name", "url", "evidence"):
            if not str(record.get(field) or "").strip():
                raise ResultError(f"a {status} match needs a non-empty '{field}'")
        for field in ("pack_size", "pack_price"):
            try:
                value = float(record.get(field))
            except (TypeError, ValueError):
                raise ResultError(f"a {status} match needs a numeric '{field}'") from None
            if value <= 0:
                raise ResultError(f"'{field}' must be > 0")
            record[field] = value
        want = units[key]
        if want and str(record.get("unit") or "").lower() != want:
            raise ResultError(
                f"unit must be '{want}' (the basket's unit for '{key}') — convert "
                f"pack_size (g→kg, ml→l, count→ud) before writing"
            )
        record["unit"] = want
    alts = record.get("alternatives") or []
    if not isinstance(alts, list):
        raise ResultError("alternatives must be a list of records")
    for alt in alts:
        if alt.get("status") not in MATCHED:
            raise ResultError("each alternative must be an exact/equivalent match")
        validate_item(key, alt, units)
    return record


def add_alternative(existing: Optional[dict], alt: dict, key: str, units: dict[str, str]) -> dict:
    """Merge another qualifying product into an item record.

    No match yet → the alternative becomes the record (keeping the old
    notes/rejected). Otherwise it is appended to ``alternatives`` (replacing
    one with the same URL).
    """
    alt = validate_item(key, dict(alt), units)
    if alt["status"] not in MATCHED:
        raise ResultError("an alternative must be an exact/equivalent match")
    if not existing or existing.get("status") not in MATCHED:
        merged = dict(alt)
        if existing:
            merged["rejected"] = list(existing.get("rejected") or []) + list(alt.get("rejected") or [])
            merged["notes"] = " | ".join(n for n in (existing.get("notes"), alt.get("notes")) if n)
        return merged
    merged = dict(existing)
    others = [a for a in (merged.get("alternatives") or []) if a.get("url") != alt.get("url")]
    merged["alternatives"] = others + [alt]
    return merged


def _store_path(run_dir: Path, store: str) -> Path:
    return run_dir / "stores" / f"{store}.json"


def load_store(run_dir: Path, store: str) -> dict:
    path = _store_path(run_dir, store)
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"store": store, "delivery": {}, "access": {}, "items": {}}


@contextlib.contextmanager
def store_lock(run_dir: Path, store: str, timeout_s: float = 60.0):
    """Exclusive lock around a store file's read-modify-write.

    Two agents may update the same store file (e.g. a deli follow-up and a
    pack-size follow-up); without this, interleaved load/save loses a write.
    """
    lock = _store_path(run_dir, store).with_suffix(".lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_s
    while True:
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            break
        except FileExistsError:
            if time.monotonic() > deadline:
                raise ResultError(f"{lock.name} held for > {timeout_s:g}s — another writer is stuck; "
                                  f"check for a live process before removing it") from None
            time.sleep(0.2)
    try:
        yield
    finally:
        os.close(fd)
        lock.unlink(missing_ok=True)


def save_store(run_dir: Path, store: str, doc: dict) -> Path:
    path = _store_path(run_dir, store)
    path.parent.mkdir(parents=True, exist_ok=True)
    doc["updated_at"] = datetime.now().isoformat(timespec="seconds")
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return path


def validate_delivery(record: dict) -> dict:
    if record.get("delivers") not in DELIVERS:
        raise ResultError(f"delivers must be one of {sorted(DELIVERS)}")
    tiers = record.get("fee_tiers") or []
    for t in tiers:
        if not isinstance(t, dict) or "min_order" not in t or "fee" not in t:
            raise ResultError("each fee tier needs 'min_order' and 'fee'")
        t["min_order"], t["fee"] = float(t["min_order"]), float(t["fee"])
    record["fee_tiers"] = sorted(tiers, key=lambda t: t["min_order"])
    if record.get("min_order") is not None:
        record["min_order"] = float(record["min_order"])
    return record


def status_summary(run_dir: Path, store: str) -> dict:
    basket = load_basket(run_dir)
    doc = load_store(run_dir, store)
    done = doc.get("items", {})
    keys = [it["key"] for it in basket["items"]]
    by_status: dict[str, int] = {}
    for rec in done.values():
        by_status[rec.get("status", "?")] = by_status.get(rec.get("status", "?"), 0) + 1
    return {
        "store": store,
        "done": len([k for k in keys if k in done]),
        "total": len(keys),
        "by_status": by_status,
        "missing": [k for k in keys if k not in done],
        "delivery_recorded": bool(doc.get("delivery")),
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("status", "item", "alt", "delivery", "access"))
    parser.add_argument("--run", required=True, help="Run dir, e.g. benchmark_runs/2026-09-27")
    parser.add_argument("--store", required=True)
    parser.add_argument("--key", help="Item key (for 'item').")
    parser.add_argument("--json", help="Record as a JSON string.")
    parser.add_argument("--json-file", help="Record as a JSON file (avoids shell quoting).")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    run_dir = Path(args.run)

    if args.action == "status":
        print(json.dumps(status_summary(run_dir, args.store), ensure_ascii=False, indent=2))
        return 0

    raw = Path(args.json_file).read_text(encoding="utf-8") if args.json_file else args.json
    if not raw:
        print("❌ --json or --json-file is required", file=sys.stderr)
        return 2
    try:
        record = json.loads(raw)
        with store_lock(run_dir, args.store):
            doc = load_store(run_dir, args.store)
            if args.action in ("item", "alt") and not args.key:
                raise ResultError(f"--key is required for '{args.action}'")
            if args.action == "item":
                doc["items"][args.key] = validate_item(args.key, record, basket_units(load_basket(run_dir)))
            elif args.action == "alt":
                doc["items"][args.key] = add_alternative(doc["items"].get(args.key), record, args.key,
                                                         basket_units(load_basket(run_dir)))
            elif args.action == "delivery":
                doc["delivery"] = validate_delivery(record)
            else:
                doc["access"] = record
            save_store(run_dir, args.store, doc)
    except (ResultError, json.JSONDecodeError) as err:
        print(f"❌ rejected: {err}", file=sys.stderr)
        return 1
    print(f"✅ saved {args.action} {args.key or ''} → {_store_path(run_dir, args.store)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
