"""Unit tests for the Carrefour cart handler's pure helpers (issue #150)."""

import pytest

from automation import carrefour
from automation.browser import (
    BotChallengeError,
    NotLoggedInError,
    SessionExpiredError,
    StoreAccessError,
    StoreApiError,
)
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
    assert lines["VC4AECOMM-081271"] == {"sku": "0812710000", "units": 3, "name": "Pollo", "cuts": []}
    assert lines["589702176"]["units"] == 1


def test_cart_units_empty_cart():
    assert carrefour.cart_units({"food": {"items": []}}) == {}
    assert carrefour.cart_units({}) == {}


def _stub_fetch(monkeypatch, status, body):
    monkeypatch.setattr(carrefour, "_fetch_json", lambda page, url, method="GET": (status, body))


# A Cloudflare interstitial in place of the API's JSON (body is raw HTML text).
_CHALLENGE_HTML = "<!DOCTYPE html><html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"


def test_require_session_rejects_anonymous(monkeypatch):
    # Anonymous header: a user block with no email (verified live 2026-09-28).
    _stub_fetch(monkeypatch, 200, {"user": {"email": "", "addresses": []}})
    with pytest.raises(NotLoggedInError):
        carrefour._require_session(object())


def test_require_session_accepts_logged_in(monkeypatch):
    _stub_fetch(monkeypatch, 200, {"user": {"email": "x@example.com"}})
    carrefour._require_session(object())


def test_require_session_challenge_is_not_a_login_problem(monkeypatch):
    _stub_fetch(monkeypatch, 403, _CHALLENGE_HTML)
    with pytest.raises(BotChallengeError, match="403"):
        carrefour._require_session(object())


def test_require_session_unexpected_status_names_it(monkeypatch):
    _stub_fetch(monkeypatch, 500, {"type": "internal"})
    with pytest.raises(StoreApiError, match="500"):
        carrefour._require_session(object())


def test_require_session_changed_shape_is_an_api_error(monkeypatch):
    _stub_fetch(monkeypatch, 200, {"menu": ""})
    with pytest.raises(StoreApiError, match="user"):
        carrefour._require_session(object())


def test_read_cart_no_cart_404_is_an_empty_cart(monkeypatch):
    # A logged-in account with no cart (e.g. after an order) — verified live
    # 2026-10-06: the storefront's own page load gets this same 404 (#217).
    _stub_fetch(monkeypatch, 404, {"code": 404, "message": "no_cart", "type": "no_cart"})
    assert carrefour._read_cart(object()) == {}


def test_read_cart_other_404_is_an_api_error(monkeypatch):
    _stub_fetch(monkeypatch, 404, None)
    with pytest.raises(StoreApiError, match="404"):
        carrefour._read_cart(object())


def test_read_cart_401_is_not_logged_in(monkeypatch):
    _stub_fetch(monkeypatch, 401, {"type": "unauthorized"})
    with pytest.raises(NotLoggedInError, match="401"):
        carrefour._read_cart(object())


def test_read_cart_challenge_is_a_bot_challenge(monkeypatch):
    _stub_fetch(monkeypatch, 403, _CHALLENGE_HTML)
    with pytest.raises(BotChallengeError):
        carrefour._read_cart(object())


def test_read_cart_returns_lines(monkeypatch):
    _stub_fetch(monkeypatch, 200, {"food": {"items": [{"products": [
        {"product_id": "589702176", "sku_id": "5897021760", "units": 2, "name": "Guisantes"},
    ]}]}})
    assert carrefour._read_cart(object())["589702176"]["units"] == 2


def test_only_the_redirect_error_claims_a_redirect():
    errors = [
        NotLoggedInError("carrefour", "header API returned 200 with an empty user.email"),
        BotChallengeError("carrefour", "cart API returned 403 with an HTML page"),
        StoreApiError("carrefour", "cart API returned an unexpected 404"),
    ]
    messages = {str(err) for err in errors}
    assert len(messages) == 3
    assert not any("redirected" in m for m in messages)
    assert "redirected" in str(SessionExpiredError("carrefour", "https://www.carrefour.es/access"))
    assert all(isinstance(err, StoreAccessError) for err in [*errors, SessionExpiredError("carrefour")])


def test_check_login_is_header_only(monkeypatch):
    calls = []
    monkeypatch.setattr(carrefour, "goto_with_login_check", lambda page, store, url: calls.append(("goto", url)))
    monkeypatch.setattr(carrefour, "human_delay", lambda *a: None)
    monkeypatch.setattr(carrefour, "_fetch_json", lambda page, url, method="GET": (calls.append((method, url)) or (200, {"user": {"email": "x@example.com"}})))
    carrefour.check_login(object())
    assert calls == [("goto", carrefour.HOME_URL), ("GET", carrefour._HEADER_API)]


def test_carrefour_is_a_registered_handler():
    assert HANDLERS["carrefour"] is carrefour


def test_login_redirect_error_names_the_url():
    from automation import browser

    class _Page:
        url = "https://www.carrefour.es/access?back=/supermercado"

        def goto(self, url, timeout, wait_until):
            pass

    with pytest.raises(SessionExpiredError, match="/access"):
        browser.goto_with_login_check(_Page(), "carrefour", "https://www.carrefour.es/supermercado")
