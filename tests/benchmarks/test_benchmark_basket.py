"""Unit tests for the benchmark basket builder (issue #145) — no network."""

from __future__ import annotations

from datetime import date

import pytest

from benchmark import build_basket as bb


def test_months_spanned_adds_one_median_gap():
    dates = [date(2026, 7, 1), date(2026, 7, 8), date(2026, 7, 15), date(2026, 7, 29)]
    # first→last 28 days + median gap 7 days = 35 days
    assert bb.months_spanned(dates) == pytest.approx(35 / 30.44)


def test_months_spanned_single_date_is_one_week():
    assert bb.months_spanned([date(2026, 7, 1)]) == pytest.approx(7 / 30.44)


def test_monthly_quantities_sum_across_logs_and_stores():
    logs = [
        {"date": "2026-07-01", "store": "mercadona", "items": [{"comida": "Pan Blanco", "comprar": 4}]},
        {"date": "2026-07-08", "store": "mercadona", "items": [{"comida": "pan blanco", "comprar": 2}]},
        {"date": "2026-07-08", "store": "ametller", "items": [{"comida": "Jamón cocido", "comprar": 3}]},
    ]
    qty, meta = bb.monthly_quantities(logs)
    months = bb.months_spanned([date(2026, 7, 1), date(2026, 7, 8)])  # 14 days
    assert qty["pan-blanco"] == pytest.approx(6 / months, abs=1e-3)
    assert qty["jamon-cocido"] == pytest.approx(3 / months, abs=1e-3)
    assert meta["orders_per_month"]["mercadona"] == pytest.approx(2 / months, abs=0.01)


def test_item_key_is_accent_and_case_insensitive():
    assert bb.item_key("Leche avena niños 0%") == bb.item_key("leche AVENA ninos 0%") == "leche-avena-ninos-0"


@pytest.mark.parametrize(
    ("comida", "category", "brand", "tier"),
    [
        ("mozzarella galbani", "", "GALBANI", "A"),
        ("detergente", "droguería", "Bosque Verde", "D"),
        ("fairy platos", "droguería", "Fairy", "A"),       # a real brand beats commodity
        ("jamon cocido lonchas", "", "AMETLLER ORIGEN", "B"),
        ("pollo", "carne y pescado", "", "B"),
        ("crema cacahuete", "conservas", "Hacendado", "C"),
    ],
)
def test_suggest_tier(comida, category, brand, tier):
    assert bb.suggest_tier(comida, category, brand) == tier


def test_parse_mercadona_recovers_missing_pack_size_from_reference_price():
    raw = {"id": "24386", "brand": "Hacendado", "price_instructions": {
        "unit_price": "11.95", "unit_size": None, "size_format": "kg", "reference_price": "14.938",
    }}
    prod = bb.parse_mercadona_product(raw)
    assert prod["pack_size"] == pytest.approx(0.8, abs=1e-3)
    assert prod["unit"] == "kg"


def test_parse_ametller_product():
    raw = {"id": "45832", "name": "Pechuga de pavo 200g", "brand": "AMETLLER ORIGEN", "price": 4.99,
           "unitQuantity": 0.2, "unitMeasure": "KG", "pricePerUnit": 24.95,
           "c_ao_ingredientes": "Pechuga de pavo (93%)", "slugUrl": "https://x/45832.html"}
    prod = bb.parse_ametller_product(raw)
    assert (prod["pack_size"], prod["unit"], prod["pack_price"]) == (0.2, "kg", 4.99)
    assert prod["ingredients"].startswith("Pechuga de pavo")


def test_parse_mercadona_count_pack_is_priced_per_piece():
    raw = {"id": "22985", "brand": "Bosque Verde", "price_instructions": {
        "unit_price": "1.35", "unit_size": 1.0, "size_format": "ud", "total_units": 40, "unit_name": "bolsas",
    }}
    prod = bb.parse_mercadona_product(raw)
    assert (prod["pack_size"], prod["unit"], prod["unit_name"]) == (40, "ud", "bolsas")
