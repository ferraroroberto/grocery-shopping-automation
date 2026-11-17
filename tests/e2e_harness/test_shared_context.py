"""The e2e suite shares one browser context per distinct arguments and resets it between tests.

A context that outlives its test is only safe if what a test leaves in it is
undone, and if no test leaves what the reset cannot undo. Pinned here against
recording stand-ins: the reset's contents and order, reuse keyed by context
arguments, page-level arguments sharing a context, a failed reset discarding
the context, and a static check that no e2e file installs context-level state
or closes a context it leased. No browser is started.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

from tests.e2e import _shared_context as shared
from tests.e2e._shared_context import SharedContexts, context_key, reset_context

_E2E = Path(__file__).resolve().parents[1] / "e2e"
_ORIGIN = "http://127.0.0.1:1"


class _Page:
    def __init__(self, log: list[str], label: str) -> None:
        self._log, self._label = log, label

    def close(self) -> None:
        self._log.append(f"close {self._label}")

    def route(self, pattern: str, handler: Any) -> None:
        self._log.append(f"route {pattern}")

    def unroute_all(self, behavior: str) -> None:
        self._log.append("unroute page")

    def goto(self, url: str) -> None:
        self._log.append(f"goto {url}")

    def evaluate(self, script: str) -> None:
        self._log.append("clear origin state")

    def set_viewport_size(self, size: dict) -> None:
        self._log.append(f"viewport {size['width']}")

    def emulate_media(self, color_scheme: str) -> None:
        self._log.append(f"scheme {color_scheme}")

    def set_extra_http_headers(self, headers: dict) -> None:
        self._log.append("headers")


class _Context:
    def __init__(self, fail_on: str = "") -> None:
        self.log: list[str] = []
        self.closed = False
        self._fail_on = fail_on
        self.pages = [_Page(self.log, "page-1"), _Page(self.log, "page-2")]

    def _step(self, name: str) -> None:
        self.log.append(name)
        if name == self._fail_on:
            raise RuntimeError(name)

    def unroute_all(self, behavior: str) -> None:
        self._step("unroute_all")

    def clear_cookies(self) -> None:
        self._step("clear_cookies")

    def clear_permissions(self) -> None:
        self._step("clear_permissions")

    def new_page(self) -> _Page:
        return _Page(self.log, "new")

    def close(self) -> None:
        self.closed = True


class _Browser:
    def __init__(self) -> None:
        self.made: list[tuple[dict, _Context]] = []

    def new_context(self, **args: Any) -> _Context:
        context = _Context()
        self.made.append((args, context))
        return context


@pytest.fixture(autouse=True)
def _fresh_stats():
    before = dict(shared.STATS)
    for key in shared.STATS:
        shared.STATS[key] = 0
    yield
    shared.STATS.update(before)


def test_reset_empties_the_pages_routes_cookies_and_origin_storage_without_a_socket():
    context = _Context()
    reset_context(context, _ORIGIN)
    assert context.log[:5] == ["close page-1", "close page-2", "unroute_all", "clear_cookies", "clear_permissions"]
    # The origin is answered by a route, so clearing its storage never reaches the server.
    assert context.log[5:] == [f"route {_ORIGIN}/**", f"goto {_ORIGIN}/", "clear origin state", "unroute page", "close new"]


def test_a_context_is_reused_for_the_same_arguments_and_not_for_others():
    browser = _Browser()
    pool = SharedContexts(browser, _ORIGIN)
    first = pool.new_context(has_touch=True)
    assert pool.new_context(has_touch=True) is first
    assert pool.new_context() is not first
    assert len(browser.made) == 2
    assert (shared.STATS["created"], shared.STATS["reused"]) == (2, 1)


def test_pages_differing_only_in_page_level_arguments_share_one_context():
    browser = _Browser()
    pool = SharedContexts(browser, _ORIGIN)
    pool.new_page(viewport={"width": 1100, "height": 950})
    pool.new_page(viewport={"width": 390, "height": 844}, color_scheme="dark")
    assert len(browser.made) == 1 and browser.made[0][0] == {}
    log = browser.made[0][1].log
    assert "viewport 1100" in log and "viewport 390" in log and "scheme dark" in log


def test_a_later_page_in_the_same_test_starts_with_empty_origin_storage():
    browser = _Browser()
    pool = SharedContexts(browser, _ORIGIN)
    pool.new_page()
    log = browser.made[0][1].log
    assert "clear origin state" not in log
    pool.new_page()
    assert "clear origin state" in log


def test_releasing_resets_what_the_test_leased_and_a_failed_reset_discards_the_context():
    browser = _Browser()
    pool = SharedContexts(browser, _ORIGIN)
    kept = pool.new_context()
    broken = pool.new_context(has_touch=True)
    broken._fail_on = "clear_cookies"
    pool.release_leased()
    assert not kept.closed and broken.closed
    assert shared.STATS["discarded"] == 1
    assert pool.new_context() is kept
    assert pool.new_context(has_touch=True) is not broken


def test_context_key_ignores_argument_order():
    assert context_key({"a": 1, "b": 2}) == context_key({"b": 2, "a": 1})


def test_no_e2e_file_sets_state_on_a_shared_context_that_the_reset_cannot_undo():
    """Init scripts, offline mode and the like outlive a page, so they belong on the page."""
    offenders = []
    pattern = re.compile(r"\b(?:ctx|context)\.(?:add_init_script|set_offline|set_geolocation|close)\(")
    for path in sorted(_E2E.glob("test_*.py")):
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.name}:{number}: {line.strip()}")
    assert not offenders, offenders
