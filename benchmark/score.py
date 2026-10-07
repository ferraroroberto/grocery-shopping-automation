"""Score a benchmark run into split scenarios (issue #145). Deterministic, no LLM.

    python -m benchmark.score benchmark_runs/2026-09-27 [--promote]

Inputs: ``<run>/basket.json`` (the monthly basket at today's stores) and
``<run>/stores/<store>.json`` (validated research records, see
:mod:`benchmark.results`). Outputs ``<run>/scenarios.json`` and
``<run>/report.md``.

**Cost model.** An item's monthly cost at a store is *pro-rata*: the monthly
base quantity (``monthly_packs × current pack_size``, in kg / l / ud) divided
by the store's pack size, times its pack price. Pack rounding is deliberately
not applied — over a month of repeated orders a 180 g vs 200 g pack evens out,
and rounding each item up would bias every non-baseline store upward.

Only ``exact`` / ``equivalent`` records count as offers; the store an item is
bought from today is always an offer at today's price (from ``basket.json``).

**Delivery.** Each store in a scenario is ordered from ``orders_per_month``
times (the household's observed frequency, from the purchase logs). The fee
per order comes from the store's ``fee_tiers`` at the per-order value. If that
value is under the store's ``min_order``, orders are merged (fewer, larger
orders) until it clears — or the scenario is flagged when even one monthly
order can't. A store with unknown fees is costed at 0 and flagged.

**Scenarios.** Status quo; each store alone (coverage %, common-subset
comparison, cost with missing items filled in at today's store); the cheapest
fully-covering combinations of 1, 2 and 3 stores (each item goes to its
cheapest store in the combination, including re-splitting today's two stores);
and a no-fees lower bound.
"""

from __future__ import annotations

import argparse
import itertools
import json
import logging
import math
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Optional

_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from benchmark import history  # noqa: E402
from benchmark.paths import mappings_dir  # noqa: E402
from benchmark.results import MATCHED  # noqa: E402

logger = logging.getLogger("benchmark.score")

# A low-confidence match on food can't prove it clears its quality bar, so it
# doesn't count as an offer (reported as "unverified" instead). Commodity
# (tier D) matches still count at low confidence.
UNVERIFIED_TIERS = {"A", "B", "C"}
# Fee-optimised variant: a store may be ordered from less often (never below
# this) when that lets each order clear a free-delivery tier. Fresh food makes
# fewer than two orders a month unrealistic.
MIN_ORDERS_PER_MONTH = 2
OUTLIER_LOW, OUTLIER_HIGH = 0.5, 2.0
PRICE_CHANGE_PCT = 5.0
TOP_N = 5
# Combination search depth, and the bar an extra store must clear to join the
# recommended plan: each added store (another account, delivery slot and
# checkout) has to save at least this much per month over the plan without it.
MAX_COMBO_STORES = 5
EXTRA_STORE_MIN_SAVING = 10.0


@dataclass
class Offer:
    store: str
    key: str
    name: str
    url: str
    pack_size: float
    pack_price: float
    status: str  # baseline | exact | equivalent

    @property
    def unit_price(self) -> float:
        return self.pack_price / self.pack_size


# ─────────────────────────────────────────────────────────────────────────────
# Loading
# ─────────────────────────────────────────────────────────────────────────────


def load_run(run_dir: Path) -> tuple[dict, dict[str, dict]]:
    """``(basket, {store: store_doc})`` for a run directory."""
    basket = json.loads((run_dir / "basket.json").read_text(encoding="utf-8"))
    stores: dict[str, dict] = {}
    for path in sorted((run_dir / "stores").glob("*.json")) if (run_dir / "stores").exists() else []:
        stores[path.stem] = json.loads(path.read_text(encoding="utf-8"))
    return basket, stores


def monthly_base(item: dict) -> Optional[float]:
    """Monthly quantity in the item's unit, or None when unpriceable."""
    cur = item.get("current") or {}
    if not cur.get("pack_size") or cur.get("pack_price") is None:
        return None
    return float(item["monthly_packs"]) * float(cur["pack_size"])


