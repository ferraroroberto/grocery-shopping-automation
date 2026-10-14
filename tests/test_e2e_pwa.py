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
import app.routers.system as system_router
import automation.product_options as product_options
import src.data as data
from app.api import app
from src.inventory_extract import ExtractionResult

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import sync_playwright  # noqa: E402
from tests.e2e._geometry import assert_min_target, assert_no_horizontal_overflow, assert_no_overlap  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "list_test_fixture.xlsx"
# Invented benchmark run the Items → Stores view prices from (#148).
BENCHMARK_RUNS = REPO_ROOT / "tests" / "fixtures" / "store_links"
LIVE = os.environ.get("GROCERY_E2E_LIVE") == "1"
TRANSCRIPT = "en la nevera, tengo dos yogures y un litro de leche. en el congelador, tres salmones."

# 6 of the 7 modes group into the fleet nav's 4 tabs (mirrors MODE_TO_TAB in
# modules/core.js); grouped modes are reached via a segment inside their tab's
# pane -- Items is the item list ("list") and Prices ("stores", #256). Product
# search lives in the list's Add item sheet and cart automation ("Fill carts")
# at the bottom of Shop since the Search/Auto tabs were retired (#182).
# Settings is the 7th and never a tab (#200): the header gear opens it.
TAB_FOR_MODE = {
    "dashboard": "inventory",
    "shopping": "shopping",
    "audit": "audit",
    "audio": "audit",
    "list": "items",
    "stores": "items",
}

# Home's This week card (#260) is painted once the inventory has loaded.
LOADED_JS = "document.querySelector('#this-week')?.dataset.state === 'ready'"
_HOME_FITS_JS = """() => {
  const search = document.querySelector('#search').getBoundingClientRect();
  const nav = document.querySelector('nav.tabs').getBoundingClientRect();
  return search.bottom <= (nav.top > search.top ? nav.top : innerHeight);
}"""
# Where Home's blocks sit, for a failed one-screen check.
_HOME_RECTS_JS = """() => Object.fromEntries(['#pane-inventory .home-head', '#this-week', '#search', 'nav.tabs', '.page-foot']
  .map((s) => { const r = document.querySelector(s).getBoundingClientRect(); return [s, [Math.round(r.top), Math.round(r.bottom)]]; })
  .concat([['scrollY', [scrollY, document.querySelector('main.app').scrollTop]]]))"""


def goto_mode(page, mode: str) -> None:
    """Navigate to a mode: click its nav tab, then its segment when grouped.
    Settings has no tab -- it opens from the gear in the visible page header."""
    if mode == "settings":
        if not page.locator("#pane-settings").is_visible():
            page.locator(".home-settings:visible").click()
        page.wait_for_timeout(100)
        return
    page.click(f"[data-tab='{TAB_FOR_MODE[mode]}']")
    segment = page.locator(f".subnav [data-mode='{mode}']")
    if segment.count():
        segment.click()
    page.wait_for_timeout(100)


def open_add_sheet(page):
    """Items' list -> the Add item button -> the Add item sheet (#256)."""
    goto_mode(page, "list")
    page.click("#items-add")
    sheet = page.locator("#add-sheet")
    sheet.wait_for()
    return sheet


def open_benchmark_sheet(page):
    """Items -> Prices -> the glance card's Benchmark row -> its sheet (#257)."""
    if not page.locator("#pane-items .stores-glance").is_visible():
        goto_mode(page, "stores")
    page.click("[data-stores-action='benchmark']")
    sheet = page.locator("#stores-benchmark-sheet")
    sheet.wait_for()
    return sheet


def open_fill_options(page):
    """Shop -> the carts glance's Options row -> the Fill carts options sheet (#258)."""
    if not page.locator("#pane-shopping #fill-carts").is_visible():
        goto_mode(page, "shopping")
    page.click("#automation-options")
    sheet = page.locator("#fill-carts-options")
    sheet.wait_for()
    return sheet


def open_store_sheet(page, store: str = "mercadona"):
    """Shop -> a store group -> its Store options row -> the store sheet (#258)."""
    if not page.locator("#pane-shopping").is_visible():
        goto_mode(page, "shopping")
    group = page.locator(f"#pane-shopping details[data-store='{store}']")
    if group.get_attribute("open") is None:
        group.locator("> summary").click()
    group.locator("[data-store-sheet]").click()
    sheet = page.locator("#store-sheet")
    sheet.wait_for()
    return sheet


def open_transcript_menu(page):
    """Audit -> Voice -> the transcript card's menu -> its sheet (#259)."""
    if not page.locator("#pane-audit #voice-glance").is_visible():
        goto_mode(page, "audio")
    page.click("#audio-menu")
    sheet = page.locator("#transcript-sheet")
    sheet.wait_for()
    return sheet


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


# A mention no row took, as speech says it: Manual's word search (#259) finds
# the fixture's "pulpo cocido" from it.
UNMATCHED_PHRASE = "pulpo a la gallega"


