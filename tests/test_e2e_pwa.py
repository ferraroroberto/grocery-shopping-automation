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

# The 9 modes group into the fleet nav's 5 tabs (mirrors MODE_TO_TAB in
# modules/core.js); grouped modes are reached via a sub-pill inside their tab's
# pane. Product search lives in Items -> Add Item and cart automation ("Fill
# carts") at the bottom of Shop since the Search/Auto tabs were retired (#182).
TAB_FOR_MODE = {
    "dashboard": "inventory",
    "shopping": "shopping",
    "audit": "audit",
    "audio": "audit",
    "targets": "items",
    "edit": "items",
    "add": "items",
    "stores": "items",
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
def test_five_tab_nav_and_relocated_sections(page, pw, server):
    """#182: five tabs, and every capability of the retired Search/Auto tabs in
    reach -- product search in Items -> Add Item, Fill carts at the bottom of
    Shop, Email Watch in Setup -- with a saved retired tab reopening on its new
    home instead of falling back to Home. Then the WebKit 390px nav check."""
    tabs = page.locator("nav.tabs .tab")
    assert tabs.count() == 5
    assert tabs.locator(".tab-label").all_inner_texts() == ["Home", "Shop", "Audit", "Items", "Setup"]

    # J-04 (#153): every pane opens on its own page header, not a bare control
    # row -- one shared markup shape (icon + tab-naming title + context line +
    # theme toggle) as each pane's first element, and only the active pane's
    # is ever visible at once.
    header_titles = {
        "pane-inventory": "Home", "pane-shopping": "Shop", "pane-audit": "Audit",
        "pane-items": "Items", "pane-settings": "Setup",
    }
    for pane_id, title in header_titles.items():
        header = page.locator(f"#{pane_id} > :first-child")
        assert "page-header" in (header.get_attribute("class") or ""), f"{pane_id}'s first child isn't the page header"
        assert header.locator(".card-title").inner_text().strip() == title
    assert page.locator(".page-header").count() == 5
    assert page.locator(".page-header:visible").count() == 1

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

    # WebKit (iPhone, 390px): five tabs with full labels, 44px targets, no sideways scroll.
    webkit = pw.webkit.launch()
    try:
        phone = webkit.new_page(**pw.devices["iPhone 13"])
        errors: list[str] = []
        phone.on("pageerror", lambda exc: errors.append(str(exc)))
        phone.goto(server.url)
        phone.wait_for_function("document.querySelector('#status')?.textContent?.includes('Loaded')")
        assert phone.viewport_size["width"] == 390
        boxes = [phone.locator("nav.tabs .tab").nth(i).bounding_box() for i in range(5)]
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
def test_add_item_increases_count(page):
    goto_mode(page, "dashboard")
    page.wait_for_selector(".summary")
    before = int(page.locator(".summary-value").first.inner_text())
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
    new_url = "https://www.compraonline.ametller.cat/e2e-sandia.html"
    status = {"id": "e2e", "state": "done", "elapsed_s": 1.0, "error": None, "progress": None, "items": [
        {"term": "zzz sandia e2e", "inventory_idx": None, "existing_super": "", "store_errors": {},
         "candidates": [{"store": "ametller", "name": "Sandia e2e", "product_url": new_url,
                         "price_text": "3,00 EUR", "thumbnail": "", "match": "strong"}]},
        # Same store + link as the row already holds, so /select rewrites it unchanged.
        {"term": existing["comida"], "inventory_idx": existing["id"], "existing_super": existing["super"],
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

    new_card.locator("[data-action='search-use']").click()
    form = page.locator("#add-form")
    assert form.locator("[name='comida']").input_value() == "zzz sandia e2e"
    assert form.locator("[name='super']").input_value() == "ametller"
    assert form.locator("[name='buscador']").input_value() == new_url
    assert form.locator("[name='cantidad']").input_value() == "1"
    assert not [i for i in page.request.get(inventory).json()["items"] if i["comida"] == "zzz sandia e2e"]
    form.locator("[name='lugar']").fill("nevera")
    form.locator("button[type='submit']").click()
    new_card.locator("button:has-text('Added')").wait_for()
    created = [i for i in page.request.get(inventory).json()["items"] if i["comida"] == "zzz sandia e2e"]
    assert [(i["super"], i["buscador"], i["cantidad"]) for i in created] == [("ametller", new_url, 1)]

    old_card = page.locator(".candidate", has_text="Existing e2e")
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