def is_unverified(item: dict, rec: dict) -> bool:
    """A food match whose quality couldn't be verified (confidence low)."""
    return item.get("tier") in UNVERIFIED_TIERS and rec.get("confidence") == "low"


def record_candidates(rec: Optional[dict]) -> list[dict]:
    """The matched record and its matched ``alternatives`` (each a full record)."""
    if not rec:
        return []
    return [c for c in [rec] + list(rec.get("alternatives") or [])
            if c.get("status") in MATCHED and c.get("pack_size") and c.get("pack_price")]


def baseline_offer(item: dict) -> Offer:
    """Today's product at today's store — what the status quo buys."""
    cur = item["current"]
    return Offer(item["store"], item["key"], cur.get("name", ""), cur.get("url", ""),
                 float(cur["pack_size"]), float(cur["pack_price"]), "baseline")


def build_offers(basket: dict, stores: dict[str, dict]) -> dict[str, dict[str, Offer]]:
    """Item key → {store → the best qualifying Offer at that store}.

    Every qualifying candidate a store record carries — the record itself
    plus its ``alternatives`` — is compared on €/unit (the household buys in
    volume, so a bigger pack at a lower €/kg is a real saving) and the
    cheapest wins. Today's store always offers today's product; a qualifying
    *different, cheaper* product at that same store (e.g. Ametller's own
    cleaner, cheaper ham) replaces it as a ``same_store_swap`` — plans may use
    it, the status quo never does (it uses :func:`baseline_offer`). Unverified
    matches (:func:`is_unverified`) are left out.
    """
    offers: dict[str, dict[str, Offer]] = {}
    for item in basket["items"]:
        if monthly_base(item) is None:
            continue
        base = baseline_offer(item)
        per_store = {item["store"]: base}
        for store, doc in stores.items():
            candidates = [c for c in record_candidates((doc.get("items") or {}).get(item["key"]))
                          if not is_unverified(item, c)]
            if not candidates:
                continue
            rec = min(candidates, key=lambda c: float(c["pack_price"]) / float(c["pack_size"]))
            offer = Offer(store, item["key"], rec.get("name", ""), rec.get("url", ""),
                          float(rec["pack_size"]), float(rec["pack_price"]), rec["status"])
            if store == item["store"]:
                if offer.url != base.url and offer.unit_price < base.unit_price:
                    offer.status = "same_store_swap"
                    per_store[store] = offer
                continue
            per_store[store] = offer
        offers[item["key"]] = per_store
    return offers


def cost(item: dict, offer: Offer) -> float:
    """Pro-rata monthly cost of ``item`` bought as ``offer``."""
    return monthly_base(item) / offer.pack_size * offer.pack_price


# ─────────────────────────────────────────────────────────────────────────────
# Delivery
# ─────────────────────────────────────────────────────────────────────────────


def fee_for(order_value: float, fee_tiers: list[dict]) -> Optional[float]:
    """Fee of the highest tier whose ``min_order`` ≤ ``order_value``."""
    if not fee_tiers:
        return None
    fee = None
    for tier in sorted(fee_tiers, key=lambda t: t["min_order"]):
        if order_value >= tier["min_order"]:
            fee = float(tier["fee"])
    return fee if fee is not None else float(sorted(fee_tiers, key=lambda t: t["min_order"])[0]["fee"])