def _stub_extract(transcript, df, *, base_url, model, max_tokens, timeout):
    first = int(df.index[0])
    return ExtractionResult(
        items=[{"idx": first, "count": 2, "zone": str(df.loc[first, "lugar"]), "evidence": "dos"}],
        zones_mentioned=[str(df.loc[first, "lugar"])],
        unmatched_mentions=[{"phrase": UNMATCHED_PHRASE, "idx": None, "approx_count": None, "note": "no match",
                             "comida": "", "resolved_by": "", "match_score": None}],
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


@pytest.mark.e2e
def test_all_tabs_render_without_js_errors(page):
    for mode in ["dashboard", "audit", "list", "stores", "shopping", "audio", "settings"]:
        goto_mode(page, mode)
        page.wait_for_timeout(150)
    open_add_sheet(page)
    page.keyboard.press("Escape")
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
    tabs in reach -- product search in Items' Add item sheet, Fill carts at
    the bottom of Shop, Email Watch in Settings (the header gear, never a tab)
    -- with a saved retired tab reopening on its new home instead of falling
    back to Home. #256: Items is two segments, and a saved retired Items mode
    (Targets / Edit item / Add item) reopens on the Items list, never an empty
    pane. Then the WebKit 390px nav check."""
    tabs = page.locator("nav.tabs .tab")
    assert tabs.count() == 4
    # A label may also carry the badge's spoken count (", 1 not in cart", #258).
    assert [t.split(",")[0] for t in tabs.locator(".tab-label").all_inner_texts()] == ["Home", "Shop", "Audit", "Items"]
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

    goto_mode(page, "list")
    segments = page.locator("#pane-items .segmented [data-mode]")
    assert segments.all_inner_texts() == ["Items", "Prices"]
    assert segments.first.get_attribute("aria-pressed") == "true"
    assert page.locator("#pane-items .item-group").count() >= 2  # one group per zone
    sheet = open_add_sheet(page)
    assert sheet.locator("#product-search #search-term").is_visible()
    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")

    # #259: Audit opens on Voice (#252 decision 7), Voice leading its two
    # segments; a hop to Manual lasts the session, never the next launch.
    page.click("[data-tab='audit']")
    audit_segments = page.locator("#pane-audit .segmented [data-mode]")
    assert audit_segments.all_inner_texts() == ["Voice", "Manual"]
    assert audit_segments.first.get_attribute("aria-pressed") == "true"
    page.locator("#pane-audit #voice-glance").wait_for()
    audit_segments.nth(1).click()
    page.click("[data-tab='items']")
    page.click("[data-tab='audit']")
    assert audit_segments.nth(1).get_attribute("aria-pressed") == "true"
    assert page.evaluate("() => localStorage.getItem('grocery.sub.audit')") is None

    # #258: the last fill left an item out, and Shop hasn't been seen since --
    # the Shop tab carries the count badge, spoken as part of its name.
    shop_tab = page.locator("nav.tabs [data-tab='shopping']")
    shop_tab.locator(".tab-badge").wait_for()
    assert shop_tab.locator(".tab-badge").inner_text() == "1"
    assert "Shop, 1 not in cart" in shop_tab.evaluate("t => t.textContent.replace(/\\s+/g, ' ')")

    goto_mode(page, "shopping")
    fill = page.locator("#pane-shopping #fill-carts")
    fill.wait_for()
    # The carts glance leads Shop: first card under the header, one row per
    # store on the list by display name, the miss as a danger chip, the one
    # Fill carts button and the Options row. Seeing Shop clears the badge.
    assert page.locator("#pane-shopping > :nth-child(2)").get_attribute("id") == "fill-carts-host"
    rows = fill.locator("#carts-stores .action-row")
    assert rows.locator(".action-row-title").all_inner_texts() == ["Ametller Origen", "Mercadona"]
    assert rows.nth(1).locator(".chip[data-tone='danger']").inner_text() == "1 not in cart"
    assert "1 in cart" in rows.nth(1).locator(".action-row-meta").inner_text()
    assert fill.locator("#carts-last-fill").inner_text() == "Last fill Tue 6 Oct"
    assert page.locator("#shop-context").inner_text().startswith("1 not in cart")
    assert fill.locator("#automation-start").inner_text().strip() == "Fill carts"
    assert shop_tab.locator(".tab-badge").count() == 0
    # Options in a sheet: every pick still there, "what will run" in words --
    # never the interpreter command line or a lower-case store key.
    sheet = open_fill_options(page)
    for control in ("#automation-store", "#automation-cart-mode", "#automation-dry-run"):
        assert sheet.locator(control).is_visible(), f"{control} missing from the options"
    assert sheet.locator("#automation-store option").all_inner_texts() == ["All stores", "Ametller Origen", "Mercadona"]
    plan = sheet.locator("#automation-plan").inner_text()
    assert plan.startswith("Adds 41 items to the Ametller Origen and Mercadona carts"), plan
    assert "python" not in sheet.inner_text().lower() and page.locator("#automation-command").count() == 0
    # Clean cart: Fill carts waits for the confirm, which the sheet asks for.
    sheet.locator("#automation-cart-mode").select_option("clean")
    sheet.locator("#automation-clean-confirm").wait_for()
    assert sheet.locator("#automation-plan").inner_text().startswith("Empties the")
    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")
    assert fill.locator("#automation-start").is_disabled()
    assert fill.locator("#automation-blocked").is_visible()
    assert fill.locator("#automation-options-value").inner_text() == "All stores · Clean cart"
    # A shopping-list re-render (Got it) must not reset the run picks.
    group = page.locator("#pane-shopping details[data-store='mercadona']")
    assert group.locator(".collapse-title").inner_text() == "Mercadona"
    group.locator("> summary").click()
    group.locator("[data-action='mark-buy']").first.click()
    group.locator("[data-action='undo-buy']").first.wait_for()
    # Got it is the row's leading check (#254): pressed, and the name struck.
    got = group.locator(".item-row", has=page.locator("[data-action='undo-buy']")).first
    assert got.locator(".action-row-check").get_attribute("aria-pressed") == "true"
    assert got.locator(".action-row-title s").count() == 1
    assert fill.locator("#automation-options-value").inner_text() == "All stores · Clean cart"
    assert open_fill_options(page).locator("#automation-cart-mode").input_value() == "clean"
    page.keyboard.press("Escape")
    # Quick-add is the group's "Add to <store>" row; its Remove, the cart
    # offsets and Unmark all live in the store sheet (#252 decision 6).
    group.locator(".store-add-row input[name='name']").fill("Invented extra")
    group.locator(".store-add-row button[type='submit']").click()
    extra = group.locator(".item-row", has_text="Invented extra")
    extra.wait_for()
    assert extra.locator("[data-action='remove-extra']").count() == 0
    store_sheet = open_store_sheet(page)
    assert store_sheet.locator("#store-sheet-title").inner_text() == "Mercadona"
    store_sheet.locator("[data-action='offset-items']").fill("2")
    store_sheet.locator("[data-action='offset-items']").dispatch_event("change")
    assert group.locator("[data-store-sheet] .sheet-link-value").inner_text() == "2 already in cart"
    store_sheet.locator("[data-action='remove-extra']").click()
    extra.wait_for(state="detached")
    store_sheet.locator("[data-store-unmark]").click()
    group.locator("[data-action='undo-buy']").wait_for(state="detached")
    page.keyboard.press("Escape")

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
    page.locator("#pane-settings #open-email-watch").wait_for()

    # A PWA last closed on a retired tab reopens on that tab's new home.
    list_segment = page.locator("#pane-items .subnav [data-mode='list']")
    # A saved retired Items mode lands on the Items list, and is rewritten.
    for old in ("targets", "edit", "add"):
        page.evaluate("m => { localStorage.setItem('grocery.tab', 'items'); localStorage.setItem('grocery.sub.items', m); }", old)
        page.reload()
        page.locator("#pane-items .item-group .item-row").first.wait_for()
        assert list_segment.get_attribute("aria-pressed") == "true", old
        assert page.evaluate("() => localStorage.getItem('grocery.sub.items')") == "list", old
    for saved, tab, landing in (("search", "items", "#pane-items #items-add"),
                                ("automation", "shopping", "#pane-shopping #fill-carts")):
        page.evaluate("t => localStorage.setItem('grocery.tab', t)", saved)
        page.reload()
        page.locator(landing).wait_for()
        assert page.locator("nav.tabs").get_attribute("data-active-tab") == tab
        assert page.evaluate("() => localStorage.getItem('grocery.tab')") == tab
        if saved == "search":
            assert list_segment.get_attribute("aria-pressed") == "true"
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
        phone.wait_for_function(LOADED_JS)
        assert phone.viewport_size["width"] == 390
        boxes = [phone.locator("nav.tabs .tab").nth(i).bounding_box() for i in range(4)]
        assert all(b["x"] >= 0 and b["x"] + b["width"] <= 390 for b in boxes), boxes
        assert all(b["width"] >= 44 and b["height"] >= 44 for b in boxes), boxes
        assert phone.locator("nav.tabs .tab-label").evaluate_all(
            "els => els.every(e => e.scrollWidth <= e.clientWidth)"), "a tab label is truncated at 390px"
        assert phone.evaluate("() => document.documentElement.scrollWidth <= innerWidth")
        # #260: Home -- the This week card and the search -- fits above the pill.
        assert phone.evaluate(_HOME_FITS_JS), phone.evaluate(_HOME_RECTS_JS)
        assert errors == [], f"WebKit JS errors: {errors}"
    finally:
        webkit.close()


@pytest.mark.e2e
def test_home_this_week_and_search(page, server):
    """#260: Home is the This week card -- one row each for the audit, the
    list, the carts and the prices, each opening the tab that owns it -- and a
    search over every item. The card's rows are all there from the first
    paint and later reads fill them in place, so the card never changes size."""
    rows = page.locator("#this-week .this-week-rows .action-row")
    assert rows.locator(".action-row-title").all_inner_texts() == ["Audit", "Shopping list", "Carts", "Prices"]
    height = page.locator("#this-week").bounding_box()["height"]
    page.wait_for_function(
        "['audit', 'carts', 'prices'].every((k) => document.querySelector(`#week-${k}`).textContent !== '–')")
    assert page.locator("#this-week").bounding_box()["height"] == height
    summary = page.request.get(f"{server.url}/api/inventory").json()["summary"]
    assert page.locator("#week-list").inner_text() == f"{summary['shopping_items']} items · {summary['shopping_units']} units"
    # The invented last fill left one item out: the Carts row and the header say so.
    assert page.locator("#week-carts").inner_text() == "1 not in cart"
    assert page.locator("#week-carts").get_attribute("data-tone") == "danger"
    assert page.locator("#status").inner_text() == "1 not in cart"
    assert page.locator("#week-audit").inner_text().startswith(("No audit yet", "Last audit "))
    assert page.locator("#week-prices").inner_text().startswith(("Next review ", "Review due"))
    # Nothing under the search until something is typed.
    assert page.locator("#pane-inventory .item-row").count() == 0
    page.fill("#search", "burguer")
    titles = page.locator("#pane-inventory .item-row .action-row-title").all_inner_texts()
    assert titles and all("burguer" in t.lower() for t in titles)
    page.fill("#search", "zzz nothing matches")
    assert page.locator("#pane-inventory .empty-state").inner_text().strip() == "No matching items."
    page.fill("#search", "")
    assert page.locator("#pane-inventory .item-row, #pane-inventory .empty-state").count() == 0
    # Each row opens the tab that owns its fact, on the segment that shows it.
    for key, pane, segment in (("audit", "audit", "audio"), ("list", "shopping", None),
                               ("carts", "shopping", None), ("prices", "items", "stores")):
        page.click("[data-tab='inventory']")
        page.locator(f"#week-{key}").click()
        assert page.locator(f"#pane-{pane}").is_visible(), key
        if segment:
            assert page.locator(f"#pane-{pane} [data-mode='{segment}']").get_attribute("aria-pressed") == "true", key
    # Build identity footer (home-automation contract) is filled at boot.
    page.wait_for_function("document.querySelector('#build-readout')?.textContent?.startsWith('Build:')")
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_search_only_on_filterable_modes(page):
    goto_mode(page, "dashboard")
    assert page.locator("#toolbar").is_visible()
    assert not page.locator("#items-add").is_visible()
    # Items' list (#256): the search filters the one zone-grouped list, and
    # Add item rides the search's row on this mode only.
    goto_mode(page, "list")
    assert page.locator("#toolbar").is_visible() and page.locator("#items-add").is_visible()
    groups = page.locator("#pane-items .item-group")
    assert groups.count() >= 2
    page.fill("#search", "burguer")
    assert groups.count() == 1 and groups.locator(".item-row").count() >= 1
    assert all("burguer" in t.lower() for t in groups.locator(".action-row-title").all_inner_texts())
    page.fill("#search", "zzz nothing matches")
    assert page.locator("#pane-items .empty-state").inner_text().strip() == "No matching items."
    page.fill("#search", "")
    goto_mode(page, "stores")
    assert not page.locator("#items-add").is_visible()
    goto_mode(page, "shopping")
    assert not page.locator("#toolbar").is_visible()
    goto_mode(page, "settings")
    assert not page.locator("#toolbar").is_visible()
    assert page.locator("#open-sheet").is_visible()


@pytest.mark.e2e
def test_item_forms_label_every_field(page):
    """The Add item sheet's form (#256) names all six fields (#213): the
    numbers read "Target" / "In stock" even when the box holds a value. The
    item sheet (#255) names the field on each of its pages the same way."""
    form = open_add_sheet(page).locator("#add-form")
    for label in ["Item", "Supermarket", "Zone", "Target", "In stock", "URL"]:
        assert form.get_by_label(label, exact=True).count() == 1, label
    page.keyboard.press("Escape")
    goto_mode(page, "list")
    page.locator("#pane-items .item-row .action-row-main").first.click()
    sheet = page.locator("#item-sheet")
    for to, label in (("zone", "Zone"), ("store", "Store"), ("link", "Buy link"), ("name", "Name")):
        sheet.locator(f"[data-page-to='{to}']").click()
        assert sheet.locator(f"[data-page='{to}']").get_by_label(label, exact=True).count() == 1, label
        sheet.locator("[data-sheet-back]").click()
    page.keyboard.press("Escape")
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


def _open_sheet_from(page, mode: str, name: str):
    """Tap the row showing `name` in `mode`'s list and return the item sheet."""
    goto_mode(page, mode)
    if mode == "shopping":
        page.locator("details[data-store='mercadona'] > summary").click()
    else:
        page.fill("#search", name)
    page.locator("main > .pane:not([hidden]) .item-row", has_text=name).locator(".action-row-main").click()
    sheet = page.locator("#item-sheet")
    sheet.wait_for()
    return sheet


@pytest.mark.e2e
def test_item_sheet_opens_everywhere_edits_and_deletes(page, server):
    """The item sheet (#255) is the one place an item is edited, opened from
    every row that shows it: Home, Shop, Audit, the Items list (#256), and
    a Prices row (onto its Stores & prices page, #257). Its staged
    Save posts the whole row as the old Edit item form did: a blank cell stays
    blank, never the "-" placeholder (#228), and Esc discards. Delete asks in
    the in-app confirm sheet, never the browser's confirm() (#254)."""
    name = "zzz e2e blank url"
    created = page.request.post(
        f"{server.url}/api/items",
        data={"super": "mercadona", "lugar": "nevera", "comida": name, "cantidad": 1, "tenemos": 0, "buscador": ""},
    )
    assert created.ok, created.text()
    page.reload()
    page.wait_for_selector("[data-tab='audit']")

    def row():
        payload = page.request.get(f"{server.url}/api/inventory").json()
        cols = payload["columns"]
        return next((i for i in payload["items"] if i[cols["comida"]] == name), None), cols

    title = page.locator("#item-sheet-title")
    for mode in ("dashboard", "shopping", "audit", "list"):
        sheet = _open_sheet_from(page, mode, name)
        assert title.inner_text() == name, mode
        assert sheet.get_attribute("data-page") == "main", mode
        page.keyboard.press("Escape")
        sheet.wait_for(state="hidden")
    page.fill("#search", "")
    goto_mode(page, "stores")
    # The row tap replaced the pencil (#257): no edit button left on the row.
    store_row = page.locator(".store-row", has_text=name)
    assert store_row.locator(".icon-button").count() == 0
    store_row.locator(".action-row-main").click()
    sheet = page.locator("#item-sheet")
    sheet.wait_for()
    assert sheet.get_attribute("data-page") == "prices"
    assert title.inner_text() == "Stores & prices"
    sheet.locator("[data-dialog-close]").click()
    sheet.wait_for(state="hidden")

    # Esc discards a staged change: nothing is written.
    sheet = _open_sheet_from(page, "list", name)
    sheet.locator("[data-sheet-qty='cantidad'] [data-action='target-plus']").click()
    assert not sheet.locator(".detail-save-btn").is_disabled()
    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")
    saved, cols = row()
    assert saved[cols["cantidad"]] == 1

    # The buy link opens from the sheet (#264): a real http(s) URL gets a
    # trailing open button on its row and an Open link action on its page, both
    # new-tab links apart from the row's tap that edits; blank or a search term
    # gets neither. Staged only, so Esc leaves the cell as it was. Phone width too.
    sheet = _open_sheet_from(page, "list", name)
    row_open = sheet.locator("[data-open-link-row]")
    page_open = sheet.locator("[data-open-link-page]")
    assert row_open.inner_text() == "" and page_open.inner_text() == ""
    sheet.locator("[data-page-to='link']").click()
    field = sheet.locator("[data-page='link'] input[name='buscador']")
    field.fill("leche entera")
    assert page_open.inner_text() == "" and not sheet.locator("a[data-open-link-row]").count()
    field.fill("https://example.com/p/1")
    assert page_open.get_attribute("href") == "https://example.com/p/1"
    assert page_open.get_attribute("target") == "_blank" and "noopener" in page_open.get_attribute("rel")
    assert page_open.inner_text() == "Open link"
    sheet.locator("[data-sheet-back]").click()
    assert row_open.get_attribute("href") == "https://example.com/p/1"
    assert row_open.get_attribute("target") == "_blank" and "noopener" in row_open.get_attribute("rel")
    assert row_open.get_attribute("aria-label") == f"Open buy link for {name}"
    page.set_viewport_size({"width": 390, "height": 844})
    box, edit_box = row_open.bounding_box(), sheet.locator("[data-page-to='link']").bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44, box
    assert box["x"] >= edit_box["x"] + edit_box["width"] - 1, (box, edit_box)  # beside the edit tap, not over it
    assert box["x"] + box["width"] <= 390
    page.set_viewport_size({"width": 1100, "height": 950})
    sheet.locator("[data-page-to='link']").click()  # the row's own tap still edits
    assert sheet.get_attribute("data-page") == "link"
    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")
    saved, cols = row()
    assert saved[cols["buscador"]] in (None, ""), saved[cols["buscador"]]

    # Target, in stock and zone are staged, then saved as one row; the blank
    # buy link stays blank.
    sheet = _open_sheet_from(page, "list", name)
    assert sheet.locator("[data-link-value='link']").inner_text() == "No link"
    sheet.locator("[data-sheet-qty='cantidad'] [data-action='target-plus']").click()
    sheet.locator("[data-sheet-qty='tenemos'] [data-action='current-plus']").click()
    sheet.locator("[data-page-to='zone']").click()
    sheet.locator("[data-page='zone'] [data-picker] select").select_option("congelador")
    sheet.locator("[data-sheet-back]").click()
    assert sheet.locator("[data-link-value='zone']").inner_text() == "congelador"
    with page.expect_response(lambda r: r.request.method == "PUT") as resp:
        sheet.locator(".detail-save-btn").click()
    assert resp.value.request.post_data_json["buscador"] == ""
    sheet.wait_for(state="hidden")
    page.wait_for_function("document.querySelector('#toast')?.textContent?.includes('Saved')")
    saved, cols = row()
    assert (saved[cols["cantidad"]], saved[cols["tenemos"]], saved[cols["lugar"]]) == (2, 1, "congelador")
    assert saved[cols["super"]] == "mercadona"
    assert saved[cols["buscador"]] in (None, ""), saved[cols["buscador"]]

    # Delete: the x keeps the item, the confirm sheet's Delete removes it.
    native: list[str] = []
    page.on("dialog", lambda d: (native.append(d.message), d.dismiss()))
    sheet = _open_sheet_from(page, "list", name)
    confirm = page.locator("#confirm-dialog")
    sheet.locator("[data-sheet-delete]").click()
    confirm.wait_for()
    assert name in confirm.locator(".confirm-message").inner_text()
    confirm.locator("[data-confirm='no']").click()
    assert not confirm.is_visible() and sheet.is_visible()
    assert row()[0] is not None
    sheet.locator("[data-sheet-delete]").click()
    with page.expect_response(lambda r: r.request.method == "DELETE"):
        confirm.locator("[data-confirm='yes']").click()
    sheet.wait_for(state="hidden")
    assert row()[0] is None
    assert native == [], f"native dialog shown: {native}"
    page.fill("#search", "")
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_phone_geometry_fits_and_targets_meet_the_floor(browser, server):
    """Rendered geometry at phone width (design-lint `rendered-leg`,
    project-scaffolding#157): no view scrolls sideways, and the nav tabs and the
    header buttons keep a >=44px effective target without overlapping."""
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        pg.goto(server.url)
        pg.wait_for_selector("[data-tab='audit']")
        pg.wait_for_function(LOADED_JS)
        for mode in ["dashboard", "shopping", "audit", "audio", "list", "stores", "settings"]:
            goto_mode(pg, mode)
            assert_no_horizontal_overflow(pg)
            # --control-h is the spec's 36px (#230): every control still presents a >=44px
            # effective target, via the shared ::before hit area or real 44px geometry.
            assert_min_target(pg.locator("button:visible, select:visible, textarea:visible, summary:visible, "
                                         "input:not([type=hidden]):visible"))
            if mode in ("audit", "list", "stores"):
                # J-01/J-10 (#252): one stepper (or value, or Prices' picker,
                # #257) per row leaves the item's name the room to be read at 390px.
                widths = pg.locator("main > .pane:not([hidden]) .item-row .action-row-title").evaluate_all(
                    "els => els.map((e) => e.getBoundingClientRect().width)")
                assert widths and min(widths) >= 150, f"{mode}: name squeezed to {min(widths or [0]):.0f}px"
        # #259: with every checklist zone open, a zone's summary and the
        # switches under it keep their hit areas apart (TOUCH-02).
        goto_mode(pg, "audio")
        pg.locator(".checklist-zone").evaluate_all("els => els.forEach((d) => { d.open = true; })")
        assert_no_overlap(pg.locator(".voice-checklist summary, .voice-checklist .toggle"))
        # Items' list (#256): the search and Add item share one row at 390px,
        # side by side, the search still wider than the button.
        goto_mode(pg, "list")
        search, add = pg.locator("#search").bounding_box(), pg.locator("#items-add").bounding_box()
        assert abs(search["y"] - add["y"]) < 1 and search["x"] + search["width"] <= add["x"], (search, add)
        assert search["width"] > add["width"] and add["x"] + add["width"] <= 390, (search, add)
        # The item sheet (#255), the Add item sheet (#256) and the Benchmark
        # sheet (#257): every button keeps the floor without overlapping, and
        # no sheet scrolls sideways.
        def open_item_sheet():
            pg.locator("#pane-items .item-row .action-row-main").first.click()
            return pg.locator("#item-sheet")

        # #258: Shop's Fill carts options and store sheets too, and an open
        # store group's "Add to <store>" row keeps its controls apart.
        goto_mode(pg, "shopping")
        group = pg.locator("#pane-shopping details[data-store='mercadona']")
        group.locator("> summary").click()
        add_row = group.locator(".store-add-row")
        assert_min_target(add_row.locator("button:visible, input:visible"))
        assert_no_overlap(add_row.locator("button:visible, input:visible"))
        assert_no_horizontal_overflow(pg)
        for opener in (open_item_sheet, lambda: open_add_sheet(pg), lambda: open_benchmark_sheet(pg),
                       lambda: open_fill_options(pg), lambda: open_store_sheet(pg), lambda: open_transcript_menu(pg)):
            if opener is open_item_sheet:
                goto_mode(pg, "list")
            sheet = opener()
            sheet.wait_for()
            assert_min_target(sheet.locator("button:visible"))
            assert_no_overlap(sheet.locator("button:visible"))
            assert sheet.evaluate("d => d.scrollWidth <= d.clientWidth"), "a sheet scrolls sideways at 390px"
            pg.keyboard.press("Escape")
            sheet.wait_for(state="hidden")
        goto_mode(pg, "dashboard")
        for selector in ("[data-tab]:visible", ".home-head button:visible"):
            targets = pg.locator(selector)
            assert_min_target(targets)
            assert_no_overlap(targets)
        # #260: Home fits one phone screen -- the This week card and the
        # search end above the bottom nav pill (the viewport's edge where the
        # nav is drawn as top tabs), nothing to scroll for.
        assert pg.evaluate(_HOME_FITS_JS), pg.evaluate(_HOME_RECTS_JS)
    finally:
        pg.close()


@pytest.mark.e2e
def test_add_item_increases_count(page):
    # The plain count in Audit's page header: "N items".
    count = lambda: int(page.locator("#audit-context").text_content().split()[0])  # noqa: E731
    before = count()
    sheet = open_add_sheet(page)
    sheet.locator("#add-form input[name='comida']").fill("zzz e2e item")
    # Zone and Supermarket are native pickers of the existing values (#241) —
    # no typing; the text box behind them stays hidden.
    sheet.locator("#add-form [data-picker] select").nth(0).select_option("mercadona")
    sheet.locator("#add-form [data-picker] select").nth(1).select_option("nevera")
    assert not sheet.locator("#add-form input[name='super']").is_visible()
    assert not sheet.locator("#add-form input[name='lugar']").is_visible()
    # The sheet's one footer primary submits the form (#256).
    with page.expect_response(lambda r: r.url.endswith("/api/items") and r.request.method == "POST") as resp:
        sheet.locator(".detail-save-btn").click()
    body = resp.value.request.post_data_json
    assert (body["super"], body["lugar"]) == ("mercadona", "nevera")
    # The sheet closes on success with a transient toast (#200); the new item
    # is in the Items list under its zone's group.
    sheet.wait_for(state="hidden")
    page.wait_for_function("document.querySelector('#toast')?.textContent?.includes('zzz e2e item added')")
    group = page.locator("#pane-items .item-group", has=page.locator(".item-row", has_text="zzz e2e item"))
    assert group.locator(".item-group-head").inner_text().lower() == "nevera"
    assert count() == before + 1


@pytest.mark.e2e
def test_add_item_new_zone_and_supermarket(page):
    """"New…" in the pickers reveals a text box so a first-time zone or
    supermarket can still be entered (#241); the typed values are what saves.
    A blank name is refused by the form itself: nothing is posted (#256)."""
    sheet = open_add_sheet(page)
    form = sheet.locator("#add-form")
    posts: list[str] = []
    page.on("request", lambda r: posts.append(r.url) if r.method == "POST" and r.url.endswith("/api/items") else None)
    sheet.locator(".detail-save-btn").click()
    page.wait_for_timeout(200)
    assert posts == [] and sheet.is_visible()
    form.locator("[name='comida']").fill("zzz new place item")
    form.locator("[data-picker] select").nth(0).select_option(label="New supermarket…")
    form.locator("[data-picker] select").nth(1).select_option(label="New zone…")
    form.locator("input[name='super']").fill("zzz lidl e2e")
    form.locator("input[name='lugar']").fill("zzz balcon e2e")
    with page.expect_response(lambda r: r.url.endswith("/api/items") and r.request.method == "POST") as resp:
        sheet.locator(".detail-save-btn").click()
    body = resp.value.request.post_data_json
    assert (body["super"], body["lugar"]) == ("zzz lidl e2e", "zzz balcon e2e")
    assert resp.value.ok
    sheet.wait_for(state="hidden")
    # A brand-new zone gets its own group in the Items list.
    assert "zzz balcon e2e" in [t.lower() for t in page.locator("#pane-items .item-group-head").all_inner_texts()]
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_audio_match_and_apply_writes_log(page, server):
    """Audit > Voice (#259): the glance with Record, then match, then Apply as
    the one primary with "Needs you" first; a mention no item took is fixed in
    Manual in two taps (Find, then the stepper) and the review waits as it was
    left; Apply writes the log the glance then reads."""
    goto_mode(page, "audio")
    glance = page.locator("#pane-audit #voice-glance")
    assert page.locator("#pane-audit .pane-body > :first-child").get_attribute("id") == "voice-glance"
    assert glance.locator("#record-toggle").inner_text().strip() == "Record"
    assert "button-primary" in glance.locator("#record-toggle").get_attribute("class")
    # J-07: the match model reads humanized, never the raw config id
    # (gemini_pro) -- on the transcript card and in its menu, beside Clear.
    assert page.locator("#audio-model-label").inner_text() == "Gemini Pro"
    menu = open_transcript_menu(page)
    assert menu.locator("#audio-model option:checked").inner_text() == "Gemini Pro"
    assert menu.locator("#audio-clear").is_visible()
    page.keyboard.press("Escape")
    menu.wait_for(state="hidden")
    assert page.locator("#pane-audit #audio-clear").count() == 0

    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    page.locator("#voice-results").wait_for(timeout=120000)
    primaries = page.locator("#pane-audit .button-primary:visible")
    assert primaries.count() == 1 and primaries.first.get_attribute("id") == "apply-audio"
    groups = page.locator("#pane-audit .voice-results .item-group")
    assert groups.first.locator(".item-group-head").inner_text().lower() == "needs you"
    assert page.locator("#voice-need-count").inner_text() == "1"
    assert page.locator("#audit-context").inner_text() == "1 unmatched"
    # The accept switch renders pre-on; untick it, and it stays unticked.
    accept = page.locator("[data-audio-idx]").first
    assert accept.get_attribute("aria-checked") == "true"
    assert page.locator("#voice-apply-count").inner_text() == "1"
    accept.click()
    assert page.locator("#voice-apply-count").inner_text() == "0"

    # Tap 1: Find -> Manual, searching every zone for what was heard.
    groups.first.locator(".item-row", has_text=UNMATCHED_PHRASE).locator(".voice-find").click()
    assert page.locator("#pane-audit [data-mode='audit']").get_attribute("aria-pressed") == "true"
    assert page.input_value("#search") == UNMATCHED_PHRASE
    rows = page.locator("#pane-audit .item-row")
    assert rows.count() == 1 and "pulpo cocido" in rows.first.inner_text()
    before = rows.first.locator(".stepper-value").inner_text()
    # Tap 2: the stepper.
    rows.first.locator("[data-action='current-plus']").click()
    page.wait_for_function(
        "b => document.querySelector('#pane-audit .item-row .stepper-value')?.textContent !== b", arg=before)
    page.fill("#search", "")

    # Back on Voice, the review waits as it was left.
    goto_mode(page, "audio")
    page.locator("#voice-results").wait_for()
    assert page.locator("[data-audio-idx]").first.get_attribute("aria-checked") == "false"
    page.locator("[data-audio-idx]").first.click()
    page.click("#apply-audio")
    page.wait_for_function(
        "document.querySelector('#audio-status')?.textContent?.includes('Inventory updated')",
        timeout=30000,
    )
    assert page.locator("#voice-results").count() == 0
    page.wait_for_function("document.querySelector('#voice-last-date')?.textContent?.startsWith('Last audit')")
    assert page.locator("#voice-last-unmatched").inner_text() == "1"
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
    simulator and the chip until it is reset, and Checked clears it.

    The glance card (#257) leads with the cost, its delta and the one action;
    the run's status, the import, the plan actions and the breakdown live in
    the Benchmark sheet its footer row opens."""
    goto_mode(page, "stores")
    glance = page.locator("#stores-sim")
    assert glance.evaluate("el => el.closest('.pane-body').firstElementChild === el"), "glance card is not first"
    bench = open_benchmark_sheet(page)
    bench.locator(".detail-save-btn").click()  # Import latest run, the sheet's one primary
    bench.locator(".panel-status.ok").wait_for()
    page.wait_for_selector("#stores-sim[data-state='ready']")
    # Benchmark status: run date and the review due 90 days later, plus the copyable steps.
    bench.locator(".stores-status").wait_for()
    status = bench.locator(".stores-status").inner_text()
    assert "2026-01-15" in status and "2026-04-15" in status
    assert bench.locator("[data-stores-action='copy-steps']").count() == 1
    assert "Pricing" not in bench.locator(".benchmark-section", has_text="Cost breakdown").inner_text()
    bench.locator("[data-dialog-close]").click()
    bench.wait_for(state="hidden")
    # The footer row names the run's exception (the fixture's review is overdue).
    foot = glance.locator("[data-stores-action='benchmark'] .sheet-link-value")
    assert (foot.inner_text(), foot.get_attribute("data-tone")) == ("Review due", "attention")
    # One primary on the glance card: the recommended plan until a pick differs.
    assert glance.locator(".button-primary").all_inner_texts() == ["Load recommended plan"]
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
    today_total = page.locator(".stores-total strong").inner_text()

    glance.locator("[data-stores-action='recommended']").click()
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent !== before", arg=today_total,
    )
    assert "Saves" in page.locator(".stores-delta").inner_text()
    # The plan differs from the list now, so the one action is Review & apply.
    page.wait_for_function("document.querySelector('#stores-sim .button-primary')?.dataset.storesAction === 'review'")
    assert glance.locator(".button-primary").count() == 1

    # Back to the list (from the Benchmark sheet, which closes), then one
    # what-if pick: nothing is written until Apply.
    open_benchmark_sheet(page).locator("[data-stores-action='reset']").click()
    bench.wait_for(state="hidden")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent === before", arg=today_total,
    )
    burger = page.locator(".store-row", has_text="burguer ternera").first
    # Only the stores the list buys from are shown until "Show all stores".
    assert burger.locator(".store-chip", has_text="Carrefour").count() == 0
    page.click("[data-stores-showall]")
    page.wait_for_selector(".store-row .store-chip:has-text('Carrefour')")
    burger = page.locator(".store-row", has_text="burguer ternera").first
    # The store links wrap as reference pills (#257): none starts off-screen.
    assert burger.locator(".store-chips").evaluate("el => el.scrollWidth <= el.clientWidth")
    # TOUCH-02: the pills' expanded hit areas never reach into the row's tap target.
    assert_no_overlap(burger.locator(".action-row-main, .store-chip, [data-stores-pick]"))
    burger.locator("[data-stores-pick]").select_option("carrefour")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent !== before", arg=today_total,
    )
    assert burger.locator(".store-chip.is-picked").inner_text().startswith("Carrefour")

    assert glance.locator(".button-primary").inner_text() == "Review & apply (1)"
    glance.locator("[data-stores-action='review']").click()
    row = page.locator("#stores-apply-dialog .apply-row")
    row.first.wait_for()
    assert row.count() == 1
    assert row.first.locator(".apply-qty").input_value() == "2"
    page.click("#stores-apply-dialog .detail-save-btn")
    page.wait_for_function("document.querySelector('#toast')?.textContent === 'Applied 1 change'")
    assert not page.locator("#stores-apply-dialog").is_visible()
    assert glance.locator(".button-primary").inner_text() == "Load recommended plan"

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
    # The reason is the one status chip, in the attention tone (#254), and the
    # Items header names the exception instead of the mode.
    assert burger.locator(".chip[data-tone='attention']").inner_text().startswith("Moved store")
    assert page.locator("#items-context").inner_text() == "1 needs checking"

    # ── Baseline (#183), in the Benchmark sheet's cost breakdown: freeze the
    # list as the new "today", confirm shows the unchecked count (over the
    # sheet), then reset restores the exact prior simulate result.
    bench = open_benchmark_sheet(page)
    hint = "document.querySelector('#stores-benchmark-sheet .stores-today')?.textContent"
    assert "benchmarked" in bench.locator(".stores-today").inner_text()
    before_baseline_total = page.locator(".stores-total strong").inner_text()
    bench.locator("[data-stores-action='set-baseline']").click()
    baseline_dialog = page.locator("#stores-baseline-dialog")
    baseline_dialog.wait_for()
    assert "1 item" in baseline_dialog.inner_text() and "checking" in baseline_dialog.inner_text()
    baseline_dialog.locator(".detail-save-btn").click()
    page.wait_for_function("() => !document.querySelector('#stores-baseline-dialog').open")
    page.wait_for_function(f"() => {hint}.includes('baseline')")
    assert bench.is_visible()
    assert "Same as today" in page.locator(".stores-delta").inner_text()
    bench.locator("[data-stores-action='reset-baseline']").click()
    page.wait_for_function(f"() => !{hint}.includes('baseline')")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent === before", arg=before_baseline_total,
    )
    bench.locator("[data-dialog-close]").click()
    bench.wait_for(state="hidden")
    total = page.locator(".stores-total strong").inner_text()

    # A row tap opens the item sheet (#255) straight onto Stores & prices (#257).
    burger.locator(".action-row-main").click()
    dialog = page.locator("#item-sheet")
    dialog.locator("tr[data-review-store='carrefour']").wait_for()
    assert dialog.get_attribute("data-page") == "prices"
    # The cut the cart picks for that product is shown on its store row (#179).
    assert dialog.locator("tr[data-review-store='carrefour'] .review-option").inner_text().startswith("Cut: Fileteado")
    assert dialog.locator("tr[data-review-store='ametller'] .review-option").count() == 0
    # TOUCH-01 (#257): a store's link is a labelled 44px target, not the raw URL.
    link = dialog.locator("tr[data-review-store='ametller'] .review-url").first
    assert link.inner_text() == "Open at Ametller Origen"
    assert link.get_attribute("href").startswith("https://www.ametllerorigen.com/")
    assert link.bounding_box()["height"] >= 44
    # Wide on a desktop, no sideways scroll on a phone.
    page.set_viewport_size({"width": 1280, "height": 900})
    assert dialog.bounding_box()["width"] >= 900
    page.set_viewport_size({"width": 390, "height": 844})
    assert dialog.evaluate("d => d.scrollWidth <= d.clientWidth"), "item sheet scrolls sideways at 390px"
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

    # Back on the main page the row names what needs you; the quantity maths
    # is one page away.
    dialog.locator("[data-sheet-back]").click()
    prices_value = dialog.locator("[data-link-value='prices']")
    assert (prices_value.inner_text(), prices_value.get_attribute("data-tone")) == ("Needs checking", "attention")
    dialog.locator("[data-page-to='quantity']").click()
    qty = dialog.locator(".review-qty-lines").inner_text()
    assert "Ametller Origen 2 × 0.3 kg = 0.6 kg" in qty
    assert "Carrefour 2 × 0.6 kg = 1.2 kg (+100%)" in qty
    dialog.locator("[data-sheet-back]").click()

    # Target and stock are staged on the steppers and saved to the list; the
    # rest of the row is untouched.
    dialog.locator("[data-sheet-qty='cantidad'] [data-action='target-plus']").click()
    dialog.locator("[data-sheet-qty='tenemos'] [data-action='current-plus']").click()
    with page.expect_response(lambda r: r.request.method == "PUT" and r.url.endswith(f"/api/items/{item['id']}")) as resp:
        dialog.locator(".detail-save-btn").click()
    assert resp.value.ok
    dialog.wait_for(state="hidden")
    saved = next(i for i in page.request.get(f"{server.url}/api/inventory").json()["items"]
                 if i["comida"] == "burguer ternera")
    assert (saved["cantidad"], saved["tenemos"]) == (3, 2)
    assert (saved["super"], saved["buscador"]) == ("carrefour", item["buscador"])

    # Checked is stored server-side and empties the Needs-checking filter.
    burger.locator(".action-row-main").click()
    dialog.locator("[data-review-checked]").click()
    page.wait_for_function(f"({count})('needs') === '0' && ({count})('checked') === '1'")
    assert dialog.locator("[data-review-checked]").get_attribute("aria-checked") == "true"
    # Nothing left to check: the header falls back to the plain count.
    assert page.locator("#items-context").inner_text().endswith(" items")
    dialog.locator("[data-dialog-close]").click()
    assert page.locator(".store-row").count() == 0
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


# Every dropdown, inline input and labelled button in one open dialog, checked
# against the vendored recipes (#207: select-native/, button/, modal/), with
# the expected values resolved from the theme's own tokens -- never hard-coded.
# A button must match one button.css tier (or the shared disabled recipe; a
# .danger one, the outlined destructive recipe: transparent fill); glyph-only controls (.icon-button),
# switches and list rows (.action-row-main, the item sheet's link rows) are out
# of scope; the x close is only checked for being unpainted.
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
    surface: ['--input-bg', document.documentElement.dataset.theme === 'dark' ? '--ink' : '--muted', '--line'],
    disabled: ['--input-bg', '--muted', '--line'],
    danger: ['--card', '--danger-text', '--deficit'],
  };
  const expect = Object.fromEntries(Object.entries(tiers).map(([k, [bg, fg, border]]) =>
    [k, { bg: color(bg), fg: color(fg), border: color(border) }]));
  // Outlined (project-scaffolding#379): a transparent fill, never a tint or a solid.
  expect.danger.bg = 'rgba(0, 0, 0, 0)';
  const radius = len('--radius-md'), controlH = len('--control-h'), fieldH = len('--field-h'), primaryH = len('--primary-h');
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
    .filter((b) => shown(b) && !b.matches('.detail-close, .icon-button, [role=switch], .action-row-main'));
  for (const b of buttons) {
    const s = getComputedStyle(b), box = b.getBoundingClientRect();
    const got = { bg: s.backgroundColor, fg: s.color, border: s.borderTopColor };
    const pool = b.disabled ? ['disabled'] : b.matches('.danger') ? ['danger'] : ['primary', 'tint', 'surface'];
    const tier = pool.find((k) => ['bg', 'fg', 'border'].every((p) => got[p] === expect[k][p]));
    if (!tier) bad.push(`${name(b)} wears no button tier: ${JSON.stringify(got)}`);
    if (Math.abs(parseFloat(s.borderTopLeftRadius) - radius) > 0.5) bad.push(`${name(b)} radius ${s.borderTopLeftRadius}`);
    if (box.height < controlH - 0.5) bad.push(`${name(b)} is ${box.height}px tall`);
    // the label's own box, not scrollWidth: the ::before hit area (#230) extends the scrollable overflow
    const range = document.createRange(); range.selectNodeContents(b);
    const label = range.getBoundingClientRect();
    if (label.height > b.clientHeight + 1 || label.width > b.clientWidth + 1) bad.push(`${name(b)} label overflows`);
    inside(b, box);
  }
  // the x close is an .icon-button (project-scaffolding#339): unpainted at rest
  const close = card.querySelector('.detail-close');
  if (!close) bad.push('no close button');
  else {
    const s = getComputedStyle(close);
    if (s.backgroundColor !== 'rgba(0, 0, 0, 0)' || s.borderTopWidth !== '0px') bad.push(`close is painted: ${s.backgroundColor} ${s.borderTopWidth}`);
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
    if (Math.abs(box.height - fieldH) > 0.5) bad.push(`${name(f)} is ${box.height}px tall`);
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

    # The Add item sheet (#256): the search's tint button, the two pickers,
    # and the footer primary that submits the form.
    open_add_sheet(page)
    found = check("add-sheet")
    assert found["buttons"] == ["Search", "Add item"] and found["selects"] == 2, found
    page.keyboard.press("Escape")

    # The Benchmark sheet (#257): the plan and baseline buttons, full width so
    # no label wraps (COMP-07), and the import as its one footer primary.
    bench = open_benchmark_sheet(page)
    bench.locator(".detail-save-btn").click()
    page.wait_for_selector("#stores-sim[data-state='ready']")
    bench.locator("[data-stores-action='set-baseline']").wait_for()
    assert check("stores-benchmark-sheet")["buttons"] == [
        "Copy steps", "Load recommended plan", "Reset to my list", "Set as baseline", "Import latest run"]

    bench.locator("[data-stores-action='set-baseline']").click()
    page.locator("#stores-baseline-dialog").wait_for()
    assert check("stores-baseline-dialog")["buttons"] == ["Set baseline"]
    page.locator("#stores-baseline-dialog [data-dialog-close]").click()
    bench.locator("[data-dialog-close]").click()

    burger = page.locator(".store-row", has_text="burguer ternera")
    burger.locator(".action-row-main").click()
    # The item sheet (#255): its Stores & prices page with a store's editor
    # open, its main page (the in-body Delete), and a picker page.
    detail = page.locator("#item-sheet")
    detail.locator("[data-review-edit]").first.click()
    detail.locator("[data-review-form]").wait_for()
    found = check("item-sheet")
    assert {"Save", "Cancel"} <= set(found["buttons"]), found
    assert found["selects"] >= 1, found
    detail.locator("[data-review-cancel]").click()
    detail.locator("[data-sheet-back]").click()
    assert check("item-sheet")["buttons"] == ["Delete item", "Save"]
    detail.locator("[data-page-to='zone']").click()
    assert check("item-sheet")["selects"] == 1
    detail.locator("[data-dialog-close]").click()

    pick = burger.locator("[data-stores-pick]")
    other = pick.evaluate("s => [...s.options].map((o) => o.value).find((v) => v && v !== s.value)")
    pick.select_option(other)
    page.click("#stores-sim [data-stores-action='review']")
    page.locator("#stores-apply-dialog .apply-row").first.wait_for()
    found = check("stores-apply-dialog")
    assert len(found["buttons"]) == 1 and found["buttons"][0].startswith("Apply") and found["inputs"] >= 2, found
    page.locator("#stores-apply-dialog [data-dialog-close]").click()
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_add_item_product_search(page, server):
    """The Add item sheet opens on the product search (#256; #182, it was the
    Search tab). The store search is stubbed at the HTTP seam (no Chrome): a
    new term's "Use" pre-fills Add by hand below it and nothing is saved until
    the sheet's Add item, which closes it; reopened, the run is still there
    with that card marked Added. A term already on the list keeps its confirm
    row -> /select. Last in the module on purpose: it adds a row to the shared
    fixture list."""
    sheet = open_add_sheet(page)
    search = sheet.locator("#product-search")
    search.locator("#search-term").wait_for()
    # Search first, then adding by hand, in reading order.
    assert sheet.locator(".add-sheet-section .card-title").all_inner_texts() == ["Find a store product", "Add by hand"]
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
    form = sheet.locator("#add-form")
    assert form.locator("[name='comida']").input_value() == "zzz sandia e2e"
    assert form.locator("[name='super']").input_value() == "carrefour"
    assert form.locator("[name='buscador']").input_value() == new_url
    assert form.locator("[name='cantidad']").input_value() == "1"
    assert "Filled in Add by hand below" in search.locator("#search-status").inner_text()
    assert not [i for i in page.request.get(inventory).json()["items"] if i["comida"] == "zzz sandia e2e"]
    form.locator("[data-picker] select").nth(1).select_option("nevera")
    sheet.locator(".detail-save-btn").click()
    sheet.wait_for(state="hidden")
    sheet = open_add_sheet(page)
    new_card.locator("button:has-text('Added')").wait_for()
    created = [i for i in page.request.get(inventory).json()["items"] if i["comida"] == "zzz sandia e2e"]
    assert [(i["super"], i["buscador"], i["cantidad"]) for i in created] == [("carrefour", new_url, 1)]

    # The header names the exact term searched; a matched row is only a hint (#214).
    heads = page.locator(".search-group-term").all_inner_texts()
    assert heads == ["Results for “zzz sandia e2e”", f"Results for “{existing['comida']}”"]
    assert "Matches your item zzz fila existente e2e" in page.locator(".search-group-hint").inner_text()

    old_card = page.locator(".candidate", has_text="Existing e2e")
    old_card.locator("[data-action='search-use']").click()
    old_card.locator("[data-action='search-add-new']").click()  # → Add by hand, under the searched term
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
    page.locator("#voice-results").wait_for(timeout=600000)
    assert page.locator("#audio-status.ok").count() >= 1


@pytest.mark.e2e
def test_settings_gear_opens_settings_from_every_tab(page, monkeypatch):
    """#200: Settings is never a tab -- the gear beside the theme toggle opens it
    from every tab, no nav tab is selected while it is open, and a tap on a tab
    (or on the gear again) comes back. #261: Settings is three inset groups,
    every row showing its value; Store logins and Email watch open sheets, and
    the login check reports per store in its sheet, never the error toast."""
    for tab in ("inventory", "shopping", "audit", "items"):
        page.click(f"[data-tab='{tab}']")
        page.locator(f"#pane-{tab} .home-settings").click()
        assert page.locator("#pane-settings").is_visible()
        assert page.locator(f"#pane-{tab}").is_hidden()
        assert page.locator("#pane-settings #open-email-watch").count() == 1
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

    # #261: no closed cards of buttons -- three inset groups, every row valued.
    goto_mode(page, "settings")
    settings = page.locator("#pane-settings")
    assert settings.locator(".settings-group > .overline").all_inner_texts() == ["DISPLAY", "STORES", "DATA"]
    assert settings.locator("details, .settings-actions").count() == 0
    page.wait_for_function("document.querySelector('#email-watch-value').textContent.trim() !== ''")
    for row in settings.locator(".sheet-link").all():
        assert row.locator(".sheet-link-value").inner_text().strip(), row.inner_text()
    assert page.locator("#store-logins-value").inner_text() == "Not checked"

    # Email watch: its sheet holds what the folded card did.
    page.click("#open-email-watch")
    email = page.locator("#email-watch-sheet")
    email.locator("#email-check-now, .panel-status.error").first.wait_for()
    page.keyboard.press("Escape")
    email.wait_for(state="hidden")

    # Store logins: an invented check (never a real Chrome) lands in the sheet,
    # one row per store; logged out is an attention chip, the toast neutral.
    monkeypatch.setattr(system_router.login_check_runner, "run", lambda: {
        "mercadona": {"state": "logged_in", "detail": ""},
        "carrefour": {"state": "logged_out", "detail": "invented"},
    })
    page.click("#open-store-logins")
    logins = page.locator("#store-logins-sheet")
    logins.locator("#check-logins").click()
    logins.locator(".login-rows .action-row").nth(1).wait_for()
    assert logins.locator(".login-rows .chip[data-tone='attention']").inner_text() == "Logged out"
    assert logins.locator(".login-ok").count() == 1
    page.wait_for_function("document.querySelector('#toast')?.textContent === '1 store logged out'")
    assert "error" not in (page.locator("#toast").get_attribute("class") or "")
    page.keyboard.press("Escape")
    logins.wait_for(state="hidden")
    assert page.locator("#store-logins-value").inner_text() == "1 logged out"
    assert page.locator("#store-logins-value").get_attribute("data-tone") == "attention"
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
    dry_run = open_fill_options(page).locator("#automation-dry-run")
    if dry_run.get_attribute("aria-checked") != "true":
        dry_run.click()  # client-side only: it just rewrites "what will run"
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

_ALL_MODES = ["dashboard", "shopping", "audit", "audio", "list", "stores", "settings"]


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
  .filter((e) => !e.matches('.action-row-main'))
  .map((e) => e.textContent.replace(/\s+/g, ' ').trim())
  .filter(Boolean)
"""
_PROPER = {"Ametller", "Origen", "Mercadona", "Carrefour", "CSV"}


@pytest.mark.e2e
def test_labels_are_sentence_case(page):
    """J-08: buttons, summaries and headings use sentence case (first word
    capitalised, the rest lower) — store names and CSV aside — on every view.
    An item row's tap target is skipped: its text is the item's own name (#254)."""
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
    """J-09: the idle run log names the control that fills it -- the carts
    glance's Fill carts (#258)."""
    goto_mode(page, "shopping")
    log = page.locator("#automation-log")
    log.wait_for(state="attached")
    text = log.text_content()
    assert "Fill carts" in text and "progress appears here" in text


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
@pytest.mark.parametrize("mode,tab", [("dashboard", "inventory"), ("shopping", "shopping"), ("audit", "audit"), ("list", "items")])
def test_every_tab_opens_on_a_heading_not_a_control(page, mode, tab):
    """J-04: below the page header, a tab's first heading-or-control in reading
    order is a heading — never a mode pill, the search box or a collapsible
    card header (a heading inside a <summary> reads as a control)."""
    goto_mode(page, mode)
    page.wait_for_timeout(300)
    first = page.evaluate(_FIRST_AFTER_HEADER_JS, tab)
    assert first and first.startswith("h2:"), f"{tab} opens on {first}"


@pytest.mark.e2e
def test_usage_log_records_views_and_tapped_controls(page, server):
    """#276: the vendored usage log posts view and tap events, constant ids
    only, to this app's own origin -- and nothing else leaves the page. Driven
    through the real UI (tabs, a segment, Settings, a tracked control), flushed
    the way a page hide flushes, and read back through the summary route."""
    sent: list[str] = []
    page.on("request", lambda r: sent.append(r.post_data or "") if "/api/usage/events" in r.url else None)
    off_origin: list[str] = []
    page.on("request", lambda r: off_origin.append(r.url) if "usage" in r.url and not r.url.startswith(server.url) else None)

    page.click("[data-tab='shopping']")
    page.click("[data-tab='items']")
    page.locator(".subnav [data-mode='stores']").click()
    goto_mode(page, "settings")
    page.locator("#pane-settings [data-textsize='default']").click()
    page.evaluate("""() => {
      Object.defineProperty(document, 'visibilityState', { value: 'hidden', configurable: true });
      document.dispatchEvent(new Event('visibilitychange'));
    }""")
    for _ in range(50):
        summary = page.request.get(f"{server.url}/api/usage/summary?days=1").json()
        if "settings" in summary["views"] and "settings.text-default" in summary["controls"]:
            break
        page.wait_for_timeout(100)
    else:
        raise AssertionError(f"events never arrived: {summary}")

    assert {"shopping", "items", "items:prices", "settings"} <= set(summary["views"]), summary["views"]
    assert summary["controls"]["settings.text-default"] >= 1
    assert off_origin == [], f"usage events left the app's origin: {off_origin}"
    payload = " ".join(sent)
    assert "Authorization" not in payload and "burguer" not in payload.lower(), "only ids, classes and a session id are posted"
    assert page._js_errors == [], f"JS errors: {page._js_errors}"
