"""Invented data for the design-review demo instance (#251).

Everything here is made up: product names, brands, prices, stores' product ids
and the ``demo.invalid`` URLs (a TLD that never resolves). Nothing is derived
from the household's real list, purchases or accounts.

Run from the demo instance's *code copy* (``python -m scripts.synthetic_demo_data
<root>``), where ``src/config.json`` already points at ``<root>``: it writes the
workbook and two benchmark runs, scores them with the app's own
``benchmark.score``, and freezes a Stores baseline, so every tab, mode and
dialog has content and a few corners are empty.
"""
from __future__ import annotations

import json
import logging
import os
import sys
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from scripts import synthetic_demo_guard as guard

# (comida, lugar, categoría, store, cantidad, tenemos, unidades, has product link)
ROWS: List[Tuple[str, str, str, str, int, int, Optional[int], bool]] = [
    ("meadow butter", "fridge", "dairy", "mercadona", 2, 1, 250, True),
    ("alpine cheddar slices", "fridge", "dairy", "ametller", 3, 3, 150, True),
    ("orchard apple juice", "fridge", "drinks", "mercadona", 4, 1, 1000, True),
    ("lagoon yogurt tubs", "fridge", "dairy", "carrefour", 6, 2, 125, True),
    ("hummus tub", "fridge", "dairy", "mercadona", 2, 2, 200, False),
    ("fig and walnut spread", "fridge", "spreads", "ametller", 1, 0, 220, True),
    ("harvest multigrain sourdough seeded family loaf with extra long label", "fridge", "bakery", "ametller", 1, 0, None, False),
    ("sunrise granola", "pantry", "dry goods", "mercadona", 2, 1, 500, True),
    ("river rice", "pantry", "dry goods", "mercadona", 3, 3, 1000, True),
    ("harbor tuna tins", "pantry", "tins", "carrefour", 6, 2, 80, True),
    ("summit pasta", "pantry", "dry goods", "mercadona", 4, 0, 500, True),
    ("cedar lentils", "pantry", "dry goods", "mercadona", 2, 2, 500, False),
    ("tomato passata jar", "pantry", "tins", "carrefour", 3, 1, 700, True),
    ("ember olive oil", "pantry", "oils", "ametller", 2, 1, 750, True),
    ("chickpea jar", "pantry", "tins", "mercadona", 4, 4, 400, True),
    ("smoky paprika", "pantry", "spices", "mercadona", 1, 0, None, False),
    ("ginger snap biscuits", "pantry", "snacks", "mercadona", 2, 0, 250, True),
    ("copper pot salt", "shelf", "spices", "mercadona", 1, 1, None, False),
    ("blueberry jam", "shelf", "spreads", "mercadona", 1, 1, 340, True),
    ("dark cocoa bars", "shelf", "snacks", "carrefour", 4, 4, 100, True),
    ("sea salt crackers", "shelf", "snacks", "mercadona", 3, 0, 200, True),
    ("emergency candles", "shelf", "household", "mercadona", 0, 3, None, False),
    ("garden peas bag", "freezer", "frozen", "mercadona", 2, 1, 1000, True),
    ("harvest fish fingers", "freezer", "frozen", "carrefour", 2, 0, 450, True),
    ("wild berry mix", "freezer", "frozen", "ametller", 2, 2, 500, True),
    ("smoked salmon portions", "freezer", "frozen", "ametller", 3, 1, 125, True),
    ("spinach cubes", "freezer", "frozen", "mercadona", 1, 1, None, False),
    ("kitchen roll multipack", "garage", "household", "mercadona", 3, 2, None, True),
    ("bin liners roll", "garage", "household", "carrefour", 2, 0, None, True),
    ("sparkling water crate", "garage", "drinks", "carrefour", 2, 2, 1500, True),
    ("citrus dish soap", "bathroom cabinet", "cleaning", "mercadona", 2, 1, 750, True),
    ("lavender laundry gel", "bathroom cabinet", "cleaning", "mercadona", 1, 0, 1500, True),
    ("pine floor cleaner", "bathroom cabinet", "cleaning", "carrefour", 1, 1, 1000, True),
    ("bamboo toothbrushes", "bathroom cabinet", "personal", "ametller", 4, 4, None, False),
]

STORES = ("mercadona", "ametller", "carrefour")
RUN_DATES = ("2026-08-01", "2026-09-01")      # two runs, so the Stores view has a price history
BASKET_ROWS = 20                              # the first rows with a product link are priced
DELIVERY = {
    "mercadona": {"delivers": "yes", "fee_tiers": [{"min_order": 0.0, "fee": 7.9}], "min_order": 40.0},
    "ametller": {"delivers": "yes", "fee_tiers": [{"min_order": 0.0, "fee": 5.95}, {"min_order": 60.0, "fee": 0.0}],
                 "min_order": None},
    "carrefour": {"delivers": "yes", "fee_tiers": [{"min_order": 0.0, "fee": 3.99}, {"min_order": 140.0, "fee": 0.0}],
                  "min_order": 50.0},
}
BASE_URL = "https://demo.invalid"


def slug(text: str) -> str:
    return "-".join("".join(c if c.isalnum() else " " for c in text.lower()).split())[:40]