def delivery_cost(spend: float, delivery: dict, orders_per_month: float) -> dict:
    """Monthly delivery cost for ``spend`` at one store.

    Returns ``{"orders", "order_value", "fee_per_order", "monthly_fee", "flags"}``.
    """
    flags: list[str] = []
    if spend <= 0:
        return {"orders": 0, "order_value": 0.0, "fee_per_order": 0.0, "monthly_fee": 0.0, "flags": flags}
    orders = max(1.0, orders_per_month)
    min_order = delivery.get("min_order")
    if min_order and spend / orders < min_order:
        orders = max(1.0, math.floor(spend / min_order))
        flags.append(f"orders merged to {orders:g}/month to clear the {min_order:g} EUR minimum")
        if spend < min_order:
            flags.append(f"monthly spend {spend:.2f} is below the {min_order:g} EUR minimum order")
    order_value = spend / orders
    fee = fee_for(order_value, delivery.get("fee_tiers") or [])
    if fee is None:
        flags.append("delivery fee unknown — costed at 0")
        fee = 0.0
    if delivery.get("delivers") == "no":
        flags.append("store does NOT deliver to the basket postal code")
    elif delivery.get("delivers") != "yes":
        flags.append("delivery to the basket postal code not confirmed")
    return {
        "orders": round(orders, 2), "order_value": round(order_value, 2),
        "fee_per_order": fee, "monthly_fee": round(orders * fee, 2), "flags": flags,
    }


# ─────────────────────────────────────────────────────────────────────────────
# Scenarios
# ─────────────────────────────────────────────────────────────────────────────


def optimised_delivery(spend: float, delivery: dict, orders_per_month: float) -> dict:
    """Cheapest delivery over whole order counts from today's down to the floor."""
    counts = [orders_per_month] + list(range(math.floor(orders_per_month), MIN_ORDERS_PER_MONTH - 1, -1))
    options = [delivery_cost(spend, delivery, n) for n in counts if n > 0]
    return min(options, key=lambda d: (d["monthly_fee"], -d["orders"]))


def price_assignment(assignment: dict[str, Offer], items: dict[str, dict],
                     deliveries: dict[str, dict], orders_per_month: float) -> dict:
    """Total a {key: Offer} assignment: goods per store + delivery.

    ``delivery``/``total`` keep today's order frequency; ``*_optimised``
    let each store be ordered less often (>= MIN_ORDERS_PER_MONTH) when that
    clears a free-delivery tier.
    """
    spend: dict[str, float] = {}
    for key, offer in assignment.items():
        spend[offer.store] = spend.get(offer.store, 0.0) + cost(items[key], offer)
    per_store = {}
    for store, s in sorted(spend.items()):
        d = delivery_cost(s, deliveries.get(store, {}), orders_per_month)
        opt = optimised_delivery(s, deliveries.get(store, {}), orders_per_month)
        per_store[store] = {"goods": round(s, 2), **d,
                            "optimised_orders": opt["orders"], "optimised_fee": opt["monthly_fee"]}
    goods = sum(spend.values())
    fees = sum(v["monthly_fee"] for v in per_store.values())
    fees_opt = sum(v["optimised_fee"] for v in per_store.values())
    return {
        "goods": round(goods, 2), "delivery": round(fees, 2), "total": round(goods + fees, 2),
        "delivery_optimised": round(fees_opt, 2), "total_optimised": round(goods + fees_opt, 2),
        "per_store": per_store,
        "items": {k: o.store for k, o in sorted(assignment.items())},
    }


def cheapest_in(combo: tuple[str, ...], key: str, item: dict,
                offers: dict[str, dict[str, Offer]]) -> Optional[Offer]:
    candidates = [o for s, o in offers[key].items() if s in combo]
    return min(candidates, key=lambda o: cost(item, o)) if candidates else None


def best_combos(k: int, stores: list[str], items: dict[str, dict],
                offers: dict[str, dict[str, Offer]], deliveries: dict[str, dict],
                orders_per_month: float) -> list[dict]:
    """Every fully-covering k-store combination, cheapest total first."""
    out = []
    for combo in itertools.combinations(sorted(stores), k):
        assignment = {}
        for key, item in items.items():
            best = cheapest_in(combo, key, item, offers)
            if best is None:
                break
            assignment[key] = best
        else:
            priced = price_assignment(assignment, items, deliveries, orders_per_month)
            out.append({"stores": list(combo), **priced})
    return sorted(out, key=lambda r: r["total_optimised"])


