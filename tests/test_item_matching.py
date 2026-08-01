"""Unit tests for automation.item_matching.

Uses the real (redacted) order-confirmation fixture and a real purchase-log
snapshot (issue #72) so the alias/exact/fuzzy paths are exercised against
authentic data, including the one item that order genuinely dropped.
"""

from __future__ import annotations

from pathlib import Path

from automation.email_parsers.ametller import parse_confirmed_items
from automation.item_matching import (
    load_alias_table,
    load_latest_purchase_log,
    match_items,
    normalize,
)
from automation.models import CartItem

FIXTURES = Path(__file__).parent / "fixtures"
EMAIL_FIXTURE = FIXTURES / "ametller_order_prepared.txt"
PURCHASE_LOGS_DIR = FIXTURES / "sample_purchase_logs"
ALIASES_PATH = Path(__file__).parent.parent / "config" / "item_name_aliases.json"


def test_normalize_strips_accents_case_and_punctuation():
    assert normalize("Calabacín, EXTRA!!") == "calabacin extra"


def test_normalize_collapses_whitespace():
    assert normalize("  Yogurt   griego  ") == "yogurt griego"


def test_load_alias_table_real_file():
    aliases = load_alias_table("ametller", ALIASES_PATH)
    assert len(aliases) == 19
    assert aliases[normalize("Calabacín extra")] == "calabacin"


def test_load_alias_table_missing_store_is_empty():
    aliases = load_alias_table("nonexistent_store", ALIASES_PATH)
    assert aliases == {}


def test_load_latest_purchase_log():
    catalog = load_latest_purchase_log("ametller", PURCHASE_LOGS_DIR)
    assert len(catalog) == 20
    assert {item.comida for item in catalog} >= {"fresas", "salmon", "pollo"}


def test_load_latest_purchase_log_missing_store():
    assert load_latest_purchase_log("mercadona_nope", PURCHASE_LOGS_DIR) == []


def test_match_real_order_matches_everything_but_the_dropped_item():
    body = EMAIL_FIXTURE.read_text(encoding="utf-8")
    website_names = parse_confirmed_items(body)
    catalog = load_latest_purchase_log("ametller", PURCHASE_LOGS_DIR)
    aliases = load_alias_table("ametller", ALIASES_PATH)

    result = match_items(website_names, catalog, aliases=aliases)

    assert len(result.matched) == 19
    assert all(m.method == "alias" and m.confidence == 1.0 for m in result.matched)
    assert result.unmatched_website_names == []
    assert result.dropped_comida == ["fresas"]


def test_match_items_exact_normalized_without_alias():
    catalog = [CartItem("ametller", "calabacin", 1, "")]
    result = match_items(["Calabacin"], catalog, aliases={})
    assert len(result.matched) == 1
    assert result.matched[0].method == "exact"
    assert result.matched[0].comida == "calabacin"
    assert result.dropped_comida == []


def test_match_items_fuzzy_fallback_above_threshold():
    aliases = {normalize("Calabacín extra"): "calabacin"}
    catalog = [CartItem("ametller", "calabacin", 1, "")]
    # Not identical after normalization ("calabacins extra" vs "calabacin extra")
    # but close enough to clear the 0.9 fuzzy threshold.
    result = match_items(["Calabacins extra"], catalog, aliases=aliases)
    assert len(result.matched) == 1
    assert result.matched[0].method == "fuzzy"
    assert result.matched[0].comida == "calabacin"
    assert result.matched[0].confidence >= 0.9


def test_match_items_containment_of_the_three_reported_pairs():
    """Issue #116's real Telegram mismatch, with no alias entries at all."""
    catalog = [
        CartItem("ametller", "tomate cherry", 1, ""),
        CartItem("ametller", "pulpo cocido", 1, ""),
        CartItem("ametller", "cebolla", 1, ""),
    ]
    website_names = [
        "Tomate Cherry bandeja Essencials 500g",
        "Pulpo cocido Gilmar 300g",
        "Cebolla cocina",
    ]

    result = match_items(website_names, catalog, aliases={})

    assert [m.comida for m in result.matched] == ["tomate cherry", "pulpo cocido", "cebolla"]
    assert all(m.method == "contains" for m in result.matched)
    assert all(0.0 < m.confidence <= 1.0 for m in result.matched)
    assert result.unmatched_website_names == []
    assert result.dropped_comida == []


def test_match_items_containment_prefers_the_most_specific_comida():
    catalog = [
        CartItem("ametller", "tomate", 1, ""),
        CartItem("ametller", "tomate cherry", 1, ""),
    ]
    result = match_items(["Tomate Cherry bandeja Essencials 500g"], catalog, aliases={})
    assert len(result.matched) == 1
    assert result.matched[0].comida == "tomate cherry"
    assert result.dropped_comida == ["tomate"]


def test_match_items_containment_also_matches_the_longer_comida_direction():
    catalog = [CartItem("ametller", "pulpo cocido gallega", 1, "")]
    result = match_items(["Pulpo cocido"], catalog, aliases={})
    assert len(result.matched) == 1
    assert result.matched[0].method == "contains"
    assert result.matched[0].comida == "pulpo cocido gallega"


def test_match_items_containment_requires_whole_tokens():
    """A partial-word overlap is not containment — "pan" must not eat "Pantalones"."""
    catalog = [CartItem("ametller", "panecillo", 1, "")]
    result = match_items(["Panecillos integrales 6uds"], catalog, aliases={})
    assert result.matched == []
    assert result.unmatched_website_names == ["Panecillos integrales 6uds"]
    assert result.dropped_comida == ["panecillo"]


def test_match_items_containment_skips_names_below_the_length_floor():
    """A 3-char generic comida must not swallow an unrelated longer line."""
    catalog = [CartItem("ametller", "ajo", 1, "")]
    result = match_items(["Ajo negro fermentado Essencials 100g"], catalog, aliases={})
    assert result.matched == []
    assert result.unmatched_website_names == ["Ajo negro fermentado Essencials 100g"]
    assert result.dropped_comida == ["ajo"]


def test_match_items_containment_does_not_shadow_the_fuzzy_tier():
    """Issue #72's already-aliased near-duplicate still reports as fuzzy."""
    aliases = {normalize("Calabacín extra"): "calabacin"}
    catalog = [CartItem("ametller", "calabacin", 1, "")]
    result = match_items(["Calabacins extra"], catalog, aliases=aliases)
    assert result.matched[0].method == "fuzzy"


def test_match_items_unrelated_name_is_unmatched_and_comida_reported_dropped():
    aliases = {normalize("Calabacín extra"): "calabacin"}
    catalog = [CartItem("ametller", "calabacin", 1, "")]
    result = match_items(["Chocolate negro Lindt 70% 100g"], catalog, aliases=aliases)
    assert result.matched == []
    assert result.unmatched_website_names == ["Chocolate negro Lindt 70% 100g"]
    assert result.dropped_comida == ["calabacin"]
