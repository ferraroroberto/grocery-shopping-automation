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
import automation.product_options as product_options
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

# 8 of the 9 modes group into the fleet nav's 4 tabs (mirrors MODE_TO_TAB in
# modules/core.js); grouped modes are reached via a sub-pill inside their tab's
# pane. Product search lives in Items -> Add Item and cart automation ("Fill
# carts") at the bottom of Shop since the Search/Auto tabs were retired (#182).
# Settings is the 9th and never a tab (#200): the header gear opens it.
TAB_FOR_MODE = {
    "dashboard": "inventory",
    "shopping": "shopping",
    "audit": "audit",
    "audio": "audit",
    "targets": "items",
    "edit": "items",
    "add": "items",
    "stores": "items",
}


def goto_mode(page, mode: str) -> None:
    """Navigate to a mode: click its nav tab, then its sub-pill when grouped.
    Settings has no tab -- it opens from the gear in the visible page header."""
    if mode == "settings":
        if not page.locator("#pane-settings").is_visible():
            page.locator(".home-settings:visible").click()
        page.wait_for_timeout(100)
        return
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
        product_options.DEFAULT_OPTIONS_PATH = orig_options
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
    for mode in ["dashboard", "audit", "targets", "edit", "add", "stores", "shopping", "audio", "settings"]:
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
def test_four_tab_nav_and_relocated_sections(page, pw, server):
    """#182 + #200: four tabs, and every capability of the retired Search/Auto
    tabs in reach -- product search in Items -> Add Item, Fill carts at the
    bottom of Shop, Email Watch in Settings (the header gear, never a tab) --
    with a saved retired tab reopening on its new home instead of falling back
    to Home. Then the WebKit 390px nav check."""
    tabs = page.locator("nav.tabs .tab")
    assert tabs.count() == 4
    assert tabs.locator(".tab-label").all_inner_texts() == ["Home", "Shop", "Audit", "Items"]
    assert page.locator("nav.tabs [data-tab='settings']").count() == 0

    # J-04 (#153) + #200: every pane opens on its own page header, not a bare
    # control row -- the vendored home-head (icon + tab-naming title + context
    # line + theme toggle + Settings gear) as each pane's first element, and
    # only the active pane's is ever visible at once.
    header_titles = {
        "pane-inventory": "Home", "pane-shopping": "Shop", "pane-audit": "Audit",
        "pane-items": "Items", "pane-settings": "Settings",
    }
    for pane_id, title in header_titles.items():
        header = page.locator(f"#{pane_id} > :first-child")
        assert "home-head" in (header.get_attribute("class") or ""), f"{pane_id}'s first child isn't the page header"
        assert header.locator(".home-title").inner_text().strip() == title
        assert header.locator(".theme-toggle").count() == 1
        assert header.locator(".home-settings").count() == 1
    assert page.locator(".home-head").count() == 5
    assert page.locator(".home-head:visible").count() == 1

    goto_mode(page, "add")
    assert page.locator("#pane-items #product-search #search-term").is_visible()

    goto_mode(page, "shopping")
    fill = page.locator("#pane-shopping #fill-carts")
    fill.wait_for()
    for control in ("#automation-store", "#automation-cart-mode", "#automation-dry-run", "#automation-start"):
        assert fill.locator(control).is_visible(), f"{control} missing from Fill carts"
    # The raw pythonw ... argv is folded under "Command", not shown as a label.
    assert not fill.locator("#automation-command").is_visible()
    # A shopping-list re-render (Got it) must not reset the run picks.
    fill.locator("#automation-cart-mode").select_option("clean")
    page.locator("#pane-shopping details[data-store] > summary").first.click()
    page.locator("#pane-shopping [data-action='mark-buy']").first.click()
    page.locator("#pane-shopping [data-action='undo-buy']").first.wait_for()
    assert fill.locator("#automation-cart-mode").input_value() == "clean"

    # Theme toggle works from every pane's own header, not just Home's
    # (#theme-toggle id stays on Home's for back-compat; the rest share the
    # .theme-toggle class, #153 J-04).
    theme = lambda: page.evaluate("document.documentElement.getAttribute('data-theme')")  # noqa: E731
    before_theme = theme()
    page.locator("#pane-shopping .theme-toggle").click()
    assert theme() != before_theme
    page.locator("#pane-shopping .theme-toggle").click()
    assert theme() == before_theme

    goto_mode(page, "settings")
    page.locator("#pane-settings #email-monitor").wait_for()

    # A PWA last closed on a retired tab reopens on that tab's new home.
    for saved, tab, landing in (("search", "items", "#pane-items #search-term"),
                                ("automation", "shopping", "#pane-shopping #fill-carts")):
        page.evaluate("t => localStorage.setItem('grocery.tab', t)", saved)
        page.reload()
        page.locator(landing).wait_for()
        assert page.locator("nav.tabs").get_attribute("data-active-tab") == tab
        assert page.evaluate("() => localStorage.getItem('grocery.tab')") == tab
        if saved == "search":
            assert "active" in page.locator(".subnav [data-mode='add']").get_attribute("class")
    page.wait_for_function(  # scrolled to Fill carts, below the store list
        "() => { const r = document.querySelector('#fill-carts').getBoundingClientRect();"
        " return r.top < innerHeight && r.bottom > 0; }")
    assert page._js_errors == [], f"JS errors: {page._js_errors}"

    # WebKit (iPhone, 390px): four tabs with full labels, 44px targets, no sideways scroll.
    webkit = pw.webkit.launch()
    try:
        phone = webkit.new_page(**pw.devices["iPhone 13"])
        errors: list[str] = []
        phone.on("pageerror", lambda exc: errors.append(str(exc)))
        phone.goto(server.url)
        phone.wait_for_function("document.querySelector('#status')?.textContent?.includes('Loaded')")
        assert phone.viewport_size["width"] == 390
        boxes = [phone.locator("nav.tabs .tab").nth(i).bounding_box() for i in range(4)]
        assert all(b["x"] >= 0 and b["x"] + b["width"] <= 390 for b in boxes), boxes
        assert all(b["width"] >= 44 and b["height"] >= 44 for b in boxes), boxes
        assert phone.locator("nav.tabs .tab-label").evaluate_all(
            "els => els.every(e => e.scrollWidth <= e.clientWidth)"), "a tab label is truncated at 390px"
        assert phone.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        assert errors == [], f"WebKit JS errors: {errors}"
    finally:
        webkit.close()


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
def test_item_forms_label_every_field(page):
    """Edit item and Add item name all six fields (#213): the numbers read
    "Target" / "In stock" even when the box holds a value."""
    labels = ["Item", "Supermarket", "Zone", "Target", "In stock", "URL"]
    for mode, selector in (("edit", ".edit-form"), ("add", "#add-form")):
        goto_mode(page, mode)
        form = page.locator(selector).first
        form.wait_for()
        for label in labels:
            assert form.get_by_label(label, exact=True).count() == 1, f"{mode}: {label}"
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_add_item_increases_count(page):
    goto_mode(page, "dashboard")
    page.wait_for_selector(".summary")
    before = int(page.locator(".summary-value").first.inner_text())
    goto_mode(page, "add")
    page.fill("#add-form input[name='comida']", "zzz e2e item")
    page.fill("#add-form input[name='super']", "mercadona")
    page.fill("#add-form input[name='lugar']", "nevera")
    page.click("#add-form button[type='submit']")
    # Mutations report a transient "Saved" toast (#200); the resting count only
    # shows on Home (the repeated "Loaded N items" line was UI noise).
    page.wait_for_function("document.querySelector('#toast')?.textContent?.includes('Saved')")
    goto_mode(page, "dashboard")
    page.wait_for_selector(".summary")
    after = int(page.locator(".summary-value").first.inner_text())
    assert after == before + 1


