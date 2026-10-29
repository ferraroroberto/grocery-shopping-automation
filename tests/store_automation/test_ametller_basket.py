"""Unit tests for the Ametller SCAPI basket read (issue #390).

A failed basket GET must never be reported as an empty cart — only a
confirmed 200 response counts.
"""

import pytest

from automation import ametller
from automation.browser import NotLoggedInError, StoreApiError

_AUTH = {"token": "tok", "customer_id": "cust-1", "customer_type": "registered"}


class FakeResponse:
    def __init__(self, status: int, body: object = None, *, raise_on_json: bool = False) -> None:
        self.status = status
        self.ok = 200 <= status < 300
        self._body = body
        self._raise_on_json = raise_on_json

    def json(self) -> object:
        if self._raise_on_json:
            raise ValueError("not json")
        return self._body


class FakeRequest:
    def __init__(self, response: FakeResponse | None = None, *, raise_error: Exception | None = None) -> None:
        self._response = response
        self._raise_error = raise_error

    def get(self, url, headers=None, timeout=None):
        if self._raise_error is not None:
            raise self._raise_error
        return self._response


class FakePage:
    def __init__(self, request: FakeRequest) -> None:
        self.request = request


def test_missing_auth_raises_not_logged_in():
    with pytest.raises(NotLoggedInError):
        ametller._basket_items(FakePage(FakeRequest()), {"token": "", "customer_id": ""})


def test_non_ok_status_raises_store_api_error_not_empty():
    page = FakePage(FakeRequest(FakeResponse(500)))
    with pytest.raises(StoreApiError, match="500"):
        ametller._basket_items(page, _AUTH)


def test_network_exception_raises_store_api_error_not_empty():
    page = FakePage(FakeRequest(raise_error=RuntimeError("timeout")))
    with pytest.raises(StoreApiError, match="timeout"):
        ametller._basket_items(page, _AUTH)


def test_unparseable_body_raises_store_api_error_not_empty():
    page = FakePage(FakeRequest(FakeResponse(200, raise_on_json=True)))
    with pytest.raises(StoreApiError):
        ametller._basket_items(page, _AUTH)


def test_confirmed_200_with_no_lines_is_a_real_empty_cart():
    page = FakePage(FakeRequest(FakeResponse(200, {"baskets": []})))
    assert ametller._basket_items(page, _AUTH) == []


def test_200_with_lines_returns_them():
    body = {
        "baskets": [
            {
                "basketId": "b1",
                "productItems": [
                    {"itemId": "i1", "productId": "p1", "quantity": 2},
                ],
            }
        ]
    }
    page = FakePage(FakeRequest(FakeResponse(200, body)))
    items = ametller._basket_items(page, _AUTH)
    assert items == [{"basketId": "b1", "itemId": "i1", "productId": "p1", "qty": 2}]
