"""The shared Chrome profile wait-with-backoff, now used by the cart run too (#226).

Everything is faked: no Chrome is launched, no store is touched, no cart changes.
"""

import pytest

from automation import browser, run_automation
from automation.browser import ProfileBusyError
from automation.models import CartItem


class _FakePlaywright:
    def stop(self) -> None:
        pass


@pytest.fixture()
def no_chrome(monkeypatch):
    """launch_context minus Chrome: profile 'initialized', sleeps recorded, launch always fails."""
    sleeps: list[int] = []
    calls = {"launches": 0}

    class _Starter:
        def start(self):
            return _FakePlaywright()

    def failing_open(playwright, **kwargs):
        calls["launches"] += 1
        raise RuntimeError("profile locked")

    monkeypatch.setattr(browser, "_profile_initialized", lambda _dir: True)
    monkeypatch.setattr(browser, "sync_playwright", lambda: _Starter())
    monkeypatch.setattr(browser, "_open_context", failing_open)
    monkeypatch.setattr(browser.time, "sleep", sleeps.append)
    return sleeps, calls


def test_wait_for_profile_ends_in_a_precise_busy_error(no_chrome):
    sleeps, calls = no_chrome
    waits = []
    with pytest.raises(ProfileBusyError, match="held by another job") as info:
        browser.launch_context(wait_for_profile=True, on_wait=lambda *a: waits.append(a))
    assert sleeps == list(browser._PROFILE_WAIT_BACKOFF_S)
    assert calls["launches"] == len(browser._PROFILE_WAIT_BACKOFF_S) + 1
    assert len(waits) == len(browser._PROFILE_WAIT_BACKOFF_S)
    assert "profile locked" in str(info.value)  # the underlying launch error is kept
    assert isinstance(info.value.__cause__, RuntimeError)


def test_without_wait_for_profile_the_launch_error_is_raised_as_is(no_chrome):
    sleeps, calls = no_chrome
    with pytest.raises(RuntimeError, match="profile locked") as info:
        browser.launch_context()
    assert not isinstance(info.value, ProfileBusyError)
    assert sleeps == [] and calls["launches"] == 1


def _item(store: str, name: str) -> CartItem:
    return CartItem(store, name, 1, f"https://example.test/{name}")


def test_cart_run_waits_for_the_profile_and_reports_a_busy_store(monkeypatch):
    launches = []

    def busy_launch(**kwargs):
        launches.append(kwargs)
        raise ProfileBusyError("held by another job")

    monkeypatch.setattr(run_automation, "read_cart_items", lambda store: [_item("mercadona", "leche"), _item("ametller", "pan")])
    monkeypatch.setattr(run_automation, "launch_context", busy_launch)
    written = []
    monkeypatch.setattr(run_automation, "_write_purchase_log_if_live", lambda report, dry: written.append(report) or [])

    assert run_automation.main([]) == 1  # errors → non-zero, but the run still finishes
    assert [kw["wait_for_profile"] for kw in launches] == [True, True]  # both stores waited, none crashed the run
    report = written[0]
    assert {item.comida for item, _msg in report.errors} == {"leche", "pan"}
    assert all("held by another job" in msg for _item, msg in report.errors)
    assert report.added == []
