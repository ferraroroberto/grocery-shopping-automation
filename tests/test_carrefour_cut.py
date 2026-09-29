"""Carrefour cut preference (issue #176): config lookup, cart-line cuts, and
the handler's refuse-rather-than-mix rules — with the browser stubbed out."""

import json

import pytest

from automation import carrefour, product_options
from automation.errors import AddToCartFailed
from automation.models import CartItem

DORADA_URL = "https://www.carrefour.es/supermercado/dorada-de-racion-carrefour-600-g-aprox/R-628108203/p"
DORADA_ID = "628108203"
ITEM = CartItem("carrefour", "dorada", 2, DORADA_URL)


# --- config/product_options.json ------------------------------------------

def test_shipped_config_selects_entero_limpio_for_the_dorada():
    assert product_options.preferred_cut("carrefour", DORADA_ID) == "Entero limpio"


def test_absent_options_file_means_no_preference(tmp_path):
    missing = tmp_path / "nope.json"
    assert product_options.load_product_options(missing) == {}
    assert product_options.preferred_cut("carrefour", DORADA_ID, path=missing) is None


def test_preferred_cut_lookup(tmp_path):
    path = tmp_path / "opts.json"
    path.write_text(json.dumps({"Carrefour": {DORADA_ID: {"cut": " Entero limpio "}, "1": {"cut": ""}, "2": {}}}), encoding="utf-8")
    assert product_options.preferred_cut("carrefour", DORADA_ID, path=path) == "Entero limpio"
    assert product_options.preferred_cut("carrefour", "1", path=path) is None
    assert product_options.preferred_cut("carrefour", "2", path=path) is None
    assert product_options.preferred_cut("carrefour", "999", path=path) is None
    assert product_options.preferred_cut("mercadona", DORADA_ID, path=path) is None
    assert product_options.preferred_cut("carrefour", "", path=path) is None


@pytest.mark.parametrize("text", ["{not json", '["a list"]', '{"carrefour": ["x"]}', '{"carrefour": {"1": "Entero"}}'])
def test_broken_options_file_stops_the_run(tmp_path, text):
    path = tmp_path / "opts.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        product_options.load_product_options(path)


def test_options_for_url(tmp_path):
    path = tmp_path / "opts.json"
    path.write_text(json.dumps({"carrefour": {DORADA_ID: {"cut": "Entero limpio", "note": "cleaned"}, "1": {"cut": "Rodajas"}}}), encoding="utf-8")
    assert product_options.options_for_url("carrefour", DORADA_URL, path=path) == {"cut": "Entero limpio", "note": "cleaned"}
    assert product_options.options_for_url("Carrefour", "https://www.carrefour.es/supermercado/x/R-1/p", path=path) == {"cut": "Rodajas"}
    assert product_options.options_for_url("carrefour", "https://www.carrefour.es/supermercado/x/R-2/p", path=path) == {}
    assert product_options.options_for_url("carrefour", "https://www.carrefour.es/supermercado", path=path) == {}
    assert product_options.options_for_url("carrefour", "", path=path) == {}
    # A store with no product-id parser has no options.
    assert product_options.options_for_url("mercadona", "https://tienda.mercadona.es/product/1/x", path=path) == {}


def test_shipped_config_shows_the_dorada_cut():
    assert product_options.options_for_url("carrefour", DORADA_URL)["cut"] == "Entero limpio"


# --- cart-line cuts ----------------------------------------------------------

def test_cart_units_collects_the_cut_type():
    # Line shape observed live on 2026-09-28 (trimmed).
    cart = {"food": {"items": [{"products": [
        {"product_id": DORADA_ID, "sku_id": "0333890000", "units": 1, "name": "Dorada", "cut_type": "Entero limpio", "sale_type": "cut"},
        {"product_id": DORADA_ID, "sku_id": "0333890000", "units": 1, "name": "Dorada", "cut_type": "Entero limpio"},
    ]}]}}
    assert carrefour.cart_units(cart)[DORADA_ID] == {"sku": "0333890000", "units": 2, "name": "Dorada", "cuts": ["Entero limpio"]}