@pytest.mark.e2e
def test_audio_match_and_apply_writes_log(page, server):
    goto_mode(page, "audio")
    # J-07: the Match model dropdown shows a humanized label, never the raw
    # config id (gemini_pro) verbatim.
    model_label = page.locator("#audio-model option:checked").inner_text()
    assert model_label == "Gemini Pro"
    assert "_" not in model_label
    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    page.wait_for_selector("text=Detected items", timeout=120000)
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
    2 × 0.6 kg). Then the per-item review (#165): the moved burger needs
    checking; its detail shows the quantity maths, a price override moves the
    simulator and the chip until it is reset, and Checked clears it."""
    goto_mode(page, "stores")
    page.click("[data-stores-action='import']")
    page.wait_for_selector(".stores-head .panel-status.ok")
    page.wait_for_selector("#stores-sim[data-state='ready']")
    # LAYOUT-02: the Stores list gets its own filter input in the list's own
    # card (the re-homed global toolbar search), not a second search box.
    assert page.locator(".stores-list-card input[type='search']").count() == 1
    assert page.locator("#toolbar input[type='search']").count() == 1  # the same node, moved
    # LAYOUT-03 (#194, accepted in .fleet.toml): a store chip with a product
    # URL is a link that opens it in a new tab — the picked (today's) store's
    # chip here is the imported Ametller link.
    burger_row = page.locator(".store-row", has_text="burguer ternera").first
    ametller_chip = burger_row.locator(".store-chip.is-picked")
    assert ametller_chip.count() == 1
    assert ametller_chip.evaluate("el => el.tagName") == "A"
    assert ametller_chip.get_attribute("href") == \
        "https://www.ametllerorigen.com/es/american-burger-ametller-origen-150g-2uds/p?sc=14"
    assert ametller_chip.get_attribute("target") == "_blank"
    # The re-homed search actually filters the Stores rows (not just present).
    all_row_count = page.locator(".store-row").count()
    page.fill("#search", "burguer ternera")
    page.wait_for_function("document.querySelectorAll('.store-row').length === 1")
    assert "burguer ternera" in page.locator(".store-row").inner_text()
    page.fill("#search", "")
    page.wait_for_function(f"document.querySelectorAll('.store-row').length === {all_row_count}")
    # #193 regression: typing character by character (not a programmatic
    # fill/dispatch) must not rebuild the Stores pane — the search keeps focus
    # and its full value, the rows filter as you type, and the Monthly cost
    # card is the exact same DOM node throughout (a tagged JS property proves
    # no renderStores() rebuild happened, as opposed to the list-only repaint).
    page.evaluate("document.querySelector('#stores-sim').__regression193 = true")
    page.click("#search")
    page.keyboard.type("burguer ternera", delay=50)
    assert page.evaluate("document.activeElement === document.querySelector('#search')")
    assert page.eval_on_selector("#search", "el => el.value") == "burguer ternera"
    page.wait_for_function("document.querySelectorAll('.store-row').length === 1")
    assert "burguer ternera" in page.locator(".store-row").inner_text()
    assert page.evaluate("document.querySelector('#stores-sim')?.__regression193") is True
    page.fill("#search", "")
    page.wait_for_function(f"document.querySelectorAll('.store-row').length === {all_row_count}")
    # Benchmark status: run date and the review due 90 days later, plus the copyable steps.
    status = page.locator(".stores-status").inner_text()
    assert "2026-01-15" in status and "2026-04-15" in status
    assert page.locator("[data-stores-action='copy-steps']").count() == 1
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
    assert (item["super"], item["cantidad"], item["tenemos"]) == ("carrefour", 2, 1)  # stock 2 × 0.3 / 0.6
    assert item["buscador"] == item["urls"]["carrefour"]

    # Store filter (#170): one store → only its rows; a second adds its rows; none → all.
    store_pill = "[data-stores-store='{}']"
    page.wait_for_selector(store_pill.format("carrefour"))
    all_rows = page.locator(".store-row").count()
    page.click(store_pill.format("carrefour"))
    assert page.locator(store_pill.format("carrefour")).get_attribute("aria-pressed") == "true"
    assert page.locator(".store-row").count() == 1
    assert "Carrefour" in page.locator(".store-row").first.inner_text()
    page.click(store_pill.format("ametller"))
    ametller = int(page.locator(f"{store_pill.format('ametller')} .pill-count").inner_text())
    assert page.locator(".store-row").count() == 1 + ametller
    page.click(store_pill.format("carrefour"))
    page.click(store_pill.format("ametller"))
    assert page.locator(".store-row").count() == all_rows

    # Only items I buy (#172): a target-0 row hides and the Store counts follow; off → it returns.
    bought = [i for i in page.request.get(f"{server.url}/api/inventory").json()["items"] if i["cantidad"] > 0]
    zero_row = page.locator(".store-row", has_text="amoniaco")  # target 0 in the fixture
    assert zero_row.count() == 1
    page.click("[data-stores-targetonly]")
    page.wait_for_function(f"document.querySelectorAll('.store-row').length === {len(bought)}")
    assert zero_row.count() == 0
    mercadona = sum(i["super"] == "mercadona" for i in bought)
    assert page.locator(f"{store_pill.format('mercadona')} .pill-count").inner_text() == str(mercadona)
    page.click("[data-stores-targetonly]")
    page.wait_for_function(f"document.querySelectorAll('.store-row').length === {all_rows}")
    assert zero_row.count() == 1

    # ── Per-item review (#165) ──
    count = "(key) => document.querySelector(`[data-stores-filter='${key}'] .pill-count`)?.textContent"
    page.wait_for_function(f"({count})('needs') === '1'")
    page.click("[data-stores-filter='needs']")
    burger = page.locator(".store-row", has_text="burguer ternera").first
    assert page.locator(".store-row").count() == 1
    assert burger.locator(".review-badge").inner_text().startswith("Moved store")

    # ── Baseline (#183): freeze the list as the new "today", confirm shows the
    # unchecked count, then reset restores the exact prior simulate result.
    hint = page.locator("#stores-sim .hint").first
    assert "benchmarked" in hint.inner_text()
    before_baseline_total = page.locator(".stores-total strong").inner_text()
    page.click("[data-stores-action='set-baseline']")
    baseline_dialog = page.locator("#stores-baseline-dialog")
    baseline_dialog.wait_for()
    assert "1 item" in baseline_dialog.inner_text() and "checking" in baseline_dialog.inner_text()
    baseline_dialog.locator(".detail-save-btn").click()
    page.wait_for_function("() => !document.querySelector('#stores-baseline-dialog').open")
    page.wait_for_function("() => document.querySelector('#stores-sim .hint')?.textContent.includes('baseline')")
    assert "Same as today" in page.locator(".stores-delta").inner_text()
    page.click("[data-stores-action='reset-baseline']")
    page.wait_for_function("() => !document.querySelector('#stores-sim .hint')?.textContent.includes('baseline')")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent === before", arg=before_baseline_total,
    )
    total = page.locator(".stores-total strong").inner_text()

    burger.locator("[data-stores-action='detail']").click()
    dialog = page.locator("#stores-detail-dialog")
    dialog.locator(".review-qty-lines").wait_for()
    qty = dialog.locator(".review-qty-lines").inner_text()
    assert "Ametller Origen 2 × 0.3 kg = 0.6 kg" in qty
    assert "Carrefour 2 × 0.6 kg = 1.2 kg (+100%)" in qty
    # The cut the cart picks for that product is shown on its store row (#179).
    assert dialog.locator("tr[data-review-store='carrefour'] .review-option").inner_text().startswith("Cut: Fileteado")
    assert dialog.locator("tr[data-review-store='ametller'] .review-option").count() == 0
    # Wide on a desktop, no sideways scroll on a phone.
    page.set_viewport_size({"width": 1280, "height": 900})
    assert dialog.bounding_box()["width"] >= 900
    page.set_viewport_size({"width": 390, "height": 844})
    assert dialog.evaluate("d => d.scrollWidth <= d.clientWidth"), "detail dialog scrolls sideways at 390px"
    page.set_viewport_size({"width": 1100, "height": 950})

    # Your pack price at Carrefour prices the simulator and the chip, until reset.
    carrefour_chip = burger.locator(".store-chip", has_text="Carrefour")
    assert "€14.40/mo" in carrefour_chip.inner_text()
    dialog.locator("[data-review-edit='carrefour']").click()
    dialog.locator("[data-review-form='carrefour'] [name='pack_price']").fill("6")
    dialog.locator("[data-review-form='carrefour'] button[type='submit']").click()
    dialog.locator("tr[data-review-store='carrefour'] .chip:has-text('yours')").wait_for()
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent !== before", arg=total,
    )
    assert "€12.00/mo" in carrefour_chip.inner_text()  # 1.2 kg a month / 0.6 kg × €6
    dialog.locator("[data-review-edit='carrefour']").click()
    dialog.locator("[data-review-reset='carrefour']").click()
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent === before", arg=total,
    )
    assert dialog.locator(".chip:has-text('yours')").count() == 0

    # Target and stock are saved to the list from the detail; the rest of the row is untouched.
    dialog.locator("[data-review-qty='cantidad']").fill("3")
    dialog.locator("[data-review-qty='tenemos']").fill("2")
    with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith(f"/api/items/{item['id']}")) as resp:
        dialog.locator(".detail-save-btn").click()
    assert resp.value.ok
    saved = next(i for i in page.request.get(f"{server.url}/api/inventory").json()["items"]
                 if i["comida"] == "burguer ternera")
    assert (saved["cantidad"], saved["tenemos"]) == (3, 2)
    assert (saved["super"], saved["buscador"]) == ("carrefour", item["buscador"])

    # Checked is stored server-side and empties the Needs-checking filter.
    dialog.locator("[data-review-checked]").click()
    page.wait_for_function(f"({count})('needs') === '0' && ({count})('checked') === '1'")
    assert dialog.locator("[data-review-checked]").get_attribute("aria-checked") == "true"
    dialog.locator("[data-dialog-close]").click()
    assert page.locator(".store-row").count() == 0
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


# Every dropdown, inline input and labelled button in one open dialog, checked
# against the vendored recipes (#207: select-native/, button/, modal/), with
# the expected values resolved from the theme's own tokens -- never hard-coded.
# A button must match one button.css tier (or the shared disabled recipe);
# glyph-only controls (the x close, .icon-button) and switches are out of scope.
_DIALOG_CONTROLS_PROBE = r"""(id) => {
  const dialog = document.getElementById(id);
  const card = dialog.querySelector('.detail-card');
  const probe = document.createElement('div');
  card.appendChild(probe);
  const color = (name) => { probe.style.cssText = `background-color: var(${name})`; return getComputedStyle(probe).backgroundColor; };
  const len = (name) => { probe.style.cssText = `width: var(${name})`; return parseFloat(getComputedStyle(probe).width); };
  const tiers = {
    primary: ['--accent-fill', '--accent-fg', '--accent-border-strong'],
    tint: ['--accent-soft', '--accent-text', '--accent-border-soft'],
    surface: ['--card-off', '--muted', '--line'],
    disabled: ['--card-off', '--muted', '--line'],
  };
  const expect = Object.fromEntries(Object.entries(tiers).map(([k, [bg, fg, border]]) =>
    [k, { bg: color(bg), fg: color(fg), border: color(border) }]));
  const radius = len('--radius-md'), controlH = len('--control-h'), primaryH = len('--primary-h');
  const field = { bg: color('--input-bg'), border: color('--control-border') };
  probe.remove();

  const cardBox = card.getBoundingClientRect();
  const shown = (el) => el.getClientRects().length > 0;
  const bad = [];
  const name = (el) => `${el.tagName.toLowerCase()}.${el.className} "${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 24)}"`;
  const inside = (el, box) => {
    if (box.right > cardBox.right + 1 || box.left < cardBox.left - 1) bad.push(`${name(el)} overflows the dialog`);
  };

  const buttons = [...card.querySelectorAll('button')]
    .filter((b) => shown(b) && !b.matches('.detail-close, .icon-button, [role=switch]'));
  for (const b of buttons) {
    const s = getComputedStyle(b), box = b.getBoundingClientRect();
    const got = { bg: s.backgroundColor, fg: s.color, border: s.borderTopColor };
    const pool = b.disabled ? ['disabled'] : ['primary', 'tint', 'surface'];
    const tier = pool.find((k) => ['bg', 'fg', 'border'].every((p) => got[p] === expect[k][p]));
    if (!tier) bad.push(`${name(b)} wears no button tier: ${JSON.stringify(got)}`);
    if (Math.abs(parseFloat(s.borderTopLeftRadius) - radius) > 0.5) bad.push(`${name(b)} radius ${s.borderTopLeftRadius}`);
    if (box.height < controlH - 0.5) bad.push(`${name(b)} is ${box.height}px tall`);
    if (b.scrollHeight > b.clientHeight + 1 || b.scrollWidth > b.clientWidth + 1) bad.push(`${name(b)} label overflows`);
    inside(b, box);
  }
  const footer = card.querySelector('.detail-actions');
  const save = footer?.querySelector('.detail-save-btn');
  if (!save) bad.push('no footer primary');
  else {
    const box = save.getBoundingClientRect(), fs = getComputedStyle(footer);
    const content = footer.clientWidth - parseFloat(fs.paddingLeft) - parseFloat(fs.paddingRight);
    if (box.height < primaryH - 0.5) bad.push(`footer primary is ${box.height}px tall`);
    if (Math.abs(box.width - content) > 1) bad.push(`footer primary is ${box.width}px of ${content}px`);
  }

  const fields = [...card.querySelectorAll('select, .input-native')].filter(shown);
  for (const f of fields) {
    const s = getComputedStyle(f), box = f.getBoundingClientRect();
    if (Math.abs(box.height - controlH) > 0.5) bad.push(`${name(f)} is ${box.height}px tall`);
    if (s.backgroundColor !== field.bg) bad.push(`${name(f)} fill ${s.backgroundColor}`);
    if (s.borderTopColor !== field.border) bad.push(`${name(f)} border ${s.borderTopColor}`);
    if (Math.abs(parseFloat(s.borderTopLeftRadius) - radius) > 0.5) bad.push(`${name(f)} radius ${s.borderTopLeftRadius}`);
    inside(f, box);
  }
  return {
    buttons: buttons.map((b) => b.textContent.trim()),
    selects: fields.filter((f) => f.tagName === 'SELECT').length,
    inputs: fields.filter((f) => f.tagName === 'INPUT').length,
    bad,
  };
}"""


@pytest.mark.e2e
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_dialog_controls_are_styled(page, theme):
    """#207: every dialog's dropdowns and Save/Cancel buttons render on the
    vendored select-native / button recipes in both themes, on a desktop and
    at phone width. modal.css no longer styles them itself, so a modal
    re-vendor without its two companions leaves them as bare UA controls."""
    page.evaluate("t => { localStorage.setItem('grocery.theme', t); document.documentElement.dataset.theme = t; }", theme)

    def check(dialog_id: str) -> dict:
        found = {}
        for width, height in ((1100, 950), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(150)
            found = page.evaluate(_DIALOG_CONTROLS_PROBE, dialog_id)
            assert not found["bad"], f"#{dialog_id} at {width}px ({theme}): {found['bad']}"
        page.set_viewport_size({"width": 1100, "height": 950})
        return found

    # Login is static markup; the fixture server has auth off, so open it directly.
    page.evaluate("document.getElementById('login-dialog').showModal()")
    assert check("login-dialog")["buttons"] == ["Unlock"]
    page.evaluate("document.getElementById('login-dialog').close()")

    goto_mode(page, "stores")
    page.click("[data-stores-action='import']")
    page.wait_for_selector("#stores-sim[data-state='ready']")

    page.click("[data-stores-action='set-baseline']")
    page.locator("#stores-baseline-dialog").wait_for()
    assert check("stores-baseline-dialog")["buttons"] == ["Set baseline"]
    page.locator("#stores-baseline-dialog [data-dialog-close]").click()

    burger = page.locator(".store-row", has_text="burguer ternera")
    burger.locator("[data-stores-action='detail']").click()
    detail = page.locator("#stores-detail-dialog")
    detail.locator(".review-qty-lines").wait_for()
    detail.locator("[data-review-edit]").first.click()
    detail.locator("[data-review-form]").wait_for()
    found = check("stores-detail-dialog")
    assert {"Save", "Cancel", "Save target & stock"} <= set(found["buttons"]), found
    assert found["selects"] >= 1, found
    detail.locator("[data-dialog-close]").click()

    pick = burger.locator("[data-stores-pick]")
    other = pick.evaluate("s => [...s.options].map((o) => o.value).find((v) => v && v !== s.value)")
    pick.select_option(other)
    page.click("[data-stores-action='review']")
    page.locator("#stores-apply-dialog .apply-row").first.wait_for()
    found = check("stores-apply-dialog")
    assert len(found["buttons"]) == 1 and found["buttons"][0].startswith("Apply") and found["inputs"] >= 2, found
    page.locator("#stores-apply-dialog [data-dialog-close]").click()
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_add_item_product_search(page, server):
    """Items -> Add Item carries the product search (#182; it was the Search
    tab). The store search is stubbed at the HTTP seam (no Chrome): a new
    term's "Use" pre-fills the Add Item form and nothing is saved until Add
    Item; a term already on the list keeps its confirm row -> /select.
    Last in the module on purpose: it adds a row to the shared fixture list."""
    goto_mode(page, "add")
    search = page.locator("#pane-items #product-search")
    search.locator("#search-term").wait_for()
    assert search.locator("#search-run").is_visible() and search.locator("#search-record").is_visible()
    assert search.locator(".card-title").inner_text().strip() == "Find a store product"

    inventory = f"{server.url}/api/inventory"
    existing = next(i for i in page.request.get(inventory).json()["items"] if i["buscador"] and i["cantidad"] > 0)
    new_url = "https://www.carrefour.es/supermercado/e2e-sandia/R-e2e/p"
    status = {"id": "e2e", "state": "done", "elapsed_s": 1.0, "error": None, "progress": None, "items": [
        {"term": "zzz sandia e2e", "inventory_idx": None, "existing_super": "",
         "store_errors": {"mercadona": "Timeout 20000ms exceeded"},
         # Per-store lines (#211): a failed store reads as failed, not "no results".
         "stores": [{"store": "mercadona", "state": "failed", "count": 0, "reason": "timeout",
                     "error": "Timeout 20000ms exceeded", "elapsed_s": 20.0},
                    {"store": "carrefour", "state": "done", "count": 1, "reason": None,
                     "error": None, "elapsed_s": 6.0}],
         "candidates": [{"store": "carrefour", "name": "Sandia e2e", "product_url": new_url,
                         "price_text": "3,00 EUR", "thumbnail": "", "match": "strong"}]},
        # Same store + link as the row already holds, so /select rewrites it unchanged.
        {"term": existing["comida"], "inventory_idx": existing["id"], "existing_super": existing["super"],
         "inventory_name": "zzz fila existente e2e",
         "store_errors": {}, "candidates": [{"store": existing["super"], "name": "Existing e2e",
                                              "product_url": existing["buscador"], "price_text": "",
                                              "thumbnail": "", "match": ""}]},
    ]}
    for path in ("start", "status"):
        page.route(f"**/api/product-search/{path}", lambda route: route.fulfill(json=status))
    search.locator("#search-term").fill("sandia")
    search.locator("#search-run").click()
    new_card = page.locator(".candidate", has_text="Sandia e2e")
    new_card.wait_for()
    lines = page.locator(".search-store-states li").all_inner_texts()
    assert "Mercadona: couldn't search — the site was too slow" in lines
    assert "Carrefour: 1 result" in lines
    assert "Timeout" not in " ".join(lines)  # failure copy is sanitized
    assert "Ametller" not in search.locator(".hint").inner_text()

    new_card.locator("[data-action='search-use']").click()
    form = page.locator("#add-form")
    assert form.locator("[name='comida']").input_value() == "zzz sandia e2e"
    assert form.locator("[name='super']").input_value() == "carrefour"
    assert form.locator("[name='buscador']").input_value() == new_url
    assert form.locator("[name='cantidad']").input_value() == "1"
    assert not [i for i in page.request.get(inventory).json()["items"] if i["comida"] == "zzz sandia e2e"]
    form.locator("[name='lugar']").fill("nevera")
    form.locator("button[type='submit']").click()
    new_card.locator("button:has-text('Added')").wait_for()
    created = [i for i in page.request.get(inventory).json()["items"] if i["comida"] == "zzz sandia e2e"]
    assert [(i["super"], i["buscador"], i["cantidad"]) for i in created] == [("carrefour", new_url, 1)]

    # The header names the exact term searched; a matched row is only a hint (#214).
    heads = page.locator(".search-group-term").all_inner_texts()
    assert heads == ["Results for “zzz sandia e2e”", f"Results for “{existing['comida']}”"]
    assert "Matches your item zzz fila existente e2e" in page.locator(".search-group-hint").inner_text()

    old_card = page.locator(".candidate", has_text="Existing e2e")
    old_card.locator("[data-action='search-use']").click()
    old_card.locator("[data-action='search-add-new']").click()  # → Add Item form, under the searched term
    assert form.locator("[name='comida']").input_value() == existing["comida"]
    assert form.locator("[name='buscador']").input_value() == existing["buscador"]
    old_card.locator("[data-action='search-use']").click()
    with page.expect_response(lambda r: r.url.endswith("/api/product-search/select")) as resp:
        old_card.locator("[data-action='search-confirm']").click()
    assert resp.value.ok and resp.value.request.post_data_json["inventory_idx"] == existing["id"]
    old_card.locator("button:has-text('Updated')").wait_for()
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
@pytest.mark.live
@pytest.mark.skipif(not LIVE, reason="set GROCERY_E2E_LIVE=1 and run the hub to exercise the real LLM")
def test_audio_match_live_hub(page):
    goto_mode(page, "audio")
    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    # Real hub call — proves no premature timeout (budget up to 10 min).
    page.wait_for_selector("text=Detected items", timeout=600000)
    assert page.locator("#audio-status.ok").count() >= 1


@pytest.mark.e2e
def test_settings_gear_opens_settings_from_every_tab(page):
    """#200: Settings is never a tab -- the gear beside the theme toggle opens it
    from every tab, no nav tab is selected while it is open, and a tap on a tab
    (or on the gear again) comes back."""
    for tab in ("inventory", "shopping", "audit", "items"):
        page.click(f"[data-tab='{tab}']")
        page.locator(f"#pane-{tab} .home-settings").click()
        assert page.locator("#pane-settings").is_visible()
        assert page.locator(f"#pane-{tab}").is_hidden()
        assert page.locator("#pane-settings #email-monitor").count() == 1
        assert page.locator("nav.tabs .tab.active").count() == 0
        # The gear on Settings' own header returns to the tab it was opened from.
        page.locator("#pane-settings .home-settings").click()
        assert page.locator(f"#pane-{tab}").is_visible()
        assert page.locator("#pane-settings").is_hidden()
        assert page.locator(f"nav.tabs [data-tab='{tab}']").get_attribute("aria-selected") == "true"
    page.locator("#pane-items .home-settings").click()
    page.click("[data-tab='shopping']")
    assert page.locator("#pane-shopping").is_visible()
    assert page.locator("#pane-settings").is_hidden()
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


# What every switch that is on must draw: the theme's accent-fill (#200,
# design.md switch.trackOn) -- resolved from the token, never hard-coded.
_ACCENT_PROBE = """() => {
  const probe = document.createElement('div');
  probe.style.background = 'var(--accent-fill)';
  document.body.appendChild(probe);
  const accent = getComputedStyle(probe).backgroundColor;
  probe.remove();
  const on = [...document.querySelectorAll('[role=switch][aria-checked=true]')]
    .map((sw) => getComputedStyle(sw).backgroundColor);
  return { accent, on };
}"""


@pytest.mark.e2e
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_switch_on_track_is_the_accent(page, theme):
    """#200: an on switch is the accent blue in both themes, never success green."""
    page.evaluate("t => { localStorage.setItem('grocery.theme', t); document.documentElement.dataset.theme = t; }", theme)
    goto_mode(page, "shopping")
    dry_run = page.locator("#automation-dry-run")
    dry_run.wait_for()
    if dry_run.get_attribute("aria-checked") != "true":
        dry_run.click()  # client-side only: it just rewrites the command preview
    page.wait_for_timeout(400)  # the track's 0.15s background transition must settle
    probe = page.evaluate(_ACCENT_PROBE)
    assert probe["on"], "no switch is on to measure"
    assert set(probe["on"]) == {probe["accent"]}, probe


# Computed font-size (px) of every visible text-bearing element in the visible
# pane, in DOM order -- identical DOM at every size, so the lists line up.
_TEXT_PROBE = """() => {
  const pane = [...document.querySelectorAll('main.app > .pane')].find((p) => !p.hidden);
  const out = [];
  for (const node of pane.querySelectorAll('*')) {
    if (!node.getClientRects().length) continue;
    const own = [...node.childNodes].some((n) => n.nodeType === 3 && n.textContent.trim());
    if (!own) continue;
    out.push([node.tagName.toLowerCase() + '.' + node.className, parseFloat(getComputedStyle(node).fontSize)]);
  }
  return out;
}"""

_ALL_MODES = ["dashboard", "shopping", "audit", "audio", "targets", "edit", "add", "stores", "settings"]


def _sample_view_at(page, mode, size):
    """The view's text sizes with <html data-textsize> stamped to `size` -- the
    exact attribute the control sets -- so both samples come from one visit and
    the same DOM (a second visit could differ in zone / open state)."""
    goto_mode(page, mode)
    page.wait_for_timeout(250)
    page.evaluate("s => { document.documentElement.dataset.textsize = s; }", size)
    return page.evaluate(_TEXT_PROBE)


@pytest.mark.e2e
def test_text_size_scales_every_view(page):
    """#200 / fleet-config#1211: the vendored Small / Default / Large control in
    Settings scales the text of every view. Proven with a computed-style probe:
    the root font-size steps 15 / 16 / 18px and, per view, every text element
    drawn in rem grows by the Large step (geometry stays px by design), and the
    choice survives a reload."""
    root_px = lambda: page.evaluate("parseFloat(getComputedStyle(document.documentElement).fontSize)")  # noqa: E731
    assert page.evaluate("document.documentElement.dataset.textsize") == "default"
    assert root_px() == 16
    goto_mode(page, "settings")
    control = page.locator("#pane-settings #textSizeControl")
    assert control.locator("[data-textsize]").count() == 3
    assert control.locator("[data-textsize='default']").get_attribute("aria-pressed") == "true"
    control.locator("[data-textsize='small']").click()
    assert root_px() == 15
    control.locator("[data-textsize='large']").click()
    assert root_px() == 18
    assert control.locator("[data-textsize='large']").get_attribute("aria-pressed") == "true"
    assert page.evaluate("localStorage.getItem('grocery.textsize')") == "large"

    for mode in _ALL_MODES:
        large = _sample_view_at(page, mode, "large")
        default = _sample_view_at(page, mode, "default")
        page.evaluate("document.documentElement.dataset.textsize = 'large'")
        before, after = default, large
        assert before, f"{mode}: nothing to measure"
        assert [name for name, _ in before] == [name for name, _ in after], f"{mode}: DOM differs between sizes"
        grown = [name for (name, a), (_, b) in zip(before, after) if b > a]
        shrunk = [name for (name, a), (_, b) in zip(before, after) if b < a]
        fixed = sorted({name for (name, a), (_, b) in zip(before, after) if b == a})
        assert not shrunk, f"{mode}: text shrank at Large: {shrunk}"
        assert len(grown) / len(before) >= 0.9, f"{mode}: only {len(grown)}/{len(before)} elements grew; fixed: {fixed}"

    page.reload()
    page.wait_for_selector("[data-tab='audit']")
    assert page.evaluate("document.documentElement.dataset.textsize") == "large"
    assert root_px() == 18
    # restore, so a later test sharing this origin starts at Default
    goto_mode(page, "settings")
    page.locator("#pane-settings [data-textsize='default']").click()
    assert root_px() == 16


# --- design-review judgment copy (#153 J-07 / J-08 / J-09) -------------------

_VISIBLE_COPY_JS = """
() => {
  const pane = [...document.querySelectorAll('main > section.pane')].find((p) => !p.hidden);
  const options = [...pane.querySelectorAll('option')].map((o) => o.textContent);
  return { text: pane.innerText, options };
}
"""
# Words a shopper would not use. The two sibling apps the user must start are
# named by their folder names, so those are masked before matching.
_DEV_TERMS = (
    r"\b(LLM|hub|whisper|tray|SSE|endpoint|argv|JSON|API|snake_case|Command)\b"
    r"|:\d{4}\b|\b[a-z]+_[a-z]+\b"
)


@pytest.mark.e2e
def test_visible_copy_has_no_developer_terms(page):
    """J-07: no internal identifier, port or developer vocabulary in what a
    shopper reads, on any view — the Audio audit's service status included
    (the e2e server has no voice recorder / hub / whisper, so its error banner
    is on screen)."""
    import re

    offenders = {}
    for mode in _ALL_MODES:
        goto_mode(page, mode)
        page.wait_for_timeout(400)
        copy = page.evaluate(_VISIBLE_COPY_JS)
        text = "\n".join([copy["text"], *copy["options"]])
        text = text.replace("local-llm-hub", "").replace("voice-transcriber", "")
        found = sorted({m.group(0) for m in re.finditer(_DEV_TERMS, text)})
        if found:
            offenders[mode] = found
    assert not offenders, f"developer terms visible: {offenders}"


_CASE_JS = r"""
() => [...document.querySelectorAll('main > section.pane:not([hidden]) button, main > section.pane:not([hidden]) summary, main > section.pane:not([hidden]) h1, main > section.pane:not([hidden]) h2')]
  .filter((e) => e.getClientRects().length)
  .map((e) => e.textContent.replace(/\s+/g, ' ').trim())
  .filter(Boolean)
"""
_PROPER = {"Ametller", "Origen", "Mercadona", "Carrefour", "CSV"}


@pytest.mark.e2e
def test_labels_are_sentence_case(page):
    """J-08: buttons, summaries and headings use sentence case (first word
    capitalised, the rest lower) — store names and CSV aside — on every view."""
    mixed = {}
    for mode in _ALL_MODES:
        goto_mode(page, mode)
        page.wait_for_timeout(300)
        for label in page.evaluate(_CASE_JS):
            words = [w for w in label.split(" ")[1:] if w and w[0].isalpha()]
            bad = [w for w in words if w[0].isupper() and w not in _PROPER and not w.isupper()]
            if bad:
                mixed.setdefault(mode, []).append(label)
    assert not mixed, f"Title Case labels: {mixed}"


@pytest.mark.e2e
def test_empty_automation_log_says_what_fills_it(page):
    """J-09: the idle Fill carts log names the control that fills it."""
    goto_mode(page, "shopping")
    log = page.locator("#automation-log")
    log.wait_for(state="attached")
    text = log.inner_text()
    assert "Run automation" in text and "progress appears here" in text


_FIRST_AFTER_HEADER_JS = r"""
(tab) => {
  const pane = document.querySelector(`#pane-${tab}`);
  const head = pane.querySelector('.home-head');
  const seen = [...pane.querySelectorAll('h1,h2,h3,h4,h5,h6,button,input,select,textarea,summary,[role="tab"]')]
    .filter((e) => !head.contains(e) && e.getClientRects().length);
  const first = seen[0];
  return first ? `${first.tagName.toLowerCase()}:${first.textContent.trim().slice(0, 30)}` : null;
}
"""


@pytest.mark.e2e
@pytest.mark.parametrize("mode,tab", [("dashboard", "inventory"), ("shopping", "shopping"), ("audit", "audit"), ("targets", "items")])
def test_every_tab_opens_on_a_heading_not_a_control(page, mode, tab):
    """J-04: below the page header, a tab's first heading-or-control in reading
    order is a heading — never a mode pill, the search box or a collapsible
    card header (a heading inside a <summary> reads as a control)."""
    goto_mode(page, mode)
    page.wait_for_timeout(300)
    first = page.evaluate(_FIRST_AFTER_HEADER_JS, tab)
    assert first and first.startswith("h2:"), f"{tab} opens on {first}"
