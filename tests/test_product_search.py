"""Unit tests for the store search engine (issue #87) — no live-site calls.

Each store's search is exercised with a fake Playwright ``page`` whose
``request.get`` returns a recorded-shape JSON payload, so the hit-parsing,
URL construction and per-store error isolation are all covered offline.
"""

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation import product_search
from automation.browser import SessionExpiredError


class FakeResp:
    def __init__(self, payload, ok=True, status=200):
        self._payload = payload
        self.ok = ok
        self.status = status

    def json(self):
        return self._payload


class FakeRequest:
    def __init__(self, resp):
        self._resp = resp
        self.calls = []

    def get(self, url, params=None, headers=None, timeout=None):
        self.calls.append({"url": url, "params": params, "headers": headers})
        return self._resp


class FakePage:
    def __init__(self, resp):
        self.request = FakeRequest(resp)


class FakeCarrefourLocator:
    """Stands in for ``page.locator(...)`` — ``.first`` is itself so both
    ``cards.first.wait_for(...)`` and ``cards.evaluate_all(...)`` work."""

    def __init__(self, raw_cards, *, no_results=False):
        self._raw_cards = raw_cards
        self._no_results = no_results
        self.first = self

    def wait_for(self, state=None, timeout=None):
        if self._no_results:
            raise PlaywrightTimeoutError("no cards rendered")

    def evaluate_all(self, _js):
        return self._raw_cards


class FakeCarrefourPage:
    def __init__(self, raw_cards, *, no_results=False):
        self._loc = FakeCarrefourLocator(raw_cards, no_results=no_results)

    def locator(self, _selector):
        return self._loc


@pytest.fixture(autouse=True)
def _no_navigation(monkeypatch):
    """Neutralise the real browser navigation / login check + settle waits."""
    monkeypatch.setattr(product_search, "goto_with_login_check", lambda *a, **k: None)
    monkeypatch.setattr(product_search, "_AMETLLER_SEARCH_SETTLE_S", 0)
    monkeypatch.setattr(product_search, "_CARREFOUR_SEARCH_SETTLE_S", 0)


def test_slugify():
    assert product_search._slugify("Alvocat caixa 1kg") == "alvocat-caixa-1kg"
    assert product_search._slugify("Síndria negra extra") == "sindria-negra-extra"


def test_fmt_price_spanish():
    assert product_search._fmt_price(5.15) == "5,15 €"
    assert product_search._fmt_price(None) == ""


def test_search_mercadona_parses_hits():
    hit = {
        "id": "3529", "slug": "sandia-baja-semillas-pieza",
        "display_name": "Sandía baja en semillas",
        "thumbnail": "http://img/x.jpg",
        "price_instructions": {"unit_price": "5.15"},
    }
    page = FakePage(FakeResp({"hits": [hit]}))
    out = product_search.search_mercadona(page, "sandia", 8)
    assert len(out) == 1
    c = out[0]
    assert c.store == "mercadona"
    assert c.product_url == "https://tienda.mercadona.es/product/3529/sandia-baja-semillas-pieza"
    assert c.price_text == "5,15 €"
    assert c.match == "strong"
    # search rides the session-scoped endpoint with the query term
    assert page.request.calls[0]["params"]["q"] == "sandia"


def test_search_mercadona_non_ok_raises():
    page = FakePage(FakeResp({}, ok=False, status=403))
    with pytest.raises(RuntimeError):
        product_search.search_mercadona(page, "sandia", 8)


def test_search_ametller_parses_hits(monkeypatch):
    monkeypatch.setattr(
        product_search.ametller, "_read_auth",
        lambda page: {"token": "tok", "customer_id": "c", "customer_type": "registered"},
    )
    # Spanish name: the SCAPI call asks for locale=es (issue #111).
    hit = {
        "productId": "14200", "productName": "Aguacate caja 1kg",
        "price": 4.99, "image": {"disBaseLink": "http://img/a.jpg"},
    }
    page = FakePage(FakeResp({"hits": [hit], "total": 24}))
    out = product_search.search_ametller(page, "aguacate", 8)
    assert len(out) == 1
    c = out[0]
    assert c.store == "ametller"
    assert c.product_url == "https://www.ametllerorigen.com/es/aguacate-caja-1kg/14200.html"
    assert c.price_text == "4,99 €"
    # the SCAPI call carries the bearer token
    assert page.request.calls[0]["headers"]["Authorization"] == "Bearer tok"


def test_search_ametller_requests_spanish_locale(monkeypatch):
    """SCAPI must be asked for ``locale=es`` — without it names come back in
    Catalan ("Alvocat" for aguacate). Only the plain form is accepted; the
    region-qualified ``es-ES`` / ``es_ES`` spellings 400. See issue #111."""
    monkeypatch.setattr(
        product_search.ametller, "_read_auth",
        lambda page: {"token": "t", "customer_id": "c", "customer_type": "registered"},
    )
    page = FakePage(FakeResp({"hits": [], "total": 0}))
    product_search.search_ametller(page, "aguacate", 8)
    assert page.request.calls[0]["params"]["locale"] == "es"


