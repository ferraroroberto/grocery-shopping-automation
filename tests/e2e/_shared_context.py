"""One browser context per distinct context arguments, reset between tests.

A fresh context per test starts with no sockets and an empty HTTP cache, so every
test pays its own connections to the app, and every one it closes parks a
loopback port in TIME_WAIT (see ``_port_pool``). Here a context outlives the test
that asked for it: each test gets a new page in it, and the reset below puts back
what a context keeps between pages.

The reset covers the context state this suite touches: the pages, the context's
routes, cookies and permission grants, and the app origin's local and session
storage. A test that needs state the reset cannot undo (context init scripts,
offline mode, geolocation) must not set it on a shared context: install init
scripts on the page, which dies with it.

A reset that raises discards the context, so a damaged one never reaches the next test.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from playwright.sync_api import Browser, BrowserContext, Page, Route

logger = logging.getLogger(__name__)

# Runs on the app's origin once every page is closed: what outlives a page there.
_CLEAR_ORIGIN_STATE = "() => { localStorage.clear(); sessionStorage.clear(); }"

# The `new_page` arguments that are also page-level state, and so die with the page.
PAGE_LEVEL_ARGS = ("viewport", "color_scheme", "extra_http_headers")

#: How the shared contexts were used this session, for the run summary.
STATS = {"created": 0, "reused": 0, "discarded": 0}


def context_key(args: dict[str, Any]) -> str:
    """A stable identity for a set of context arguments."""
    return json.dumps(args, sort_keys=True, default=repr)


def _blank(route: Route) -> None:
    route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>reset</title>")


def reset_context(context: BrowserContext, origin: str) -> None:
    """Return *context* to what a freshly created one holds, for *origin*'s state.

    The app's origin is answered by a route, so clearing its storage opens no
    socket to the app.
    """
    for page in list(context.pages):
        page.close()
    context.unroute_all(behavior="ignoreErrors")
    context.clear_cookies()
    context.clear_permissions()
    scratch = context.new_page()
    try:
        clear_origin_state(scratch, origin)
    finally:
        scratch.close()


def clear_origin_state(page: Page, origin: str) -> None:
    """Empty *origin*'s local and session storage through *page*, with the origin routed so no socket opens."""
    page.route(f"{origin}/**", _blank)
    try:
        page.goto(f"{origin}/")
        page.evaluate(_CLEAR_ORIGIN_STATE)
    finally:
        page.unroute_all(behavior="ignoreErrors")


class SharedContexts:
    """A browser's contexts keyed by their arguments, handed out to one test at a time."""

    def __init__(self, browser: Browser, origin: str) -> None:
        self._browser = browser
        self._origin = origin
        self._by_key: dict[str, BrowserContext] = {}
        self._leased: dict[str, BrowserContext] = {}

    def __getattr__(self, name: str) -> Any:
        return getattr(self._browser, name)

    def new_context(self, **args: Any) -> BrowserContext:
        key = context_key(args)
        context = self._by_key.get(key)
        if context is None:
            context = self._browser.new_context(**args)
            self._by_key[key] = context
            STATS["created"] += 1
        else:
            STATS["reused"] += 1
        self._leased[key] = context
        return context

    def new_page(self, **args: Any) -> Any:
        """A page in the shared context for the context-level *args*.

        The arguments a page can take on its own (the viewport, the colour scheme, extra
        headers) are applied to the page, so pages that differ only in those share one
        context and so its sockets.
        """
        page_level = {name: args.pop(name) for name in PAGE_LEVEL_ARGS if name in args}
        used = context_key(args) in self._leased
        page = self.new_context(**args).new_page()
        if used:
            # A later page in the test starts like one in a fresh context: the app reopens on
            # the tab an earlier page left in storage, so clear it.
            clear_origin_state(page, self._origin)
        if "viewport" in page_level:
            page.set_viewport_size(page_level["viewport"])
        if "color_scheme" in page_level:
            page.emulate_media(color_scheme=page_level["color_scheme"])
        if "extra_http_headers" in page_level:
            page.set_extra_http_headers(page_level["extra_http_headers"])
        return page

    def release_leased(self) -> None:
        """Reset every context the finished test used, dropping one whose reset fails."""
        leased, self._leased = self._leased, {}
        for key, context in leased.items():
            try:
                reset_context(context, self._origin)
            except Exception:
                logger.warning("⚠️ shared browser context reset failed; discarding it", exc_info=True)
                STATS["discarded"] += 1
                self._by_key.pop(key, None)
                try:
                    context.close()
                except Exception:
                    logger.warning("⚠️ discarded context did not close cleanly", exc_info=True)


def write_summary(terminalreporter: Any) -> None:
    total = STATS["created"] + STATS["reused"]
    if total:
        terminalreporter.write_line(
            f"shared contexts: {STATS['created']} created for {total} requests "
            f"({STATS['reused']} reused, {STATS['discarded']} discarded after a failed reset)"
        )