def recommend(best_by_k: dict[str, list[dict]]) -> tuple[Optional[dict], Optional[dict]]:
    """``(max_savings, recommended)`` over the best combination per store count.

    ``max_savings`` is the cheapest fully-covering plan at any size;
    ``recommended`` grows one store at a time and only accepts a bigger plan
    when it saves at least EXTRA_STORE_MIN_SAVING per month (fee-optimised)
    for *each* store it adds — jumping past a rejected size must still pay
    the threshold once per extra store.
    """
    tops = [rows[0] for _, rows in sorted(best_by_k.items(), key=lambda kv: int(kv[0])) if rows]
    if not tops:
        return None, None
    best = min(tops, key=lambda r: r["total_optimised"])
    rec = tops[0]
    for nxt in tops[1:]:
        extra = len(nxt["stores"]) - len(rec["stores"])
        if rec["total_optimised"] - nxt["total_optimised"] >= EXTRA_STORE_MIN_SAVING * extra:
            rec = nxt
    return best, rec


def single_store(store: str, items: dict[str, dict], offers: dict[str, dict[str, Offer]],
                 deliveries: dict[str, dict], orders_per_month: float) -> dict:
    """One store alone: coverage, common-subset comparison, filled-in total."""
    covered = {k: offers[k][store] for k in items if store in offers[k]}
    missing = sorted(k for k in items if store not in offers[k])
    baseline = {k: baseline_offer(items[k]) for k in items}
    subset_store = sum(cost(items[k], o) for k, o in covered.items())
    subset_base = sum(cost(items[k], baseline[k]) for k in covered)
    filled = {**covered, **{k: baseline[k] for k in missing}}
    return {
        "store": store,
        "coverage_pct": round(100 * len(covered) / len(items), 1) if items else 0.0,
        "covered": len(covered), "missing": missing,
        "common_subset": {
            "store_goods": round(subset_store, 2), "status_quo_goods": round(subset_base, 2),
            "delta_pct": round(100 * (subset_store / subset_base - 1), 1) if subset_base else None,
        },
        "alone": price_assignment(covered, items, deliveries, orders_per_month) if covered else None,
        "filled_in": price_assignment(filled, items, deliveries, orders_per_month),
    }


def outliers(items: dict[str, dict], offers: dict[str, dict[str, Offer]]) -> list[dict]:
    """Offers whose €/unit is far from the baseline's — likely unit/pack errors."""
    out = []
    for key, per_store in offers.items():
        base = baseline_offer(items[key])
        for store, o in per_store.items():
            if o.status == "baseline":
                continue
            ratio = o.unit_price / base.unit_price
            if ratio < OUTLIER_LOW or ratio > OUTLIER_HIGH:
                out.append({"key": key, "store": store, "ratio": round(ratio, 2),
                            "unit_price": round(o.unit_price, 2),
                            "baseline_unit_price": round(base.unit_price, 2), "name": o.name})
    return sorted(out, key=lambda r: r["ratio"])


def upgrades(basket: dict, stores: dict[str, dict]) -> list[dict]:
    """Tier-B UPGRADE options, with the monthly cost delta vs today."""
    out = []
    for item in basket["items"]:
        base = monthly_base(item)
        if base is None:
            continue
        today = cost(item, Offer(item["store"], item["key"], "", "", float(item["current"]["pack_size"]),
                                 float(item["current"]["pack_price"]), "baseline"))
        for store, doc in stores.items():
            up = ((doc.get("items") or {}).get(item["key"]) or {}).get("upgrade")
            if not up:
                continue
            try:
                size, price = float(up["pack_size"]), float(up["pack_price"])
            except (KeyError, TypeError, ValueError):
                continue
            if size <= 0:
                continue
            monthly = base / size * price
            out.append({"key": item["key"], "store": store, "name": up.get("name", ""),
                        "url": up.get("url", ""), "unit_price": round(price / size, 2),
                        "monthly": round(monthly, 2), "delta_vs_today": round(monthly - today, 2),
                        "evidence": up.get("evidence", "")})
    return sorted(out, key=lambda r: (r["key"], r["monthly"]))


