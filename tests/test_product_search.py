"""Unit tests for the store search engine (issue #87) — no live-site calls.

Each store's search is exercised with a fake Playwright ``page`` whose
``request.get`` returns a recorded-shape JSON payload, so the hit-parsing,
URL construction and per-store error isolation are all covered offline.
"""

import pytest

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


@pytest.fixture(autouse=True)
def _no_navigation(monkeypatch):
    """Neutralise the real browser navigation / login check + settle wait."""
    monkeypatch.setattr(product_search, "goto_with_login_check", lambda *a, **k: None)
    monkeypatch.setattr(product_search, "_AMETLLER_SEARCH_SETTLE_S", 0)


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
    hit = {
        "productId": "14200", "productName": "Alvocat caixa 1kg",
        "price": 4.99, "image": {"disBaseLink": "http://img/a.jpg"},
    }
    page = FakePage(FakeResp({"hits": [hit], "total": 24}))
    out = product_search.search_ametller(page, "aguacate", 8)
    assert len(out) == 1
    c = out[0]
    assert c.store == "ametller"
    assert c.product_url == "https://www.ametllerorigen.com/es/alvocat-caixa-1kg/14200.html"
    assert c.price_text == "4,99 €"
    # the SCAPI call carries the bearer token
    assert page.request.calls[0]["headers"]["Authorization"] == "Bearer tok"


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
