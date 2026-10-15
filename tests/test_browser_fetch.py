"""open_actions_context and fetch() share one stealth-launched context (issue #296).

Everything is faked: no Chrome is launched, no store is touched.
"""

import pytest

from benchmark import browser_fetch


class _FakeResponse:
    def __init__(self, status: int) -> None:
        self.status = status


class _FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[str] = []
        self.evaluated: str | None = None

    def goto(self, url, **kwargs):
        self.goto_calls.append(url)
        return _FakeResponse(200)

    def evaluate(self, script, url):
        self.evaluated = url
        return {"status": 200, "body": "api-body"}

    def inner_text(self, selector):
        return "page text"

    def content(self):
        return "<html></html>"


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self._page = page
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _FakePlaywrightCM:
    def __enter__(self):
        return object()

    def __exit__(self, *exc):
        return False


@pytest.fixture()
def stub_launch(monkeypatch, tmp_path):
    """Stand in for sync_playwright()/_open_context()/state_dir() — no real browser."""
    page = _FakePage()
    context = _FakeContext(page)
    opened = {}

    def fake_open_context(pw, *, headless, user_data_dir):
        opened["headless"] = headless
        opened["user_data_dir"] = user_data_dir
        return context, page

    monkeypatch.setattr(browser_fetch, "sync_playwright", lambda: _FakePlaywrightCM())
    monkeypatch.setattr(browser_fetch, "_open_context", fake_open_context)
    monkeypatch.setattr(browser_fetch, "state_dir", lambda: tmp_path)
    return context, page, opened


def test_open_actions_context_yields_the_shared_launch_and_closes_on_exit(stub_launch):
    context, page, opened = stub_launch
    with browser_fetch.open_actions_context("carrefour") as (ctx, pg):
        assert ctx is context
        assert pg is page
        assert not context.closed
    assert context.closed
    assert opened["user_data_dir"].name == "carrefour"  # per-store profile, not the shared login one


def test_fetch_text_mode_uses_open_actions_context(stub_launch):
    context, page, _opened = stub_launch
    status, body = browser_fetch.fetch("dia", "https://example.test/search", settle_s=0)
    assert status == 200
    assert body == "page text"
    assert page.goto_calls == ["https://example.test/search"]
    assert context.closed  # closed exactly once, by the shared context manager


def test_fetch_api_mode_evaluates_fetch_in_page(stub_launch):
    _context, page, _opened = stub_launch
    status, body = browser_fetch.fetch(
        "carrefour", "https://example.test/api?q=x", mode="api", settle_s=0,
    )
    assert status == 200
    assert body == "api-body"
    assert page.evaluated == "https://example.test/api?q=x"