def unverified(basket: dict, stores: dict[str, dict]) -> dict[str, list[str]]:
    """Store → item keys matched only at low confidence (not counted)."""
    items = {it["key"]: it for it in basket["items"]}
    out: dict[str, list[str]] = {}
    for store, doc in stores.items():
        keys = sorted(k for k, rec in (doc.get("items") or {}).items()
                      if k in items and items[k]["store"] != store and record_candidates(rec)
                      and all(is_unverified(items[k], c) for c in record_candidates(rec)))
        if keys:
            out[store] = keys
    return out


def previous_run(run_dir: Path) -> Optional[Path]:
    """The latest sibling run before this one that has a scenarios.json."""
    siblings = sorted(p for p in run_dir.parent.iterdir()
                      if p.is_dir() and not p.name.startswith("_") and p.name < run_dir.name
                      and (p / "scenarios.json").exists())
    return siblings[-1] if siblings else None


def diff_runs(prev: dict, unit_prices: dict[str, dict[str, float]]) -> dict:
    """Unit-price moves > PRICE_CHANGE_PCT and offers that disappeared."""
    old = prev.get("unit_prices", {})
    moves, gone = [], []
    for key, per_store in old.items():
        for store, p in per_store.items():
            now = unit_prices.get(key, {}).get(store)
            if now is None:
                gone.append({"key": key, "store": store})
            elif p and abs(now / p - 1) * 100 > PRICE_CHANGE_PCT:
                moves.append({"key": key, "store": store, "before": p, "now": now,
                              "pct": round(100 * (now / p - 1), 1)})
    return {"previous_run": prev.get("run_date"), "price_moves": sorted(moves, key=lambda m: m["pct"]),
            "no_longer_offered": gone}


def score(run_dir: Path) -> dict:
    basket, stores = load_run(run_dir)
    items = {it["key"]: it for it in basket["items"] if monthly_base(it) is not None}
    unpriced = sorted(it["key"] for it in basket["items"] if monthly_base(it) is None)
    offers = build_offers(basket, stores)
    freq = basket.get("frequency", {}).get("orders_per_month", {})
    orders_per_month = max(freq.values()) if freq else 4.0
    all_stores = sorted({s for per in offers.values() for s in per})
    deliveries = {s: (stores.get(s) or {}).get("delivery") or {} for s in all_stores}
    eligible = [s for s in all_stores if deliveries[s].get("delivers") != "no"]

    best_by_k = {str(k): best_combos(k, eligible, items, offers, deliveries, orders_per_month)[:TOP_N]
                 for k in range(1, MAX_COMBO_STORES + 1)}
    max_savings, recommended = recommend(best_by_k)
    status_quo = price_assignment(
        {k: baseline_offer(items[k]) for k in items},
        items, deliveries, orders_per_month,
    )
    lower = sum(min(cost(items[k], o) for o in offers[k].values()) for k in items)
    unit_prices = {k: {s: round(o.unit_price, 4) for s, o in per.items()} for k, per in offers.items()}

    result = {
        "run_date": basket.get("run_date"),
        "orders_per_month": orders_per_month,
        "items_scored": len(items),
        "unpriced_items": unpriced,
        "stores": all_stores,
        "deliveries": deliveries,
        "status_quo": status_quo,
        "single_store": [single_store(s, items, offers, deliveries, orders_per_month) for s in all_stores],
        "best": best_by_k,
        "max_savings": max_savings,
        "recommended": recommended,
        "resplit_current": (best_combos(2, ["ametller", "mercadona"], items, offers, deliveries,
                                        orders_per_month) or [None])[0],
        "lower_bound_goods": round(lower, 2),
        "outliers": outliers(items, offers),
        "unverified": unverified(basket, stores),
        "upgrades": upgrades(basket, stores),
        "unit_prices": unit_prices,
    }
    prev = previous_run(run_dir)
    if prev:
        result["diff"] = diff_runs(json.loads((prev / "scenarios.json").read_text(encoding="utf-8")),
                                   unit_prices)
    return result


