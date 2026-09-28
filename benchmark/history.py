"""Run history for the supermarket benchmark (issue #145).

Every finished run is recorded into two append-only-by-run files under
``benchmark_runs/_state/`` (gitignored — prices are household data):

* ``history.jsonl`` — one summary line per run: status-quo and recommended
  totals, the recommended/max-savings store sets, and per-store coverage,
  price delta and delivery terms. The report's "Over time" section and the
  next run's orchestrator read it.
* ``prices.csv`` — one row per (run, store, item) offer or miss: product,
  pack size, pack price, €/unit, status, confidence. Long format, so a price
  trend for any item/store is a filter away.

Recording is idempotent per run date: re-recording a run replaces its rows.

    python -m benchmark.history record benchmark_runs/2026-09-27
    python -m benchmark.history show
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
STATE_DIR = _REPO_ROOT / "benchmark_runs" / "_state"
HISTORY_PATH = STATE_DIR / "history.jsonl"
PRICES_PATH = STATE_DIR / "prices.csv"
PRICE_FIELDS = ["run_date", "store", "key", "comida", "tier", "baseline", "status", "confidence",
                "name", "url", "pack_size", "unit", "pack_price", "unit_price"]


def _plan(p: Optional[dict]) -> Optional[dict]:
    if not p:
        return None
    return {"stores": p["stores"], "total_optimised": p["total_optimised"], "goods": p["goods"]}


def summarise(scenarios: dict, basket: dict) -> dict:
    """The history line for one scored run."""
    sq = scenarios["status_quo"]
    rs = scenarios.get("resplit_current") or {}
    return {
        "run_date": scenarios["run_date"],
        "items": scenarios["items_scored"],
        "months_of_logs": basket.get("frequency", {}).get("months_spanned"),
        "orders_per_month": scenarios["orders_per_month"],
        "status_quo": {"goods": sq["goods"], "total": sq["total"], "total_optimised": sq["total_optimised"]},
        "resplit_current": {"total_optimised": rs.get("total_optimised")} if rs else None,
        "recommended": _plan(scenarios.get("recommended")),
        "max_savings": _plan(scenarios.get("max_savings")),
        "stores": {
            s["store"]: {
                "coverage_pct": s["coverage_pct"],
                "common_subset_delta_pct": s["common_subset"]["delta_pct"],
                "unverified": len(scenarios.get("unverified", {}).get(s["store"], [])),
                "fee_tiers": scenarios["deliveries"].get(s["store"], {}).get("fee_tiers"),
                "min_order": scenarios["deliveries"].get(s["store"], {}).get("min_order"),
            }
            for s in scenarios["single_store"]
        },
    }


def price_rows(run_date: str, basket: dict, stores: dict[str, dict]) -> list[dict]:
    """Long-format rows: today's product per item + every store record."""
    rows = []
    for it in basket["items"]:
        cur = it.get("current") or {}
        if cur.get("pack_price") is not None and cur.get("pack_size"):
            rows.append({"run_date": run_date, "store": it["store"], "key": it["key"], "comida": it["comida"],
                         "tier": it.get("tier"), "baseline": True, "status": "baseline", "confidence": "high",
                         "name": cur.get("name"), "url": cur.get("url"), "pack_size": cur["pack_size"],
                         "unit": cur.get("unit"), "pack_price": cur["pack_price"],
                         "unit_price": round(cur["pack_price"] / cur["pack_size"], 4)})
        for store, doc in stores.items():
            rec = (doc.get("items") or {}).get(it["key"])
            if not rec or store == it["store"]:
                continue
            size, price = rec.get("pack_size"), rec.get("pack_price")
            rows.append({"run_date": run_date, "store": store, "key": it["key"], "comida": it["comida"],
                         "tier": it.get("tier"), "baseline": False, "status": rec.get("status"),
                         "confidence": rec.get("confidence"), "name": rec.get("name"), "url": rec.get("url"),
                         "pack_size": size, "unit": rec.get("unit"), "pack_price": price,
                         "unit_price": round(price / size, 4) if size and price else None})
    return rows


def load_history(path: Path = HISTORY_PATH) -> list[dict]:
    """Every recorded run summary, oldest first."""
    if not path.exists():
        return []
    runs = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return sorted(runs, key=lambda r: r["run_date"])


def record(run_dir: Path, history_path: Path = HISTORY_PATH, prices_path: Path = PRICES_PATH) -> dict:
    """Record (or re-record) one scored run into both history files."""
    scenarios = json.loads((run_dir / "scenarios.json").read_text(encoding="utf-8"))
    basket = json.loads((run_dir / "basket.json").read_text(encoding="utf-8"))
    stores = {p.stem: json.loads(p.read_text(encoding="utf-8"))
              for p in sorted((run_dir / "stores").glob("*.json"))}
    summary = summarise(scenarios, basket)
    run_date = summary["run_date"]

    history_path.parent.mkdir(parents=True, exist_ok=True)
    runs = [r for r in load_history(history_path) if r["run_date"] != run_date] + [summary]
    history_path.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n"
                                    for r in sorted(runs, key=lambda r: r["run_date"])), encoding="utf-8")

    kept: list[dict] = []
    if prices_path.exists():
        with prices_path.open(encoding="utf-8", newline="") as fh:
            kept = [row for row in csv.DictReader(fh) if row["run_date"] != run_date]
    with prices_path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=PRICE_FIELDS)
        writer.writeheader()
        writer.writerows(kept + price_rows(run_date, basket, stores))
    return summary


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=("record", "show"))
    parser.add_argument("run_dir", nargs="?", type=Path)
    args = parser.parse_args(argv)
    sys.stdout.reconfigure(encoding="utf-8")
    if args.action == "record":
        if not args.run_dir:
            parser.error("record needs a run_dir")
        s = record(args.run_dir)
        print(f"✅ recorded {s['run_date']} into {HISTORY_PATH.name} and {PRICES_PATH.name}")
        return 0
    for r in load_history():
        rec = r.get("recommended") or {}
        print(f"{r['run_date']}  today {r['status_quo']['total_optimised']:.2f}  "
              f"recommended {rec.get('total_optimised', 0):.2f} ({' + '.join(rec.get('stores', []))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
