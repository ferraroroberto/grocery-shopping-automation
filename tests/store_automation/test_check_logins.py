"""The read-only store-login check (issue #217): per-store classification."""

from types import SimpleNamespace

import pytest

from automation import check_logins
from automation.browser import (
    BotChallengeError,
    NotLoggedInError,
    SessionExpiredError,
    StoreApiError,
)


def _handler(raises=None):
    def check_login(page):
        if raises is not None:
            raise raises
    return SimpleNamespace(check_login=check_login)


def test_logged_in():
    assert check_logins.check_store(_handler(), object()) == {"state": "logged_in", "detail": ""}


@pytest.mark.parametrize("err", [
    NotLoggedInError("carrefour", "header API returned 200 with an empty user.email"),
    SessionExpiredError("carrefour", "https://www.carrefour.es/access"),
])
def test_logged_out(err):
    result = check_logins.check_store(_handler(err), object())
    assert result == {"state": "logged_out", "detail": str(err)}


@pytest.mark.parametrize("err", [
    BotChallengeError("carrefour", "header API returned 403 with an HTML page"),
    StoreApiError("carrefour", "header API returned an unexpected 500"),
    TimeoutError("navigation timed out"),
])
def test_unknown_when_the_signal_cannot_be_read(err):
    result = check_logins.check_store(_handler(err), object())
    assert result["state"] == "unknown"
    assert str(err) in result["detail"]


def test_store_without_a_check_is_unknown():
    result = check_logins.check_store(SimpleNamespace(), object())
    assert result["state"] == "unknown"
    assert result["detail"]