@pytest.mark.parametrize(
    ("line", "expected"),
    [
        ({}, []),
        (None, []),
        ({"units": 0, "cuts": []}, []),
        ({"units": 2, "cuts": ["Entero limpio"]}, []),
        ({"units": 1, "cuts": ["Entero"]}, ["Entero"]),
        ({"units": 2, "cuts": ["Entero limpio", "Rodajas"]}, ["Rodajas"]),
        ({"units": 1, "cuts": []}, ["(no cut)"]),
    ],
)
def test_wrong_cuts(line, expected):
    assert carrefour.wrong_cuts(line, "Entero limpio") == expected


# --- add_to_cart with a preferred cut (browser stubbed) ------------------------

class _Page:
    url = DORADA_URL


@pytest.fixture
def handler(monkeypatch):
    """Stub navigation/session; the test drives the cart reads and clicks."""
    state = {"carts": [], "clicks": [], "selected": [], "deleted": []}
    monkeypatch.setattr(carrefour, "goto_with_login_check", lambda page, store, url: None)
    monkeypatch.setattr(carrefour, "human_delay", lambda *a: None)
    monkeypatch.setattr(carrefour.time, "sleep", lambda s: None)
    monkeypatch.setattr(carrefour, "_require_session", lambda page: None)
    monkeypatch.setattr(carrefour.product_options, "preferred_cut", lambda store, pid: "Entero limpio")

    def read_cart(page):
        return state["carts"].pop(0) if len(state["carts"]) > 1 else state["carts"][0]

    def click(page, selector):
        state["clicks"].append(selector)
        return True

    def fetch(page, url, method="GET"):
        state["deleted"].append((method, url))
        return 200, None

    monkeypatch.setattr(carrefour, "_read_cart", read_cart)
    monkeypatch.setattr(carrefour, "_click_once", click)
    monkeypatch.setattr(carrefour, "_select_cut", lambda page, item, cut: state["selected"].append(cut))
    monkeypatch.setattr(carrefour, "_fetch_json", fetch)
    return state


def _line(units, *cuts):
    return {DORADA_ID: {"sku": "0333890000", "units": units, "name": "Dorada", "cuts": list(cuts)}}


def test_selects_the_cut_before_the_first_add(handler):
    handler["carts"] = [{}, _line(1, "Entero limpio"), _line(2, "Entero limpio")]
    carrefour.add_to_cart(_Page(), ITEM)
    assert handler["selected"] == ["Entero limpio"]
    assert handler["clicks"] == [carrefour.SELECTORS["add_button"], carrefour.SELECTORS["increase"]]


def test_rerun_with_the_right_cut_adds_nothing(handler):
    handler["carts"] = [_line(2, "Entero limpio")]
    carrefour.add_to_cart(_Page(), ITEM)
    assert handler["clicks"] == [] and handler["selected"] == []


def test_cart_already_in_another_cut_is_refused_untouched(handler):
    handler["carts"] = [_line(1, "Entero")]
    with pytest.raises(AddToCartFailed, match=r"already holds 1 as Entero, not cut 'Entero limpio'"):
        carrefour.add_to_cart(_Page(), ITEM)
    assert handler["clicks"] == [] and handler["deleted"] == []


def test_add_that_lands_in_another_cut_is_rolled_back(handler):
    handler["carts"] = [{}, _line(1, "Entero")]
    with pytest.raises(AddToCartFailed, match=r"came back as Entero, not cut 'Entero limpio' — line removed"):
        carrefour.add_to_cart(_Page(), ITEM)
    assert handler["deleted"] == [("DELETE", carrefour._LINE_API.format(sku="0333890000"))]


def test_missing_cut_option_fails_before_any_click(monkeypatch):
    monkeypatch.setattr(carrefour, "_offered_cuts", lambda page: ["Entero", "Rodajas"])
    with pytest.raises(AddToCartFailed, match=r"cut 'Entero limpio' is not offered on the product page \(offers: Entero, Rodajas\) — not added"):
        carrefour._select_cut(object(), ITEM, "Entero limpio")


def test_no_cut_picker_at_all_is_named(monkeypatch):
    monkeypatch.setattr(carrefour, "_offered_cuts", lambda page: [])
    with pytest.raises(AddToCartFailed, match=r"offers: no cut picker"):
        carrefour._select_cut(object(), ITEM, "Entero limpio")
