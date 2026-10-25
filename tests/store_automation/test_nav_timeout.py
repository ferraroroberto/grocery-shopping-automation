"""A product-page navigation timeout in the cart run (issue #326).

A timed-out ``page.goto`` is "the site is slow", not "something broke": one
warning line, no traceback, its own status, retried at the end of the pass. A
run of consecutive timeouts gets one backoff and page recovery, then the pass
resumes or ends early. Everything runs against a fake page and fake store: no
Chrome, no store, no cart.
"""

import logging

import pytest
from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

from automation import browser, product_search, run_automation
from automation.browser import goto_with_login_check
from automation.models import CartItem
from automation.report import ADDED_VERIFIED, ERROR, miss_line

HOME = "https://shop.test/home"
URL = "https://shop.test/p/{}"
TIMED_OUT = "timed_out"


class FakePage:
    """A page whose ``goto`` follows a per-URL script of ``"ok"`` / ``"timeout"``.

    An exhausted (or absent) script means ``"ok"``; ``about:blank`` always loads.
    """

    def __init__(self, script: dict[str, list[str]] | None = None) -> None:
        self.script = {url: list(seq) for url, seq in (script or {}).items()}
        self.gotos: list[str] = []
        self.url = ""

    def goto(self, url: str, timeout: int = 30000, wait_until: str = "load") -> None:
        self.gotos.append(url)
        seq = self.script.get(url)
        if url != "about:blank" and seq and seq.pop(0) == "timeout":
            raise PlaywrightTimeoutError(f"Page.goto: Timeout {timeout}ms exceeded.\nCall log: ...")
        self.url = url


class FakeStore:
    """A store adapter that navigates for real (through ``goto_with_login_check``)."""

    def __init__(self, cart: dict) -> None:
        self.cart = cart
        self.calls: list[str] = []
        self.boom: dict[str, Exception] = {}

    def add_to_cart(self, page, item: CartItem) -> None:
        self.calls.append(item.comida)
        goto_with_login_check(page, "fake", item.buscador)
        if item.comida in self.boom:
            raise self.boom[item.comida]

    @staticmethod
    def cart_key(url: str) -> str:
        return url.rsplit("/", 1)[-1]

    def read_cart_lines(self, page) -> dict:
        return self.cart

    def read_cart_total(self, page) -> int:
        goto_with_login_check(page, "fake", HOME)
        return 0


class _Closable:
    def close(self) -> None:
        pass

    def stop(self) -> None:
        pass


def _item(name: str) -> CartItem:
    return CartItem("fake", name, 1, URL.format(name))


@pytest.fixture()
def run(monkeypatch):
    """Run ``main`` over a fake store and page → ``(report, store, page, sleeps)``."""

    def _run(names, script, cart, *, boom=None):
        store = FakeStore(cart)
        store.boom = boom or {}
        page = FakePage({URL.format(n): seq for n, seq in script.items()} | {HOME: script.get("HOME", [])})
        sleeps: list[float] = []
        written = []
        monkeypatch.setitem(run_automation.HANDLERS, "fake", store)
        monkeypatch.setitem(product_search.SEARCHERS, "fake", lambda page, q, limit: [])
        monkeypatch.setattr(run_automation, "read_cart_items", lambda _store: [_item(n) for n in names])
        monkeypatch.setattr(run_automation, "launch_context", lambda **kw: (_Closable(), _Closable(), page))
        monkeypatch.setattr(run_automation, "human_delay", lambda *a, **k: None)
        monkeypatch.setattr(run_automation.time, "sleep", sleeps.append)
        monkeypatch.setattr(run_automation, "build_notify_notifier", lambda: None)
        monkeypatch.setattr(run_automation, "_write_purchase_log_if_live",
                            lambda report, dry, text="": written.append(report) or [])
        run_automation.main([])
        return written[0], store, page, sleeps

    return _run


def _by_name(report):
    return {o.item.comida: o for o in report.outcomes}


def test_goto_translates_a_playwright_timeout_into_a_typed_store_error():
    page = FakePage({URL.format("kiwi"): ["timeout"]})
    with pytest.raises(browser.NavigationTimeoutError) as caught:
        goto_with_login_check(page, "fake", URL.format("kiwi"))
    assert isinstance(caught.value, browser.StoreAccessError)
    assert URL.format("kiwi") in str(caught.value)


def test_one_timeout_is_a_warning_without_a_traceback_and_is_retried(run, caplog):
    caplog.set_level(logging.INFO)
    report, store, _page, sleeps = run(
        ["kiwi", "pan"], {"kiwi": ["timeout", "ok"]}, {"kiwi": 1, "pan": 1},
    )
    assert store.calls == ["kiwi", "pan", "kiwi"]  # retried after the pass
    assert _by_name(report)["kiwi"].status == ADDED_VERIFIED
    assert sleeps == []  # one timeout is no streak: no backoff
    hits = [r for r in caplog.records if "kiwi" in r.getMessage() and r.levelno >= logging.WARNING]
    assert [r.levelno for r in hits] == [logging.WARNING]
    assert hits[0].exc_info is None
    assert "unexpected" not in caplog.text


def test_an_item_that_times_out_twice_reads_timed_out_not_error(run):
    report, _store, _page, _sleeps = run(
        ["kiwi", "pan"], {"kiwi": ["timeout", "timeout"]}, {"pan": 1},
    )
    kiwi = _by_name(report)["kiwi"]
    assert kiwi.status == TIMED_OUT and kiwi.status != ERROR
    assert kiwi.retries == 1
    assert "timed out (site slow)" in miss_line(kiwi)


def test_a_streak_backs_off_recovers_the_page_then_resumes(run):
    names = ["a", "b", "c", "d", "e"]
    report, store, page, sleeps = run(
        names, {n: ["timeout"] for n in "abc"}, {n: 1 for n in names},
    )
    assert len(sleeps) == 1  # one backoff for the streak
    assert page.gotos.count("about:blank") == 1  # the hung navigation was dropped
    assert store.calls[:5] == names  # the pass resumed after recovery
    out = _by_name(report)
    assert all(out[n].status == ADDED_VERIFIED for n in names)  # a, b, c succeed on the retry


def test_a_streak_the_store_does_not_recover_from_ends_the_pass_early(run, caplog):
    caplog.set_level(logging.INFO)
    names = ["a", "b", "c", "d", "e", "f"]
    report, store, _page, sleeps = run(
        names,
        {**{n: ["timeout"] * 3 for n in "abc"}, "HOME": ["ok", "timeout"]},  # before-snapshot ok, probe times out
        {},
    )
    assert len(sleeps) == 1
    assert store.calls == ["a", "b", "c"]  # d, e, f never burn a timeout; no retry pass either
    out = _by_name(report)
    for n in names:
        assert out[n].status == TIMED_OUT
    assert "not tried" in out["f"].message
    ended = [r for r in caplog.records if "ending" in r.getMessage()]
    assert len(ended) == 1 and ended[0].levelno == logging.ERROR and ended[0].exc_info is None


def test_a_genuinely_unexpected_error_still_logs_a_traceback(run, caplog):
    report, _store, _page, _sleeps = run(
        ["kiwi"], {}, {}, boom={"kiwi": RuntimeError("selector exploded")},
    )
    assert _by_name(report)["kiwi"].status == ERROR
    unexpected = [r for r in caplog.records if "unexpected error" in r.getMessage()]
    assert unexpected and all(r.exc_info for r in unexpected)