def product_url(store: str, idx: int, comida: str) -> str:
    """A link that passes ``benchmark/stores.json``'s product-URL patterns for the store."""
    if store == "mercadona":
        return f"{BASE_URL}/mercadona/product/{90000 + idx}/{slug(comida)}"
    if store == "carrefour":
        return f"{BASE_URL}/carrefour/{slug(comida)}/R-DEMO{idx:03d}/p"
    return f"{BASE_URL}/ametller/{slug(comida)}/p"


def _wobble(key: str, store: str, low: float, spread: int) -> float:
    """A deterministic price factor in ``[low, low + spread/100)`` — no randomness, so runs are repeatable."""
    return low + (zlib.crc32(f"{key}:{store}".encode()) % spread) / 100


def _pack(comida: str, unidades: Optional[int]) -> Tuple[float, str]:
    """Pack size in the basket's unit (kg / l / ud) from the row's grams-or-ml count."""
    if unidades is None:
        return 1.0, "ud"
    unit = "l" if any(w in comida for w in ("juice", "water", "soap", "gel", "cleaner", "oil")) else "kg"
    return unidades / 1000, unit


def workbook_frame():
    import pandas as pd

    records = []
    for idx, (comida, lugar, cat, store, cantidad, tenemos, unidades, linked) in enumerate(ROWS):
        rec: Dict[str, object] = {
            "super": store, "buscador": product_url(store, idx, comida) if linked else comida.upper(),
            "lugar": lugar, "categoría": cat, "comida": comida, "unidades": unidades,
            "cantidad": cantidad, "tenemos": tenemos, "comprar": max(cantidad - tenemos, 0),
        }
        # Alternative store links on every third linked row; the rest stay empty (the Stores view's empty cells).
        for other in STORES:
            rec[f"url_{other}"] = product_url(other, idx, comida) if linked and idx % 3 == 0 else ""
        records.append(rec)
    return pd.DataFrame(records)


def basket_items() -> List[Tuple[int, Tuple]]:
    return [(i, r) for i, r in enumerate(ROWS) if r[7]][:BASKET_ROWS]


def write_run(runs: Path, date: str, price_factor: float) -> Path:
    run = runs / date
    (run / "stores").mkdir(parents=True, exist_ok=True)
    items, store_docs = [], {s: {} for s in STORES}
    for idx, (comida, _lugar, cat, store, cantidad, _have, unidades, _linked) in basket_items():
        key = slug(comida)
        size, unit = _pack(comida, unidades)
        base = round((2 + zlib.crc32(key.encode()) % 700 / 100) * max(size, 0.3) * price_factor, 2)
        current = {"url": product_url(store, idx, comida), "name": f"Demo {comida}", "pack_size": size,
                   "unit": unit, "pack_price": base, "unit_price": round(base / size, 2)}
        items.append({"key": key, "comida": comida, "store": store, "category": cat, "target": cantidad,
                      "buscador": current["url"], "monthly_packs": float(max(cantidad, 1)),
                      "qty_source": "target", "tier": "B", "current": current})
        for other in STORES:
            if other == store:
                store_docs[other][key] = {"status": "exact", "confidence": "high", "name": current["name"],
                                          "url": current["url"], "pack_size": size, "unit": unit,
                                          "pack_price": base, "evidence": "invented demo record"}
            elif zlib.crc32(f"{key}:{other}".encode()) % 7 == 0:
                store_docs[other][key] = {"status": "not_found", "confidence": "high", "evidence": "",
                                          "notes": "invented demo record"}
            else:
                price = round(base * _wobble(key, other, 0.8, 40), 2)
                rec = {"status": "equivalent", "confidence": "medium" if idx % 5 == 0 else "high",
                       "name": f"Demo {comida} ({other})", "url": product_url(other, idx, comida),
                       "pack_size": size, "unit": unit, "pack_price": price, "evidence": "invented demo record"}
                if idx % 4 == 0:  # a bigger pack, cheaper per unit
                    rec["alternatives"] = [{**rec, "name": rec["name"] + " family pack", "pack_size": size * 2,
                                            "pack_price": round(price * 1.7, 2)}]
                store_docs[other][key] = rec
    orders = {s: 4.0 for s in STORES}
    (run / "basket.json").write_text(json.dumps(
        {"run_date": date, "frequency": {"orders_per_month": orders}, "items": items}, indent=2), encoding="utf-8")
    for store, recs in store_docs.items():
        (run / "stores" / f"{store}.json").write_text(json.dumps(
            {"store": store, "delivery": DELIVERY[store], "items": recs, "updated_at": f"{date}T10:00:00"},
            indent=2), encoding="utf-8")
    return run


def seed(root: Path) -> None:
    """Write the workbook and benchmark state under ``root`` (the config already points there)."""
    from benchmark import score
    from src import data, store_links

    workbook_frame().to_excel(data.resolve_xlsx_path(), index=False, engine="openpyxl")
    runs = root / "benchmark_runs"
    for date, factor in zip(RUN_DATES, (1.04, 1.0)):
        run = write_run(runs, date, factor)
        if score.main([str(run), "--promote"]) != 0:
            raise RuntimeError(f"scoring the demo run {date} failed")
    store_links.set_baseline(data.load_inventory_data(), runs / RUN_DATES[-1])


def main(argv: Optional[List[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1:
        print("usage: python -m scripts.synthetic_demo_data <root>", file=sys.stderr)
        return 2
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    if os.environ.get(guard.ENV_ROOT):  # under the launcher: the same guard as the server
        guard.install_from_env()
    seed(Path(args[0]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