def test_search_ametller_no_match_returns_empty(monkeypatch):
    monkeypatch.setattr(
        product_search.ametller, "_read_auth",
        lambda page: {"token": "t", "customer_id": "c", "customer_type": "registered"},
    )
    page = FakePage(FakeResp({"hits": [], "total": 0}))
    assert product_search.search_ametller(page, "flurbos", 8) == []


def test_search_ametller_guest_session_raises(monkeypatch):
    monkeypatch.setattr(
        product_search.ametller, "_read_auth",
        lambda page: {"token": "", "customer_id": "", "customer_type": "guest"},
    )
    with pytest.raises(SessionExpiredError):
        product_search.search_ametller(FakePage(FakeResp({})), "sandia", 8)


def test_search_one_isolates_a_failing_store(monkeypatch):
    def good(page, q, limit):
        return [product_search.Candidate("mercadona", "X", "u", "1 €", 1.0, "", 0, 0.9, "strong")]

    def bad(page, q, limit):
        raise RuntimeError("boom")

    monkeypatch.setattr(product_search, "SEARCHERS", {"mercadona": good, "ametller": bad})
    res = product_search._search_one(object(), "x", 5)
    assert res["query"] == "x"
    assert len(res["candidates"]) == 1
    assert "ametller" in res["errors"]  # one store failing never sinks the other


# --- Carrefour (issue #157) --------------------------------------------------
# Raw card dicts below are trimmed from a live 2026-09-29 search for "avena"
# (see automation/product_search.py's _CARREFOUR_CARD_JS extraction shape) —
# no account/session data, just the product-facing card fields.

_CARD_PLAIN = {
    "href": "/supermercado/copos-de-avena-integral-original-carrefour-500-g/R-641402048/p",
    "name": "Copos de avena integral Original Carrefour 500 g.",
    "price_text": "0,84 €",
    "image": "https://static.carrefour.es/hd_350x_/img_pim_food/813631_00_1.jpg",
}

_CARD_ON_SALE = {
    "href": "/supermercado/copos-de-avena-integral-classic-carrefour-sin-gluten-500-g/R-VC4AECOMM-360916/p",
    "name": "Copos de avena integral Classic Carrefour sin gluten 500 g.",
    # A sale card's DOM carries the previous price too, but the JS extraction
    # only ever reads "result-current-price" — the struck-through one is never
    # in price_text.
    "price_text": "2,25 €",
    "image": "https://static.carrefour.es/hd_350x_/img_pim_food/360916_00_1.jpg",
}

# A sponsored banner tile that links to a category, not a product — no
# `/R-<id>/p` in its href, so it must be skipped rather than surfaced as a card.
_CARD_NO_PRODUCT_ID = {
    "href": "/supermercado/super-precio/8123372104/s",
    "name": "Ver todos los productos en oferta",
    "price_text": "",
    "image": "",
}


def test_parse_carrefour_price():
    assert product_search._parse_carrefour_price("0,84 €") == 0.84
    assert product_search._parse_carrefour_price("4,50 €/kg") == 4.50
    assert product_search._parse_carrefour_price("") is None
    assert product_search._parse_carrefour_price("VENDIDO EN PACK") is None


def test_parse_carrefour_card_plain():
    out = product_search._parse_carrefour_card(_CARD_PLAIN)
    assert out == {
        "name": "Copos de avena integral Original Carrefour 500 g",  # trailing "." trimmed
        "url": "https://www.carrefour.es/supermercado/copos-de-avena-integral-original-carrefour-500-g/R-641402048/p",
        "price": 0.84,
        "image": "https://static.carrefour.es/hd_350x_/img_pim_food/813631_00_1.jpg",
    }


def test_parse_carrefour_card_on_sale_reads_current_price():
    out = product_search._parse_carrefour_card(_CARD_ON_SALE)
    assert out["price"] == 2.25


def test_parse_carrefour_card_skips_non_product_card():
    assert product_search._parse_carrefour_card(_CARD_NO_PRODUCT_ID) is None


def test_search_carrefour_parses_cards():
    page = FakeCarrefourPage([_CARD_PLAIN, _CARD_ON_SALE, _CARD_NO_PRODUCT_ID])
    out = product_search.search_carrefour(page, "avena", 8)
    assert len(out) == 2  # the non-product banner tile is dropped
    c = out[0]
    assert c.store == "carrefour"
    assert c.name == "Copos de avena integral Original Carrefour 500 g"
    assert c.product_url.endswith("/R-641402048/p")
    assert c.price_text == "0,84 €"
    assert c.match == "strong"


def test_search_carrefour_respects_limit():
    page = FakeCarrefourPage([_CARD_PLAIN, _CARD_ON_SALE])
    out = product_search.search_carrefour(page, "avena", 1)
    assert len(out) == 1


def test_search_carrefour_no_results_returns_empty():
    page = FakeCarrefourPage([], no_results=True)
    assert product_search.search_carrefour(page, "flurbos", 8) == []


def test_carrefour_registered_in_searchers_and_labels():
    assert product_search.SEARCHERS["carrefour"] is product_search.search_carrefour
    assert product_search._STORE_LABEL["carrefour"] == "Carrefour"