# ─────────────────────────────────────────────────────────────────────────────
# Report + promotion
# ─────────────────────────────────────────────────────────────────────────────


def _eur(x: Optional[float]) -> str:
    return "—" if x is None else f"{x:,.2f} €"


def render_report(res: dict) -> str:
    sq = res["status_quo"]
    lines = [
        f"# Supermarket benchmark — {res['run_date']}", "",
        f"{res['items_scored']} items scored · {res['orders_per_month']:g} orders/month per store · "
        f"unpriced: {', '.join(res['unpriced_items']) or 'none'}", "",
        "## Status quo", "",
        f"**{_eur(sq['total'])}/month** = goods {_eur(sq['goods'])} + delivery {_eur(sq['delivery'])} "
        f"(fee-optimised ordering: {_eur(sq['total_optimised'])})", "",
        "| Store | Goods | Orders | Fee/order | Delivery | Fee-optimised orders → delivery | Flags |",
        "|---|---|---|---|---|---|---|",
    ]
    for s, v in sq["per_store"].items():
        lines.append(f"| {s} | {_eur(v['goods'])} | {v['orders']:g} | {_eur(v['fee_per_order'])} | "
                     f"{_eur(v['monthly_fee'])} | {v['optimised_orders']:g} → {_eur(v['optimised_fee'])} | "
                     f"{'; '.join(v['flags'])} |")
    rec, top = res.get("recommended"), res.get("max_savings")
    if rec:
        lines += ["", "## Recommended plan", "",
                  f"**{' + '.join(rec['stores'])}**: {_eur(rec['total_optimised'])}/month fee-optimised "
                  f"({rec['total_optimised'] - sq['total_optimised']:+.2f} € vs status quo). Each extra store "
                  f"must save ≥ {EXTRA_STORE_MIN_SAVING:g} €/month to join; the absolute maximum "
                  f"({' + '.join(top['stores'])}) is {_eur(top['total_optimised'])}.", ""]
        for store, v in rec["per_store"].items():
            keys = [k for k, s2 in rec["items"].items() if s2 == store]
            lines.append(f"- **{store}** — {_eur(v['goods'])} goods, {v['optimised_orders']:g} orders/month: "
                         f"{', '.join(keys)}")
    lines += ["", "## Best fully-covering combinations (ranked fee-optimised)", ""]
    for k, rows in res["best"].items():
        lines += [f"### {k} store(s)", ""]
        if not rows:
            lines += ["_No combination covers every item._", ""]
            continue
        lines += ["| Stores | Goods | Delivery | Total | vs status quo | Total, fee-optimised | vs status quo |",
                  "|---|---|---|---|---|---|---|"]
        for r in rows:
            lines.append(f"| {' + '.join(r['stores'])} | {_eur(r['goods'])} | {_eur(r['delivery'])} | "
                         f"{_eur(r['total'])} | {r['total'] - sq['total']:+.2f} € | "
                         f"{_eur(r['total_optimised'])} | {r['total_optimised'] - sq['total_optimised']:+.2f} € |")
        lines.append("")
    rs = res.get("resplit_current")
    if rs:
        moved = [k for k, s in rs["items"].items() if s != sq["items"].get(k)]
        lines += ["## Re-split of today's two stores", "",
                  f"{_eur(rs['total'])} ({rs['total'] - sq['total']:+.2f} €/month; fee-optimised "
                  f"{_eur(rs['total_optimised'])}, {rs['total_optimised'] - sq['total_optimised']:+.2f} €); moves: "
                  f"{', '.join(f'{k}→{rs['items'][k]}' for k in moved) or 'none'}", ""]
    lines += ["## Each store alone", "",
              "| Store | Coverage | Common-subset Δ vs today | Alone (covered items) | Filled-in total | Missing |",
              "|---|---|---|---|---|---|"]
    for s in res["single_store"]:
        cs = s["common_subset"]
        delta = "—" if cs["delta_pct"] is None else f"{cs['delta_pct']:+.1f} %"
        alone = _eur(s["alone"]["total"]) if s["alone"] else "—"
        lines.append(f"| {s['store']} | {s['coverage_pct']} % ({s['covered']}) | {delta} | {alone} | "
                     f"{_eur(s['filled_in']['total'])} | {len(s['missing'])} |")
    lines += ["", f"Lower bound (every item at its cheapest store, no fees): **{_eur(res['lower_bound_goods'])}**", ""]
    if res["upgrades"]:
        lines += ["## Quality upgrades (tier-B UPGRADE bar)", "",
                  "| Item | Store | Product | €/unit | Monthly | Δ vs today |", "|---|---|---|---|---|---|"]
        for u in res["upgrades"]:
            lines.append(f"| {u['key']} | {u['store']} | [{u['name']}]({u['url']}) | {u['unit_price']:.2f} | "
                         f"{_eur(u['monthly'])} | {u['delta_vs_today']:+.2f} € |")
        lines.append("")
    if res["unverified"]:
        lines += ["## Unverified matches (low confidence on food — not counted)", ""]
        for store, keys in res["unverified"].items():
            lines.append(f"- **{store}** ({len(keys)}): {', '.join(keys)}")
        lines.append("")
    if res["outliers"]:
        lines += ["## Outliers to audit (€/unit < 0.5× or > 2× today)", "",
                  "| Item | Store | Ratio | €/unit | Today €/unit | Product |", "|---|---|---|---|---|---|"]
        for o in res["outliers"]:
            lines.append(f"| {o['key']} | {o['store']} | {o['ratio']} | {o['unit_price']} | "
                         f"{o['baseline_unit_price']} | {o['name']} |")
        lines.append("")
    if res.get("diff"):
        d = res["diff"]
        lines += [f"## Changes since {d['previous_run']}", "",
                  f"{len(d['price_moves'])} price moves > {PRICE_CHANGE_PCT:g} %, "
                  f"{len(d['no_longer_offered'])} offers gone.", ""]
        for m in d["price_moves"]:
            lines.append(f"- {m['key']} @ {m['store']}: {m['before']} → {m['now']} ({m['pct']:+.1f} %)")
        lines.append("")
    return "\n".join(lines)


