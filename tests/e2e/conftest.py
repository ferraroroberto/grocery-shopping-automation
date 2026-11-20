"""Fixtures for the PWA end-to-end stories: a disposable in-thread server and a browser.

The LLM hub is stubbed by default (deterministic, runnable offline / in CI).
Set GROCERY_E2E_LIVE=1 to instead hit the real hub on :8000 — used to prove the
audio-match timeout fix against an actual model.

The server is module-scoped, so each story file starts from the committed
fixture list and the tests inside one file share it in file order.

Loopback sockets: every closed connection to the server leaves a TIME_WAIT pair,
and a drained pool fails a page load as ERR_ADDRESS_IN_USE. The server holds idle
connections for HARNESS_KEEP_ALIVE_S, a context outlives its test (`_shared_context`)
and a drained pool is named as its own failure (`_port_pool`).
"""

from __future__ import annotations

import shutil
import socket
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from playwright.sync_api import sync_playwright

import app.webapp.routers.audio as audio_router
import src.automation.product_options as product_options
import src.data as data
from app.api import app
from tests.e2e._helpers import LIVE, LOADED_JS, stub_extract
from tests.e2e._port_pool import pytest_runtest_makereport  # noqa: F401 — relabel hook
from tests.e2e._port_pool import pytest_terminal_summary as port_pool_summary
from tests.e2e._shared_context import SharedContexts
from tests.e2e._shared_context import write_summary as shared_context_summary

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "list_test_fixture.xlsx"
# Invented benchmark run the Items → Stores view prices from (#148).
BENCHMARK_RUNS = REPO_ROOT / "tests" / "fixtures" / "store_links"
# uvicorn closes an idle connection after 5 s, shorter than a test's pause between
# page loads, so the browser reopened one per burst; a minute keeps them in use.
HARNESS_KEEP_ALIVE_S = 60


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


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
        audio_router.extract,
    )
    orig_benchmark = data.CONFIG.get("benchmark")
    # A copy: the review writes _state/overrides.json, never into tests/fixtures/.
    runs = tmp / "runs"
    shutil.copytree(BENCHMARK_RUNS, runs)
    data.CONFIG["benchmark"] = {"runs_dir": str(runs)}
    # A product option (#179) on the fixture burger's Carrefour product.
    options = tmp / "product_options.json"
    options.write_text('{"carrefour": {"FIX-burger-600": {"cut": "Fileteado"}}}', encoding="utf-8")
    orig_options = product_options.DEFAULT_OPTIONS_PATH
    product_options.DEFAULT_OPTIONS_PATH = options
    data.CONFIG["data"]["xlsx_file"] = str(xlsx)
    data.CONFIG["audio_audit"]["logs_dir"] = str(logs_dir)
    # An invented last fill (#258): Mercadona's run left one item out, so Shop's
    # glance, header and tab badge have a miss to show. Never the real records.
    purchase_logs = tmp / "purchase_logs"
    (purchase_logs / "runs").mkdir(parents=True)
    (purchase_logs / "runs" / "2026-10-06T191701_mercadona.json").write_text(
        '{"run": "2026-10-06T191701", "store": "mercadona", "cart_mode": "keep",'
        ' "items": [{"status": "added_verified"}, {"status": "out_of_stock"}]}', encoding="utf-8")
    orig_purchase = data.CONFIG["automation"].get("purchase_logs_dir")
    data.CONFIG["automation"]["purchase_logs_dir"] = str(purchase_logs)
    if not LIVE:
        audio_router.extract = stub_extract

    cfg = app.state.webapp_config
    orig_auth = (cfg.auth_token, cfg.auth_password)
    cfg.auth_token = ""
    cfg.auth_password = ""

    port = _free_port()
    config = uvicorn.Config(
        app, host="127.0.0.1", port=port, log_level="warning", timeout_keep_alive=HARNESS_KEEP_ALIVE_S
    )
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
        data.CONFIG["data"]["xlsx_file"], data.CONFIG["audio_audit"]["logs_dir"], audio_router.extract = orig
        product_options.DEFAULT_OPTIONS_PATH = orig_options
        if orig_purchase is None:
            data.CONFIG["automation"].pop("purchase_logs_dir", None)
        else:
            data.CONFIG["automation"]["purchase_logs_dir"] = orig_purchase
        if orig_benchmark is None:
            data.CONFIG.pop("benchmark", None)
        else:
            data.CONFIG["benchmark"] = orig_benchmark
        cfg.auth_token, cfg.auth_password = orig_auth



@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(pw, server, browser_name):
    """The engine `--browser` names (Chromium by default; the Mac nightly has only WebKit installed),
    with `new_context` / `new_page` leasing one shared context per arguments."""
    b = getattr(pw, browser_name).launch()
    yield SharedContexts(b, server.url)
    b.close()


@pytest.fixture(autouse=True)
def _reset_leased_contexts(request):
    """Reset every shared context the test used, so the next one starts from a clean one."""
    yield
    if "browser" in request.fixturenames:
        request.getfixturevalue("browser").release_leased()


def pytest_terminal_summary(terminalreporter):
    shared_context_summary(terminalreporter)
    port_pool_summary(terminalreporter)


@pytest.fixture()
def page(browser, server, request):
    pg = browser.new_page(viewport={"width": 1100, "height": 950})
    errors: list[str] = []
    pg.on("pageerror", lambda exc: errors.append(str(exc)))
    pg._js_errors = errors  # type: ignore[attr-defined]
    pg.goto(server.url)
    pg.wait_for_selector("[data-tab='audit']")
    pg.wait_for_function(LOADED_JS)
    yield pg
    # CI uploads test-results/ when the job fails (.github/workflows/ci.yml).
    failed = getattr(request.node, "rep_call", None)
    if failed is not None and failed.failed and not pg.is_closed():
        out = REPO_ROOT / "test-results"
        out.mkdir(exist_ok=True)
        pg.screenshot(path=str(out / f"{request.node.name}.png"))
    # The page closes with its context's reset (`_reset_leased_contexts`).
