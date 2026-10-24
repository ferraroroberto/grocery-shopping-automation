"""Fixtures for the PWA end-to-end stories: a disposable in-thread server and a browser.

The LLM hub is stubbed by default (deterministic, runnable offline / in CI).
Set GROCERY_E2E_LIVE=1 to instead hit the real hub on :8000 — used to prove the
audio-match timeout fix against an actual model.

The server is module-scoped, so each story file starts from the committed
fixture list and the tests inside one file share it in file order.
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

import app.routers.audio as audio_router
import automation.product_options as product_options
import src.data as data
from app.api import app
from tests.e2e._helpers import LIVE, LOADED_JS, stub_extract

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE = REPO_ROOT / "tests" / "list_test_fixture.xlsx"
# Invented benchmark run the Items → Stores view prices from (#148).
BENCHMARK_RUNS = REPO_ROOT / "tests" / "fixtures" / "store_links"


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
def browser(pw, server):
    b = pw.chromium.launch()
    yield b
    b.close()


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
    pg.close()