def promote(run_dir: Path, stores: dict[str, dict]) -> list[Path]:
    """Carry this run's matches forward as next run's starting mappings.

    Each mapping keeps its qualifying ``alternatives`` (e.g. a bulk pack next
    to the usual size) and its confidence, so the next run re-checks every
    priced candidate instead of re-searching for the ones it would lose.
    """
    fields = ("status", "confidence", "name", "brand", "url", "ean", "pack_size", "unit")
    target = mappings_dir()
    target.mkdir(parents=True, exist_ok=True)
    written = []
    for store, doc in stores.items():
        mapping = {}
        for key, rec in (doc.get("items") or {}).items():
            candidates = record_candidates(rec)
            if not candidates:
                continue
            first, *others = [{f: c.get(f) for f in fields} for c in candidates]
            mapping[key] = first | ({"alternatives": others} if others else {}) | {"verified_run": run_dir.name}
        path = target / f"{store}.json"
        path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2), encoding="utf-8")
        written.append(path)
    return written


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--promote", action="store_true",
                        help="Finalise the run: write verified matches to <runs_dir>/_state/mappings/ "
                             "and record it in the run history (benchmark.history).")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = score(args.run_dir)
    (args.run_dir / "scenarios.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    report = render_report(res)
    (args.run_dir / "report.md").write_text(report, encoding="utf-8")
    logger.info("✅ Wrote %s and report.md", args.run_dir / "scenarios.json")
    if args.promote:
        for p in promote(args.run_dir, load_run(args.run_dir)[1]):
            logger.info("✅ Promoted mapping %s", p)
        history.record(args.run_dir)
        logger.info("✅ Recorded run in %s and %s", history.history_path().name, history.prices_path().name)
    print(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
