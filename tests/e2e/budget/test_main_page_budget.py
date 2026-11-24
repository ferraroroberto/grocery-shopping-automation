"""The main page keeps the fleet's request budget: a minute visible, a minute hidden, the return."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.e2e.budget._request_budget import (
    assert_request_budget,
    measure_request_budget,
    visible_budget,
)

REPO_ROOT = Path(__file__).resolve().parents[3]
USAGE_PATH = "/api/usage/events"


@pytest.mark.e2e
def test_main_page_keeps_its_request_budget(browser, server):
    page = browser.new_page(viewport={"width": 1100, "height": 950})
    reading = measure_request_budget(page, f"{server.url}/")
    # The usage log sends its queued events once as the page goes hidden (one
    # beacon on `visibilitychange`, so no event is lost with the tab). It is a
    # flush on a transition, not a poll: it fires once and is silent for the
    # rest of the minute. Every other path must stay silent while hidden.
    assert reading.hidden.pop(USAGE_PATH, 0) <= 1, reading.summary()
    assert_request_budget(reading, visible_budget(REPO_ROOT))
