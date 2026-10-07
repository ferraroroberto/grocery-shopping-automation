"""Build the benchmark baseline basket (issue #145).

Reads the inventory spreadsheet and the cart-automation purchase logs, derives
the household's **average monthly quantity** per item, fetches today's product
(name, brand, EAN, pack size, price, ingredients) at the store we currently buy
it from, suggests a quality tier, and writes
``benchmark_runs/<date>/basket.json`` — the input every store-research agent and
:mod:`benchmark.score` works from.

Monthly quantity: Σ ``comprar`` across all purchase logs ÷ months spanned. The
span is first-to-last order date **plus one median order gap** (the last order
covers the days after it too). Items with a target > 0 that never appear in the
logs fall back to their target quantity.

Current-product sources, both verified live 2026-09-27:

* **Mercadona** — the public ``/api/products/{id}/`` endpoint (no login).
  ``price_instructions`` gives the pack price / size / unit and
  ``nutrition_information.ingredients`` the ingredient list. Prices did not
  vary by warehouse, but the warehouse for the delivery postal code is still
  resolved (``x-customer-wh`` header of ``change-pc``) and recorded.
* **Ametller** — SCAPI Shopper Products (``c_ao_ingredientes``,
  ``unitQuantity``/``unitMeasure``, ``pricePerUnit``) with a guest token
  (:mod:`benchmark.ametller_guest`) — plain HTTP, no Chrome profile.

Per-item overrides reviewed by a human persist in
``benchmark_runs/_state/quality_specs.json`` and win on every later run:
``tier`` / ``spec`` (replace the auto-suggestion), ``current_url`` (price a
replacement when the inventory's ``buscador`` is stale or plain text),
``current_fields`` (correct a store-misreported pack size/unit) and
``exclude`` + ``reason`` (drop an item, e.g. out of season).
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import statistics
import sys
import unicodedata
from datetime import date
from pathlib import Path
from typing import Optional

import requests

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmark.ametller_guest import GuestSession  # noqa: E402
from benchmark.paths import runs_dir, state_dir  # noqa: E402
from src import data  # noqa: E402

logger = logging.getLogger("benchmark.build_basket")

_DAYS_PER_MONTH = 30.44
_HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/128.0 Safari/537.36"
    )
}

_MERCADONA_PRODUCT_API = "https://tienda.mercadona.es/api/products/{id}/"
_MERCADONA_CHANGE_PC = "https://tienda.mercadona.es/api/postal-codes/actions/change-pc/"

# Store own-labels: a product under one of these is matched on spec, not brand.
OWN_LABELS = {
    "hacendado", "bosque verde", "deliplus", "compy", "ametller origen", "ao",
    "essencials", "ametller",
}
# Deli / meat / fish / eggs — spec-locked (tier B) when not brand-locked.
_TIER_B_WORDS = (
    "pavo", "jamon", "pollo", "pechuga", "salmon", "huevo", "clara", "dorada",
    "rape", "pulpo", "gamba", "langostino", "burguer", "hamburguesa", "merluza",
    "pescad", "ternera", "nugget",
)
_TIER_D_CATEGORIES = {"drogueria"}

TIER_RULES = {
    "A": "Brand-locked — same brand + variant (EAN match when available).",
    "B": "Spec-locked deli/meat/fish/eggs — meat/fish % >= spec, no added "
         "junk, same cut/format, same eco/free-range class.",
    "C": "Key-spec match — the defining specs (e.g. 100% peanut, 99% cacao, "
         "natural not oil) must hold; brand free.",
    "D": "Commodity — cheapest product with the same function/spec.",
}


def normalize(text: object) -> str:
    """Lower-case, accent-stripped, whitespace-collapsed text."""
    s = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"\s+", " ", s).strip().lower()


def item_key(comida: str) -> str:
    """Stable slug key for an inventory item (``comida``)."""
    return re.sub(r"[^a-z0-9]+", "-", normalize(comida)).strip("-")


# ─────────────────────────────────────────────────────────────────────────────
# Monthly quantities from purchase logs
# ─────────────────────────────────────────────────────────────────────────────


def load_purchase_logs(logs_dir: Path) -> list[dict]:
    """Every ``<date>_<store>.json`` purchase log in ``logs_dir``."""
    logs: list[dict] = []
    for path in sorted(logs_dir.glob("*.json")):
        try:
            logs.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError) as err:
            logger.warning("⚠️ Skipping unreadable purchase log %s: %s", path.name, err)
    return logs


def months_spanned(dates: list[date]) -> float:
    """Months covered by a set of order dates (first→last + one median gap).

    A single order date counts as one median-less month-quarter of a week
    cycle, so it falls back to 7 days (one weekly order).
    """
    uniq = sorted(set(dates))
    if not uniq:
        return 0.0
    if len(uniq) == 1:
        return 7 / _DAYS_PER_MONTH
    gaps = [(b - a).days for a, b in zip(uniq, uniq[1:])]
    span = (uniq[-1] - uniq[0]).days + statistics.median(gaps)
    return span / _DAYS_PER_MONTH


def monthly_quantities(logs: list[dict]) -> tuple[dict[str, float], dict]:
    """Average monthly quantity per item key, plus order-frequency stats.

    Returns ``(qty_by_key, meta)`` where ``meta`` holds the months spanned,
    the log dates and per-store orders per month.
    """
    dates = [date.fromisoformat(log["date"]) for log in logs if log.get("date")]
    months = months_spanned(dates)
    totals: dict[str, float] = {}
    store_dates: dict[str, set[str]] = {}
    for log in logs:
        store_dates.setdefault(str(log.get("store")), set()).add(str(log.get("date")))
        for it in log.get("items", []) or []:
            key = item_key(it.get("comida", ""))
            if key:
                totals[key] = totals.get(key, 0.0) + float(it.get("comprar") or 0)
    qty = {k: round(v / months, 3) for k, v in totals.items()} if months else {}
    meta = {
        "months_spanned": round(months, 3),
        "log_dates": sorted({d.isoformat() for d in dates}),
        "orders_per_month": {
            s: round(len(ds) / months, 2) if months else 0.0
            for s, ds in store_dates.items()
        },
    }
    return qty, meta


# ─────────────────────────────────────────────────────────────────────────────
# Tier suggestion
# ─────────────────────────────────────────────────────────────────────────────


def suggest_tier(comida: str, category: str, brand: str) -> str:
    """Rule-based tier suggestion (A brand > D commodity > B deli/meat > C)."""
    b = normalize(brand)
    if b and b not in OWN_LABELS:
        return "A"
    if normalize(category) in _TIER_D_CATEGORIES:
        return "D"
    text = normalize(comida)
    if normalize(category) == "carne y pescado" or any(w in text for w in _TIER_B_WORDS):
        return "B"
    return "C"


def auto_spec(tier: str, current: Optional[dict], comida: str) -> str:
    """Draft must-match spec from the current product (reviewed by a human)."""
    if not current:
        return f"Equivalent to '{comida}' (no current product resolved — derive from name)."
    size = f"{current.get('pack_size')} {current.get('unit')}"
    if tier == "A":
        ean = current.get("ean") or "n/a"
        return f"Brand {current.get('brand')} — '{current.get('name')}' ({size}), EAN {ean}."
    ingredients = (current.get("ingredients") or "").strip()
    base = f"Equivalent to '{current.get('name')}' ({size})."
    return f"{base} Ingredients to match or beat: {ingredients}" if ingredients else base


# ─────────────────────────────────────────────────────────────────────────────
# Current-product fetchers
# ─────────────────────────────────────────────────────────────────────────────


def _float(value: object) -> Optional[float]:
    try:
        return float(value) if value not in (None, "") else None
    except (TypeError, ValueError):
        return None


def mercadona_warehouse(postal_code: str) -> Optional[str]:
    """Warehouse id Mercadona serves ``postal_code`` from, or None on failure."""
    try:
        resp = requests.put(
            _MERCADONA_CHANGE_PC, json={"new_postal_code": postal_code},
            headers=_HTTP_HEADERS, timeout=15,
        )
        wh = resp.headers.get("x-customer-wh")
        if not wh:
            logger.warning("⚠️ Mercadona change-pc returned %d with no warehouse header", resp.status_code)
        return wh
    except requests.RequestException as err:
        logger.warning("⚠️ Mercadona warehouse lookup failed: %s", err)
        return None


def parse_mercadona_product(raw: dict) -> dict:
    """Project a Mercadona product-API payload onto the basket product shape.

    Some products (e.g. frozen fish sold by drained weight) carry no
    ``unit_size``; the pack size is then recovered as pack price ÷ reference
    price (€/kg), which is exactly how the store derives the reference.
    """
    pi = raw.get("price_instructions") or {}
    nutrition = raw.get("nutrition_information") or {}
    pack_price = _float(pi.get("unit_price"))
    pack_size = _float(pi.get("unit_size"))
    ref = _float(pi.get("reference_price"))
    if pack_size is None and pack_price and ref:
        pack_size = round(pack_price / ref, 3)
    unit = str(pi.get("size_format") or "").lower() or "ud"
    # Count-sold packs ("1 ud" = one package of 40 bags / 8 rolls) are priced
    # per piece, so stores selling other pack counts compare fairly.
    if unit == "ud" and pack_size == 1 and _float(pi.get("total_units")):
        pack_size = _float(pi.get("total_units"))
    return {
        "product_id": str(raw.get("id") or ""),
        "url": raw.get("share_url") or "",
        "name": (raw.get("display_name") or raw.get("details", {}).get("description") or "").strip(),
        "brand": raw.get("brand") or "",
        "ean": raw.get("ean") or "",
        "pack_price": pack_price,
        "pack_size": pack_size,
        "unit": unit,
        "unit_name": pi.get("unit_name") or "",
        "unit_price": _float(pi.get("reference_price")),
        "unit_price_format": str(pi.get("reference_format") or "").lower(),
        "approx_size": bool(pi.get("approx_size")),
        "ingredients": (nutrition.get("ingredients") or "").strip(),
    }


def fetch_mercadona(product_id: str, warehouse: Optional[str]) -> Optional[dict]:
    """Current Mercadona product, or None if the API has no such product."""
    params = {"lang": "es"}
    if warehouse:
        params["wh"] = warehouse
    try:
        resp = requests.get(
            _MERCADONA_PRODUCT_API.format(id=product_id), params=params,
            headers=_HTTP_HEADERS, timeout=15,
        )
    except requests.RequestException as err:
        logger.warning("⚠️ Mercadona product %s fetch failed: %s", product_id, err)
        return None
    if resp.status_code != 200:
        logger.warning("⚠️ Mercadona product %s → HTTP %d", product_id, resp.status_code)
        return None
    return parse_mercadona_product(resp.json())


def parse_ametller_product(raw: dict) -> dict:
    """Project an Ametller SCAPI product onto the basket product shape."""
    size = _float(raw.get("unitQuantity")) or _float(raw.get("c_ao_pesoAproximado"))
    return {
        "product_id": str(raw.get("id") or ""),
        "url": raw.get("slugUrl") or "",
        "name": (raw.get("name") or "").strip(),
        "brand": raw.get("brand") or "",
        "ean": raw.get("ean") or "",
        "pack_price": _float(raw.get("price")),
        "pack_size": size,
        "unit": str(raw.get("unitMeasure") or "").lower() or "ud",
        "unit_price": _float(raw.get("pricePerUnit")),
        "unit_price_format": str(raw.get("unitMeasure") or "").lower(),
        "approx_size": raw.get("c_ao_pesoAproximado") is not None,
        "ingredients": (raw.get("c_ao_ingredientes") or "").strip(),
        "origin": raw.get("c_ao_origen") or "",
    }


def resolve_ametller_id(url: str) -> str:
    """Numeric Ametller productId, following legacy ``/p`` redirects."""
    match = re.search(r"/(\d+)\.html", url)
    if match:
        return match.group(1)
    try:
        resp = requests.get(url, headers=_HTTP_HEADERS, timeout=15, allow_redirects=True)
        match = re.search(r"/(\d+)\.html", resp.url)
        if match:
            return match.group(1)
        logger.warning("⚠️ Ametller URL %s did not redirect to a product id", url)
    except requests.RequestException as err:
        logger.warning("⚠️ Ametller URL %s resolve failed: %s", url, err)
    return ""


def fetch_ametller_batch(product_ids: list[str]) -> dict[str, dict]:
    """Fetch Ametller products via SCAPI with a guest token (no Chrome profile)."""
    if not product_ids:
        return {}
    try:
        raws = GuestSession().products(product_ids)
    except (requests.RequestException, RuntimeError) as err:
        logger.error("❌ Ametller product fetch failed: %s", err)
        return {}
    return {str(raw.get("id")): parse_ametller_product(raw) for raw in raws}


# ─────────────────────────────────────────────────────────────────────────────
# Assembly
# ─────────────────────────────────────────────────────────────────────────────


def load_specs(path: Optional[Path] = None) -> dict:
    """Human-reviewed tier/spec overrides, keyed by item key."""
    path = path or state_dir() / "quality_specs.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def postal_code() -> str:
    """Delivery postal code: ``benchmark.postal_code`` or the Ametller one."""
    return str(
        data.CONFIG.get("benchmark", {}).get("postal_code")
        or data.CONFIG["automation"]["ametller_postal_code"]
    )


def select_items(df, qty_by_key: dict[str, float]) -> list[dict]:
    """Inventory rows that belong in the basket, with their monthly quantity.

    A row is in when the logs show it being bought, or its target is > 0.
    """
    cols = data.COLUMNS
    category_col = next((c for c in df.columns if normalize(c) == "categoria"), None)
    items: list[dict] = []
    for _, row in df.iterrows():
        comida = str(row[cols["comida"]]).strip()
        key = item_key(comida)
        target = int(row[cols["cantidad"]] or 0)
        if key in qty_by_key:
            monthly, source = qty_by_key[key], "logs"
        elif target > 0:
            monthly, source = float(target), "target"
        else:
            continue
        category = row[category_col] if category_col else ""
        items.append({
            "key": key,
            "comida": comida,
            "store": str(row[cols["super"]]).strip().lower(),
            "category": "" if category != category else str(category),  # NaN → ""
            "target": target,
            "buscador": str(row[cols["buscador"]]).strip(),
            "monthly_packs": monthly,
            "qty_source": source,
        })
    return items


def build(run_date: str) -> Path:
    """Build and write ``benchmark_runs/<run_date>/basket.json``."""
    df = data.load_inventory_data()
    if df is None:
        raise SystemExit("Inventory could not be loaded — see log above.")
    logs_dir = _REPO_ROOT / data.CONFIG["automation"].get("purchase_logs_dir", "purchase_logs")
    qty_by_key, freq = monthly_quantities(load_purchase_logs(logs_dir))
    items = select_items(df, qty_by_key)
    logger.info("ℹ️ %d basket items (%d from logs, %d from targets)", len(items),
                sum(i["qty_source"] == "logs" for i in items),
                sum(i["qty_source"] == "target" for i in items))

    specs = load_specs()
    excluded = [it for it in items if specs.get(it["key"], {}).get("exclude")]
    items = [it for it in items if it not in excluded]
    for it in excluded:
        logger.info("ℹ️ Excluded %s: %s", it["comida"], specs[it["key"]].get("reason", ""))

    pc = postal_code()
    wh = mercadona_warehouse(pc)
    ametller_ids: dict[str, str] = {}
    for it in items:
        # A reviewed replacement URL wins over a stale/plain-text buscador.
        url = specs.get(it["key"], {}).get("current_url") or it["buscador"]
        if it["store"] == "mercadona":
            match = re.search(r"/product/(\d+)", url)
            it["current"] = fetch_mercadona(match.group(1), wh) if match else None
        elif it["store"] == "ametller" and url.startswith("http"):
            pid = resolve_ametller_id(url)
            if pid:
                ametller_ids[it["key"]] = pid
            it["current"] = None
        else:
            it["current"] = None
    fetched = fetch_ametller_batch(sorted(set(ametller_ids.values())))
    for it in items:
        pid = ametller_ids.get(it["key"])
        if pid:
            it["current"] = fetched.get(pid)

    for it in items:
        cur = it.get("current")
        override = specs.get(it["key"], {})
        if cur and override.get("current_fields"):
            cur.update(override["current_fields"])  # e.g. eggs: 6 ud, not "3 dz"
        tier = override.get("tier") or suggest_tier(it["comida"], it["category"], (cur or {}).get("brand", ""))
        it["tier"] = tier
        it["spec"] = override.get("spec") or auto_spec(tier, cur, it["comida"])
        it["spec_source"] = "override" if override else "auto"
        if cur and cur.get("pack_size"):
            it["monthly_base_qty"] = round(it["monthly_packs"] * cur["pack_size"], 3)
        if not cur or cur.get("pack_price") is None:
            logger.warning("⚠️ %s (%s): no current product/price resolved", it["comida"], it["store"])

    basket = {
        "run_date": run_date,
        "postal_code": pc,
        "mercadona_warehouse": wh,
        "frequency": freq,
        "tier_rules": TIER_RULES,
        "excluded": [{"key": it["key"], "reason": specs[it["key"]].get("reason", "")} for it in excluded],
        "items": items,
    }
    out_dir = runs_dir() / run_date
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / "basket.json"
    out.write_text(json.dumps(basket, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("✅ Wrote %s", out)
    return out


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--date", default=date.today().isoformat(), help="Run date (dir name).")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    build(args.date)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
