"""End-to-end tests that drive the real PWA buttons in a browser.

The LLM hub is stubbed by default (deterministic, runnable offline / in CI).
Set GROCERY_E2E_LIVE=1 to instead hit the real hub on :8000 — used to prove the
audio-match timeout fix against an actual model.
"""

from __future__ import annotations

import os
import shutil
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

import app.api as api
import src.data as data
from app.api import app
from src.inventory_extract import ExtractionResult

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "list_test_fixture.xlsx"
LIVE = os.environ.get("GROCERY_E2E_LIVE") == "1"
TRANSCRIPT = "en la nevera, tengo dos yogures y un litro de leche. en el congelador, tres salmones."


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _stub_extract(transcript, df, *, base_url, model, max_tokens, timeout):
    first = int(df.index[0])
    return ExtractionResult(
        items=[{"idx": first, "count": 2, "zone": str(df.loc[first, "lugar"]), "evidence": "dos"}],
        zones_mentioned=[str(df.loc[first, "lugar"])],
        unmatched_mentions=[],
        raw_text="{}",
    )


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    import uvicorn

    tmp = tmp_path_factory.mktemp("e2e")
    xlsx = tmp / "inv.xlsx"
    shutil.copyfile(FIXTURE, xlsx)
    logs_dir = tmp / "logs"
    orig = (
        data.CONFIG["data"]["xlsx_file"],
        data.CONFIG["audio_audit"]["logs_dir"],
        api.extract,
    )
    data.CONFIG["data"]["xlsx_file"] = str(xlsx)
    data.CONFIG["audio_audit"]["logs_dir"] = str(logs_dir)
    if not LIVE:
        api.extract = _stub_extract

    cfg = app.state.webapp_config
    orig_auth = (cfg.auth_token, cfg.auth_password)
    cfg.auth_token = ""
    cfg.auth_password = ""

    port = _free_port()
    config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
    srv = uvicorn.Server(config)
    thread = threading.Thread(target=srv.run, daemon=True)
    thread.start()
    while not srv.started:
        time.sleep(0.1)
    try:
        yield SimpleNamespace(url=f"http://127.0.0.1:{port}", logs_dir=logs_dir)
    finally:
        srv.should_exit = True
        thread.join(timeout=5)
        data.CONFIG["data"]["xlsx_file"], data.CONFIG["audio_audit"]["logs_dir"], api.extract = orig
        cfg.auth_token, cfg.auth_password = orig_auth


@pytest.fixture(scope="module")
def browser(server):
    with sync_playwright() as p:
        b = p.chromium.launch()
        yield b
        b.close()


@pytest.fixture()
def page(browser, server):
    pg = browser.new_page(viewport={"width": 1100, "height": 950})
    errors: list[str] = []
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    pg._js_errors = errors  # type: ignore[attr-defined]
    pg.goto(server.url)
    pg.wait_for_selector("[data-mode='audio']")
    yield pg
    pg.close()


@pytest.mark.e2e
def test_all_tabs_render_without_js_errors(page):
    for mode in ["dashboard", "audit", "targets", "edit", "add", "shopping", "audio", "automation"]:
        page.click(f"[data-mode='{mode}']")
        page.wait_for_timeout(150)
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_dashboard_shows_stocked_metric(page):
    page.click("[data-mode='dashboard']")
    page.wait_for_selector(".summary")
    assert page.locator("text=Stocked").count() >= 1
    assert page.locator("text=Tracked items").count() >= 1


@pytest.mark.e2e
def test_add_item_increases_count(page):
    page.click("[data-mode='dashboard']")
    page.wait_for_selector(".summary")
    before = int(page.locator(".metric strong").first.inner_text())
    page.click("[data-mode='add']")
    page.fill("#add-form input[name='comida']", "zzz e2e item")
    page.fill("#add-form input[name='super']", "mercadona")
    page.fill("#add-form input[name='lugar']", "nevera")
    page.click("#add-form button[type='submit']")
    page.wait_for_function("document.querySelector('#status')?.textContent?.includes('Loaded')")
    page.click("[data-mode='dashboard']")
    page.wait_for_selector(".summary")
    after = int(page.locator(".metric strong").first.inner_text())
    assert after == before + 1


@pytest.mark.e2e
def test_audio_match_and_apply_writes_log(page, server):
    page.click("[data-mode='audio']")
    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    page.wait_for_selector("text=Detected Items", timeout=120000)
    # the accept checkbox is pre-ticked; apply it
    page.click("#apply-audio")
    page.wait_for_function(
        "document.querySelector('#audio-status')?.textContent?.includes('Inventory updated')",
        timeout=30000,
    )
    assert page._js_errors == [], f"JS errors: {page._js_errors}"
    logs = list(server.logs_dir.glob("*.json"))
    assert logs, "apply should have written an audit log"


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="set GROCERY_E2E_LIVE=1 and run the hub to exercise the real LLM")
def test_audio_match_live_hub(page):
    page.click("[data-mode='audio']")
    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    # Real hub call — proves no premature timeout (budget up to 10 min).
    page.wait_for_selector("text=Detected Items", timeout=600000)
    assert page.locator("#audio-status.ok").count() >= 1
