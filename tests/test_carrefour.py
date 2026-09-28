"""Unit tests for the Carrefour cart handler's pure helpers (issue #150)."""

import pytest

from automation import carrefour
from automation.browser import SessionExpiredError
from automation.run_automation import HANDLERS


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://www.carrefour.es/supermercado/pechuga-de-pollo-fileteada-carrefour-1-kg-aprox/R-VC4AECOMM-081271/p",
            "VC4AECOMM-081271",
        ),
        # Some ids carry no prefix/dash.
        ("https://www.carrefour.es/supermercado/guisantes-muy-tiernos-carrefour-classic-300-g/R-589702176/p", "589702176"),
        ("https://www.carrefour.es/supermercado/x/R-531705261/p?ic_source=portal", "531705261"),
        # Malformed benchmark URL (no product id) and the redirect targets of a
        # stale product: the storefront home and a category page.
        ("https://www.carrefour.es/supermercado/copos-de-avena-integral-original-carrefour-500-g/p", ""),
        ("https://www.carrefour.es/", ""),
        ("https://www.carrefour.es/supermercado/frescos/carne/aves-y-pollo/cat190012/c", ""),
        ("", ""),
    ],
)
def test_product_id_from_url(url, expected):
    assert carrefour.product_id_from_url(url) == expected


def test_cart_units_sums_lines_per_product():
    cart = {
        "food": {
            "items": [
                {"category": "Frescos", "products": [
                    {"product_id": "VC4AECOMM-081271", "sku_id": "0812710000", "units": 2, "name": "Pollo"},
                ]},
                {"category": "Despensa", "products": [
                    {"product_id": "589702176", "sku_id": "5897021760", "units": 1, "name": "Guisantes"},
                    {"product_id": "VC4AECOMM-081271", "sku_id": "0812710000", "units": 1, "name": "Pollo"},
                ]},
            ]
        }
    }
    lines = carrefour.cart_units(cart)
    assert lines["VC4AECOMM-081271"] == {"sku": "0812710000", "units": 3, "name": "Pollo"}
    assert lines["589702176"]["units"] == 1


def test_cart_units_empty_cart():
    assert carrefour.cart_units({"food": {"items": []}}) == {}
    assert carrefour.cart_units({}) == {}


def test_require_session_rejects_anonymous(monkeypatch):
    # Anonymous header: a user block with no email (verified live 2026-09-28).
    monkeypatch.setattr(carrefour, "_fetch_json", lambda page, url, method="GET": (200, {"user": {"email": "", "addresses": []}}))
    with pytest.raises(SessionExpiredError):
        carrefour._require_session(object())


def test_require_session_accepts_logged_in(monkeypatch):
    monkeypatch.setattr(carrefour, "_fetch_json", lambda page, url, method="GET": (200, {"user": {"email": "x@example.com"}}))
    carrefour._require_session(object())


def test_read_cart_maps_404_to_session_expired(monkeypatch):
    monkeypatch.setattr(carrefour, "_fetch_json", lambda page, url, method="GET": (404, None))
    with pytest.raises(SessionExpiredError):
        carrefour._read_cart(object())


def test_carrefour_is_a_registered_handler():
    assert HANDLERS["carrefour"] is carrefour
