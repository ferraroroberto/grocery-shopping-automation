"""Unit tests for the supermarket benchmark scorer + result writer (issue #145)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from benchmark import results, score
from src import data, store_links


def _item(key: str, store: str, packs: float, size: float, price: float, unit: str = "kg") -> dict:
    return {
        "key": key, "comida": key, "store": store, "tier": "C", "monthly_packs": packs,
        "current": {"name": key, "url": f"https://{store}/{key}", "pack_size": size,
                    "unit": unit, "pack_price": price},
    }


def _rec(size: float, price: float, status: str = "equivalent", unit: str = "kg") -> dict:
    return {"status": status, "name": "x", "url": "https://x", "evidence": "ingredients",
            "pack_size": size, "unit": unit, "pack_price": price}


@pytest.fixture()
def run_dir(tmp_path: Path) -> Path:
    """Two baseline stores + one challenger (``cheap``) with a fee structure."""
    basket = {
        "run_date": "2026-09-27",
        "frequency": {"orders_per_month": {"mercadona": 4.0, "ametller": 4.0}},
        "items": [
            _item("rice", "mercadona", packs=4, size=1.0, price=2.0),     # 8 €/month
            _item("ham", "ametller", packs=10, size=0.2, price=5.0),      # 50 €/month
            _item("eggs", "ametller", packs=2, size=6, price=3.0, unit="ud"),  # 6 €/month
        ],
    }
    (tmp_path / "basket.json").write_text(json.dumps(basket), encoding="utf-8")
    stores = {
        "mercadona": {"delivery": {"delivers": "yes", "fee_tiers": [{"min_order": 0, "fee": 8.0}]},
                      "items": {"ham": _rec(0.25, 5.0)}},  # 20 €/kg vs 25 today
        "ametller": {"delivery": {"delivers": "yes",
                                  "fee_tiers": [{"min_order": 0, "fee": 5.95}, {"min_order": 60, "fee": 0}]},
                     "items": {}},
        "cheap": {"delivery": {"delivers": "yes", "min_order": 50, "fee_tiers": [{"min_order": 0, "fee": 4.0}]},
                  "items": {"rice": _rec(1.0, 1.0), "ham": _rec(0.2, 3.0),
                            "eggs": _rec(6, 1.5, unit="ud")}},
    }
    (tmp_path / "stores").mkdir()
    for name, doc in stores.items():
        (tmp_path / "stores" / f"{name}.json").write_text(json.dumps(doc), encoding="utf-8")
    return tmp_path


def test_cost_is_pro_rata_across_pack_sizes():
    item = _item("ham", "ametller", packs=10, size=0.2, price=5.0)  # 2 kg / month
    offer = score.Offer("m", "ham", "", "", pack_size=0.25, pack_price=5.0, status="exact")
    assert score.cost(item, offer) == pytest.approx(40.0)  # 2 kg at 20 €/kg, no pack rounding


def test_fee_for_picks_highest_reached_tier():
    tiers = [{"min_order": 0, "fee": 5.95}, {"min_order": 60, "fee": 0.0}]
    assert score.fee_for(59.99, tiers) == 5.95
    assert score.fee_for(60, tiers) == 0.0
    assert score.fee_for(10, []) is None


def test_delivery_merges_orders_below_minimum():
    d = score.delivery_cost(120.0, {"delivers": "yes", "min_order": 50,
                                    "fee_tiers": [{"min_order": 0, "fee": 4.0}]}, orders_per_month=4)
    assert d["orders"] == 2  # 30 €/order < 50 → merged into 2 orders of 60 €
    assert d["monthly_fee"] == pytest.approx(8.0)
    assert any("merged" in f for f in d["flags"])


def test_delivery_unknown_fee_is_flagged_not_hidden():
    d = score.delivery_cost(100.0, {}, orders_per_month=4)
    assert d["monthly_fee"] == 0.0
    assert any("unknown" in f for f in d["flags"])
    assert any("not confirmed" in f for f in d["flags"])


def test_status_quo_and_best_split(run_dir: Path):
    res = score.score(run_dir)
    sq = res["status_quo"]
    assert sq["goods"] == pytest.approx(64.0)
    # mercadona 8 € over 4 orders @ 8 € fee; ametller 56 € over 4 orders (<60) @ 5.95
    assert sq["delivery"] == pytest.approx(4 * 8.0 + 4 * 5.95)
    best1 = res["best"]["1"][0]
    assert best1["stores"] == ["cheap"]  # only store covering all three items
    # cheap: rice 4 kg @1 €/kg=4, ham 2 kg @15=30, eggs 12 ud @0.25=3 → 37 €, below 50 € min
    assert best1["goods"] == pytest.approx(37.0)
    assert best1["per_store"]["cheap"]["orders"] == 1
    assert best1["total"] == pytest.approx(41.0)
    assert res["lower_bound_goods"] == pytest.approx(37.0)


def test_resplit_moves_ham_to_mercadona(run_dir: Path):
    rs = score.score(run_dir)["resplit_current"]
    assert rs["items"]["ham"] == "mercadona"
    assert rs["items"]["eggs"] == "ametller"


def test_single_store_coverage_and_common_subset(run_dir: Path):
    merc = next(s for s in score.score(run_dir)["single_store"] if s["store"] == "mercadona")
    assert merc["covered"] == 2 and merc["missing"] == ["eggs"]
    assert merc["common_subset"]["status_quo_goods"] == pytest.approx(58.0)
    assert merc["common_subset"]["store_goods"] == pytest.approx(48.0)


def test_outlier_flags_only_far_unit_prices():
    item = _item("ham", "ametller", packs=10, size=0.2, price=5.0)  # 25 €/kg today
    base = score.Offer("ametller", "ham", "", "", 0.2, 5.0, "baseline")
    offers = {"ham": {
        "ametller": base,
        "grams_typo": score.Offer("grams_typo", "ham", "", "", 200, 5.0, "exact"),  # 0.025 €/kg
        "fair": score.Offer("fair", "ham", "", "", 0.2, 4.0, "exact"),              # 0.8×
        "boundary": score.Offer("boundary", "ham", "", "", 0.2, 2.5, "exact"),      # exactly 0.5×
    }}
    flagged = {o["store"] for o in score.outliers({"ham": item}, offers)}
    assert flagged == {"grams_typo"}


def test_diff_against_previous_run(run_dir: Path, tmp_path: Path):
    prev = {"run_date": "2026-08-01", "unit_prices": {"ham": {"cheap": 10.0, "gone": 1.0}}}
    diff = score.diff_runs(prev, {"ham": {"cheap": 15.0}})
    assert diff["price_moves"][0]["pct"] == pytest.approx(50.0)
    assert diff["no_longer_offered"] == [{"key": "ham", "store": "gone"}]


def test_results_validator_rejects_unit_mismatch():
    with pytest.raises(results.ResultError, match="unit must be 'kg'"):
        results.validate_item("ham", _rec(200, 3.0, unit="g"), {"ham": "kg"})


def test_results_validator_requires_evidence_for_match():
    rec = _rec(0.2, 3.0)
    rec["evidence"] = ""
    with pytest.raises(results.ResultError, match="evidence"):
        results.validate_item("ham", rec, {"ham": "kg"})


def test_results_not_found_needs_no_price():
    assert results.validate_item("ham", {"status": "not_found"}, {"ham": "kg"})["status"] == "not_found"


def test_results_validator_rejects_carrefour_url_without_product_id():
    rec = {**_rec(0.5, 3.0), "url": "https://www.carrefour.es/supermercado/copos-de-avena-500-g/p"}
    with pytest.raises(results.ResultError, match="product card"):
        results.validate_item("ham", rec, {"ham": "kg"}, store="carrefour")


def test_results_validator_accepts_valid_carrefour_product_url():
    rec = {**_rec(0.5, 3.0), "url": "https://www.carrefour.es/supermercado/copos-de-avena/R-VC4AECOMM-081271/p"}
    assert results.validate_item("ham", rec, {"ham": "kg"}, store="carrefour")["status"] == "equivalent"


def test_results_validator_mercadona_product_pattern():
    ok = {**_rec(0.5, 3.0), "url": "https://tienda.mercadona.es/product/5507/arandanos-tarrina"}
    assert results.validate_item("ham", ok, {"ham": "kg"}, store="mercadona")["status"] == "equivalent"
    bad = {**_rec(0.5, 3.0), "url": "https://tienda.mercadona.es/search?q=arandanos"}
    with pytest.raises(results.ResultError, match="product card"):
        results.validate_item("ham", bad, {"ham": "kg"}, store="mercadona")


def test_results_validator_store_without_pattern_is_unchecked():
    rec = {**_rec(0.5, 3.0), "url": "https://www.dia.es/whatever-shape"}
    assert results.validate_item("ham", rec, {"ham": "kg"}, store="dia")["status"] == "equivalent"


def test_results_validator_rejects_bad_alternative_url():
    rec = {**_rec(0.5, 3.0), "url": "https://www.carrefour.es/supermercado/x/R-good-id/p",
           "alternatives": [{**_rec(1.0, 5.0), "url": "https://www.carrefour.es/supermercado/no-id/p"}]}
    with pytest.raises(results.ResultError, match="product card"):
        results.validate_item("ham", rec, {"ham": "kg"}, store="carrefour")


def test_low_confidence_food_match_is_not_counted_but_reported():
    basket = {"items": [_item("ham", "ametller", 10, 0.2, 5.0), _item("soap", "mercadona", 1, 1.0, 2.0, unit="l")]}
    basket["items"][0]["tier"], basket["items"][1]["tier"] = "B", "D"
    low_ham = {**_rec(0.2, 1.0), "confidence": "low"}
    low_soap = {**_rec(1.0, 1.0, unit="l"), "confidence": "low"}
    stores = {"cheap": {"items": {"ham": low_ham, "soap": low_soap}}}
    offers = score.build_offers(basket, stores)
    assert "cheap" not in offers["ham"]   # food, low confidence → unverified
    assert "cheap" in offers["soap"]      # commodity still counts
    assert score.unverified(basket, stores) == {"cheap": ["ham"]}


def test_optimised_delivery_orders_less_to_clear_free_tier_but_not_below_floor():
    tiers = {"delivers": "yes", "fee_tiers": [{"min_order": 0, "fee": 5.95}, {"min_order": 60, "fee": 0}]}
    d = score.optimised_delivery(150.0, tiers, orders_per_month=3.62)
    assert d["orders"] == 2 and d["monthly_fee"] == 0.0     # 75 €/order clears the 60 € tier
    d = score.optimised_delivery(100.0, tiers, orders_per_month=3.62)
    assert d["orders"] == 2 and d["monthly_fee"] == pytest.approx(11.9)  # floor of 2 orders holds


def test_recommend_adds_a_store_only_when_it_earns_the_threshold():
    best = {"1": [], "2": [{"stores": ["a", "b"], "total_optimised": 100.0}],
            "3": [{"stores": ["a", "b", "c"], "total_optimised": 85.0}],
            "4": [{"stores": ["a", "b", "c", "d"], "total_optimised": 80.0}]}
    top, rec = score.recommend(best)
    assert top["stores"] == ["a", "b", "c", "d"]
    assert rec["stores"] == ["a", "b", "c"]  # the 4th store saves only 5 < 10


def test_recommend_charges_the_threshold_per_extra_store_when_skipping_a_size():
    best = {"3": [{"stores": ["a", "b", "c"], "total_optimised": 717.17}],
            "4": [{"stores": ["a", "b", "c", "d"], "total_optimised": 709.68}],
            "5": [{"stores": ["a", "b", "c", "d", "e"], "total_optimised": 706.85}]}
    _, rec = score.recommend(best)
    assert rec["stores"] == ["a", "b", "c"]  # 2 more stores for 10.32 is 5.16 each, < 10


def test_same_store_swap_used_by_plans_not_by_status_quo(run_dir: Path):
    doc = json.loads((run_dir / "stores" / "ametller.json").read_text(encoding="utf-8"))
    doc["items"]["ham"] = {**_rec(0.2, 3.5), "url": "https://ametller/cheaper-ham"}
    (run_dir / "stores" / "ametller.json").write_text(json.dumps(doc), encoding="utf-8")
    res = score.score(run_dir)
    assert res["status_quo"]["goods"] == pytest.approx(64.0)   # today's products, unchanged
    basket, stores = score.load_run(run_dir)
    offers = score.build_offers(basket, stores)
    assert offers["ham"]["ametller"].status == "same_store_swap"
    assert offers["ham"]["ametller"].pack_price == 3.5


def test_cheapest_per_unit_candidate_wins_including_alternatives():
    peas = _item("peas", "mercadona", 3, 0.3, 1.05)   # 3.50 €/kg today
    basket = {"items": [peas]}
    small = {**_rec(0.3, 0.9), "alternatives": [{**_rec(1.0, 1.5), "name": "1 kg bag"}]}
    offers = score.build_offers(basket, {"big": {"items": {"peas": small}}})
    assert offers["peas"]["big"].name == "1 kg bag"       # 1.50 €/kg beats 3.00 €/kg
    assert score.cost(peas, offers["peas"]["big"]) == pytest.approx(0.9 * 1.5)


def test_add_alternative_promotes_over_not_found_and_appends_to_a_match():
    units = {"peas": "kg"}
    promoted = results.add_alternative({"status": "not_found", "rejected": [{"name": "x"}]},
                                       _rec(1.0, 1.5), "peas", units)
    assert promoted["status"] == "equivalent" and promoted["rejected"] == [{"name": "x"}]
    appended = results.add_alternative(_rec(0.3, 0.9), {**_rec(1.0, 1.5), "url": "https://big"}, "peas", units)
    assert [a["url"] for a in appended["alternatives"]] == ["https://big"]
    with pytest.raises(results.ResultError):
        results.add_alternative(_rec(0.3, 0.9), {**_rec(1000, 1.5), "unit": "g"}, "peas", units)


def test_promote_keeps_alternatives_and_confidence(tmp_path: Path, monkeypatch):
    monkeypatch.setitem(data.CONFIG, "benchmark", {"runs_dir": str(tmp_path / "runs")})
    rec = {**_rec(0.3, 0.9), "confidence": "high", "alternatives": [{**_rec(1.0, 1.5), "url": "https://big"}]}
    stores = {"big": {"items": {"peas": rec, "gone": {"status": "not_found"}}}}
    score.promote(tmp_path / "2026-09-27", stores)
    # written where the app's "Import latest run" reads: <runs_dir>/_state/mappings
    mapping = json.loads((tmp_path / "runs" / "_state" / "mappings" / "big.json").read_text(encoding="utf-8"))
    assert store_links.load_mappings()["big"] == mapping
    assert list(mapping) == ["peas"]
    assert mapping["peas"]["confidence"] == "high"
    assert [a["url"] for a in mapping["peas"]["alternatives"]] == ["https://big"]
