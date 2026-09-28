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

import app.routers.audio as audio_router
import src.data as data
from app.api import app
from src.inventory_extract import ExtractionResult

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "list_test_fixture.xlsx"
# Invented benchmark run the Items → Stores view prices from (#148).
BENCHMARK_RUNS = REPO_ROOT / "tests" / "fixtures" / "store_links"
LIVE = os.environ.get("GROCERY_E2E_LIVE") == "1"
TRANSCRIPT = "en la nevera, tengo dos yogures y un litro de leche. en el congelador, tres salmones."

# The 9 modes group into the fleet nav's tabs (mirrors MODE_TO_TAB in app.js);
# grouped modes are reached via a sub-pill inside their tab's pane.
TAB_FOR_MODE = {
    "dashboard": "inventory",
    "shopping": "shopping",
    "audit": "audit",
    "audio": "audit",
    "targets": "items",
    "edit": "items",
    "add": "items",
    "stores": "items",
    "search": "search",
    "automation": "automation",
    "settings": "settings",
}


def goto_mode(page, mode: str) -> None:
    """Navigate to a mode: click its nav tab, then its sub-pill when grouped."""
    page.click(f"[data-tab='{TAB_FOR_MODE[mode]}']")
    pill = page.locator(f".subnav [data-mode='{mode}']")
    if pill.count():
        pill.click()
    page.wait_for_timeout(100)


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


def _stub_extract_named_but_uncounted(transcript, df, *, base_url, model, max_tokens, timeout):
    """One counted row plus one row the speaker *named* without a readable count,
    both in the same walked zone — the exact shape of issue #132, where the named
    row was also offered for zeroing."""
    zone = "congelador"
    zone_rows = df[df["lugar"] == zone]
    counted = int(zone_rows.index[0])
    # A row that would otherwise land in the zero-list: targeted and in stock.
    uncounted = int(next(
        i for i, row in zone_rows.iterrows()
        if int(i) != counted and int(row["cantidad"]) > 0 and int(row["tenemos"]) > 0
    ))
    return ExtractionResult(
        items=[{"idx": counted, "count": 2, "zone": zone, "evidence": "dos"}],
        zones_mentioned=[zone],
        unmatched_mentions=[{
            "phrase": "algo que suena parecido",
            "idx": uncounted,
            "approx_count": None,
            "note": "matches but no count dictated",
            "comida": str(df.loc[uncounted, "comida"]),
            "resolved_by": "llm",
            "match_score": None,
        }],
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
        audio_router.extract,
    )
    orig_benchmark = data.CONFIG.get("benchmark")
    data.CONFIG["benchmark"] = {"runs_dir": str(BENCHMARK_RUNS)}
    data.CONFIG["data"]["xlsx_file"] = str(xlsx)
    data.CONFIG["audio_audit"]["logs_dir"] = str(logs_dir)
    if not LIVE:
        audio_router.extract = _stub_extract

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
        if orig_benchmark is None:
            data.CONFIG.pop("benchmark", None)
        else:
            data.CONFIG["benchmark"] = orig_benchmark
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
    pg.wait_for_selector("[data-tab='audit']")
    pg.wait_for_function("document.querySelector('#status')?.textContent?.includes('Loaded')")
    yield pg
    pg.close()


@pytest.mark.e2e
def test_all_tabs_render_without_js_errors(page):
    for mode in ["dashboard", "audit", "targets", "edit", "add", "stores", "shopping", "audio", "search",
                 "automation", "settings"]:
        goto_mode(page, mode)
        page.wait_for_timeout(150)
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_standalone_page_head_meta(page):
    """The installed iOS PWA needs the translucent status bar + viewport-fit=cover,
    or the nav pill lands one status-bar height too low (_vendored/nav README
    step 4). Desktop browsers can't reproduce that, so assert the pair here."""
    status_bar = page.locator('meta[name="apple-mobile-web-app-status-bar-style"]')
    assert status_bar.get_attribute("content") == "black-translucent"
    assert "viewport-fit=cover" in page.locator('meta[name="viewport"]').get_attribute("content")


@pytest.mark.e2e
def test_search_tab_renders_shell(page):
    """The Search tab renders its input + Buscar button (no live search run)."""
    goto_mode(page, "search")
    page.wait_for_selector("#search-term")
    assert page.locator("#search-run").is_visible()
    assert page.locator("#search-record").is_visible()


@pytest.mark.e2e
def test_dashboard_shows_stocked_metric(page):
    goto_mode(page, "dashboard")
    page.wait_for_selector(".summary")
    assert page.locator("text=Stocked").count() >= 1
    assert page.locator("text=Tracked items").count() >= 1
    # Build identity footer (home-automation contract) is filled at boot.
    page.wait_for_function("document.querySelector('#build-readout')?.textContent?.startsWith('Build:')")


@pytest.mark.e2e
def test_search_only_on_filterable_modes(page):
    goto_mode(page, "dashboard")
    assert page.locator("#toolbar").is_visible()
    goto_mode(page, "shopping")
    assert not page.locator("#toolbar").is_visible()
    goto_mode(page, "settings")
    assert not page.locator("#toolbar").is_visible()
    assert page.locator("#open-sheet").is_visible()


@pytest.mark.e2e
def test_add_item_increases_count(page):
    goto_mode(page, "dashboard")
    page.wait_for_selector(".summary")
    before = int(page.locator(".metric strong").first.inner_text())
    goto_mode(page, "add")
    page.fill("#add-form input[name='comida']", "zzz e2e item")
    page.fill("#add-form input[name='super']", "mercadona")
    page.fill("#add-form input[name='lugar']", "nevera")
    page.click("#add-form button[type='submit']")
    # Mutations report transient "Saved" feedback; the resting count only
    # shows on Home (the repeated "Loaded N items" line was UI noise).
    page.wait_for_function("document.querySelector('#status')?.textContent?.includes('Saved')")
    goto_mode(page, "dashboard")
    page.wait_for_selector(".summary")
    after = int(page.locator(".metric strong").first.inner_text())
    assert after == before + 1


@pytest.mark.e2e
def test_audio_match_and_apply_writes_log(page, server):
    goto_mode(page, "audio")
    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    page.wait_for_selector("text=Detected Items", timeout=120000)
    # the accept switch renders pre-on (the old checkbox's pre-ticked guarantee)
    accept = page.locator("[data-audio-idx]").first
    assert accept.get_attribute("aria-checked") == "true"
    page.click("#apply-audio")
    page.wait_for_function(
        "document.querySelector('#audio-status')?.textContent?.includes('Inventory updated')",
        timeout=30000,
    )
    assert page._js_errors == [], f"JS errors: {page._js_errors}"
    logs = list(server.logs_dir.glob("*.json"))
    assert logs, "apply should have written an audit log"


@pytest.mark.e2e
def test_named_item_is_never_offered_for_zeroing(page, monkeypatch):
    """Regression for issue #132 — a mention that resolved to a row gets a count
    box, and must NOT also appear under "set to 0". It did, and zeroed actimel."""
    monkeypatch.setattr(audio_router, "extract", _stub_extract_named_but_uncounted)
    goto_mode(page, "audio")
    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    page.wait_for_selector("[data-audio-count-idx]", timeout=120000)

    box = page.locator("[data-audio-count-idx]")
    assert box.count() == 1, "the named-but-uncounted row should offer a count box"
    idx = box.first.get_attribute("data-audio-count-idx")
    assert page.locator(f"[data-audio-zero='{idx}']").count() == 0, (
        f"idx {idx} was named out loud — offering it for zeroing is the bug"
    )

    # And the typed count actually applies.
    box.first.fill("4")
    page.click("#apply-audio")
    page.wait_for_function(
        "document.querySelector('#audio-status')?.textContent?.includes('Inventory updated')",
        timeout=30000,
    )
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_stores_plan_simulate_and_apply(page, server):
    """Items → Stores (#148): import the fixture run, load the recommended plan
    and see the simulated total move, reveal every store with the Show-all
    switch (#163), then review and apply one store change —
    the burger to Carrefour, its target converted by pack size (3 × 0.3 kg →
    2 × 0.6 kg)."""
    goto_mode(page, "stores")
    page.click("[data-stores-action='import']")
    page.wait_for_selector(".stores-head .panel-status.ok")
    page.wait_for_selector("#stores-sim[data-state='ready']")
    today_total = page.locator(".stores-total strong").inner_text()

    page.click("[data-stores-action='recommended']")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent !== before", arg=today_total,
    )
    assert "Saves" in page.locator(".stores-delta").inner_text()

    # Back to the list, then one what-if pick: nothing is written until Apply.
    page.click("[data-stores-action='reset']")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent === before", arg=today_total,
    )
    burger = page.locator(".store-row", has_text="burguer ternera").first
    # Only the stores the list buys from are shown until "Show all stores".
    assert burger.locator(".store-chip", has_text="Carrefour").count() == 0
    page.click("[data-stores-showall]")
    page.wait_for_selector(".store-row .store-chip:has-text('Carrefour')")
    burger = page.locator(".store-row", has_text="burguer ternera").first
    burger.locator("[data-stores-pick]").select_option("carrefour")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent !== before", arg=today_total,
    )
    assert burger.locator(".store-chip.is-picked").inner_text().startswith("Carrefour")

    page.click("[data-stores-action='review']")
    row = page.locator("#stores-apply-dialog .apply-row")
    row.first.wait_for()
    assert row.count() == 1
    assert row.first.locator(".apply-qty").input_value() == "2"
    page.click("#stores-apply-dialog .detail-save-btn")
    page.wait_for_selector("#stores-action-status.ok")
    assert not page.locator("#stores-apply-dialog").is_visible()

    item = next(i for i in page.request.get(f"{server.url}/api/inventory").json()["items"]
                if i["comida"] == "burguer ternera")
    assert (item["super"], item["cantidad"]) == ("carrefour", 2)
    assert item["buscador"] == item["urls"]["carrefour"]
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="set GROCERY_E2E_LIVE=1 and run the hub to exercise the real LLM")
def test_audio_match_live_hub(page):
    goto_mode(page, "audio")
    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    # Real hub call — proves no premature timeout (budget up to 10 min).
    page.wait_for_selector("text=Detected Items", timeout=600000)
    assert page.locator("#audio-status.ok").count() >= 1
