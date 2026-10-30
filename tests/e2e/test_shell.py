"""The app shell: tabs and page headers, the toolbar, phone geometry and each tab's landing view."""

from __future__ import annotations

import pytest

from tests.e2e._geometry import assert_min_target, assert_no_horizontal_overflow, assert_no_overlap
from tests.e2e._helpers import (
    LOADED_JS,
    goto_mode,
    open_add_sheet,
    open_benchmark_sheet,
    open_fill_history,
    open_fill_options,
    open_store_sheet,
    open_transcript_menu,
)
from tests.e2e._surface import surface


_HOME_FITS_JS = """() => {
  const search = document.querySelector('#search').getBoundingClientRect();
  const nav = document.querySelector('nav.tabs').getBoundingClientRect();
  return search.bottom <= (nav.top > search.top ? nav.top : innerHeight);
}"""
# Where Home's blocks sit, for a failed one-screen check.
_HOME_RECTS_JS = """() => Object.fromEntries(['#pane-inventory .home-head', '#this-week', '#search', 'nav.tabs', '.page-foot']
  .map((s) => { const r = document.querySelector(s).getBoundingClientRect(); return [s, [Math.round(r.top), Math.round(r.bottom)]]; })
  .concat([['scrollY', [scrollY, document.querySelector('main.app').scrollTop]]]))"""


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
    """The installed iOS PWA needs the opaque `default` status bar + viewport-fit=cover,
    or the nav bar rides the viewport growth after a cold launch (_vendored/nav
    README). Desktop browsers can't reproduce that, so assert the pair here."""
    status_bar = page.locator('meta[name="apple-mobile-web-app-status-bar-style"]')
    assert status_bar.get_attribute("content") == "default"
    assert "viewport-fit=cover" in page.locator('meta[name="viewport"]').get_attribute("content")


def _check_tabs_and_page_headers(page):
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


def _check_items_and_audit_segments(page):
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


def _check_shop_tab_badge(page):
    # #258: the last fill left an item out, and Shop hasn't been seen since --
    # the Shop tab carries the count badge, spoken as part of its name.
    shop_tab = page.locator("nav.tabs [data-tab='shopping']")
    shop_tab.locator(".tab-badge").wait_for()
    assert shop_tab.locator(".tab-badge").inner_text() == "1"
    assert "Shop, 1 not in cart" in shop_tab.evaluate("t => t.textContent.replace(/\\s+/g, ' ')")
    return shop_tab


def _check_carts_glance(page, shop_tab):
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
    return fill


def _check_fill_options_sheet(page):
    # Options in a sheet: every pick still there, "what will run" in words --
    # never the interpreter command line or a lower-case store key.
    sheet = open_fill_options(page)
    for control in ("#automation-store", "#automation-cart-mode", "#automation-dry-run"):
        assert sheet.locator(control).is_visible(), f"{control} missing from the options"
    assert sheet.locator("#automation-store option").all_inner_texts() == ["All stores", "Ametller Origen", "Mercadona"]
    plan = sheet.locator("#automation-plan").inner_text()
    assert plan.startswith("Adds 41 items to the Ametller Origen and Mercadona carts"), plan
    assert "python" not in sheet.inner_text().lower() and page.locator("#automation-command").count() == 0
    return sheet


def _check_clean_cart_plan(sheet):
    # Clean cart: Fill carts waits for the confirm, which the sheet asks for.
    sheet.locator("#automation-cart-mode").select_option("clean")
    sheet.locator("#automation-clean-confirm").wait_for()
    assert sheet.locator("#automation-plan").inner_text().startswith("Empties the")


def _check_clean_cart_blocks_fill(page, fill, sheet):
    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")
    assert fill.locator("#automation-start").is_disabled()
    assert fill.locator("#automation-blocked").is_visible()
    assert fill.locator("#automation-options-value").inner_text() == "All stores · Clean cart"


def _complete_a_row(page, fill):
    # A shopping-list re-render (Complete) must not reset the run picks.
    group = page.locator("#pane-shopping details[data-store='mercadona']")
    assert group.locator(".collapse-title").inner_text() == "Mercadona"
    group.locator("> summary").click()
    group.locator("[data-action='mark-buy']").first.click()
    group.locator("[data-action='undo-buy']").first.wait_for()
    # Complete is the row's leading check (#254): pressed, and the name struck.
    got = group.locator(".item-row", has=page.locator("[data-action='undo-buy']")).first
    assert got.locator(".action-row-check").get_attribute("aria-pressed") == "true"
    assert got.locator(".action-row-title s").count() == 1
    # The check is named for its verb, the link for what it opens (#289).
    title = got.locator(".action-row-title").inner_text()
    assert got.locator(".action-row-check").get_attribute("aria-label") == f"Complete {title}"
    link = got.locator("a.action-row-trail")
    if link.count():
        assert link.get_attribute("aria-label") == f"Open buy link for {title}"
        assert link.get_attribute("title") == f"Open buy link for {title}"
    assert fill.locator("#automation-options-value").inner_text() == "All stores · Clean cart"
    assert open_fill_options(page).locator("#automation-cart-mode").input_value() == "clean"
    page.keyboard.press("Escape")
    return group


def _quick_add_extra(group):
    # Quick-add is the group's "Add to <store>" row; its Remove, the cart
    # offsets and Unmark all live in the store sheet (#252 decision 6).
    group.locator(".store-add-row input[name='name']").fill("Invented extra")
    group.locator(".store-add-row button[type='submit']").click()
    extra = group.locator(".item-row", has_text="Invented extra")
    extra.wait_for()
    assert extra.locator("[data-action='remove-extra']").count() == 0
    return extra


def _toggle_store_sheet_edit(store_sheet, group):
    store_sheet.locator("[data-action='offset-items']").fill("2")
    store_sheet.locator("[data-action='offset-items']").dispatch_event("change")
    assert group.locator("[data-store-sheet] .sheet-link-value").inner_text() == "2 already in cart"
    # No Remove at rest (COMP-15); the Edit toggle in the Added here header
    # reveals it, and removing the last quick-add leaves edit mode.
    assert store_sheet.locator("[data-action='remove-extra']").count() == 0
    edit = store_sheet.locator("[data-action='edit-extras']")
    assert edit.get_attribute("aria-pressed") == "false"
    edit.click()
    assert edit.get_attribute("aria-pressed") == "true"


def _remove_extra_and_unmark(page, store_sheet, group, extra):
    store_sheet.locator("[data-action='remove-extra']").click()
    extra.wait_for(state="detached")
    assert store_sheet.locator("[data-action='edit-extras']").count() == 0
    store_sheet.locator("[data-store-unmark]").click()
    group.locator("[data-action='undo-buy']").wait_for(state="detached")
    page.keyboard.press("Escape")


def _theme_toggle_works_from_every_header(page):
    # Theme toggle works from every pane's own header, not just Home's
    # (#theme-toggle id stays on Home's for back-compat; the rest share the
    # .theme-toggle class, #153 J-04).
    theme = lambda: page.evaluate("document.documentElement.getAttribute('data-theme')")  # noqa: E731
    before_theme = theme()
    page.locator("#pane-shopping .theme-toggle").click()
    assert theme() != before_theme
    page.locator("#pane-shopping .theme-toggle").click()
    assert theme() == before_theme


def _retired_tabs_reopen_on_new_home(page):
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


@pytest.mark.e2e
def test_four_tab_nav_and_relocated_sections(page, pw, server):
    """#182 + #200: four tabs, and every capability of the retired Search/Auto
    tabs in reach -- product search in Items' Add item sheet, Fill carts at
    the bottom of Shop, Email Watch in Settings (the header gear, never a tab)
    -- with a saved retired tab reopening on its new home instead of falling
    back to Home. #256: Items is two segments, and a saved retired Items mode
    (Targets / Edit item / Add item) reopens on the Items list, never an empty
    pane. Then the WebKit 390px nav check."""
    _check_tabs_and_page_headers(page)
    _check_items_and_audit_segments(page)
    shop_tab = _check_shop_tab_badge(page)
    fill = _check_carts_glance(page, shop_tab)
    surface(page, "shop.carts-miss")
    sheet = _check_fill_options_sheet(page)
    surface(page, "shop.fill-options")
    _check_clean_cart_plan(sheet)
    surface(page, "shop.fill-options-clean")
    _check_clean_cart_blocks_fill(page, fill, sheet)
    group = _complete_a_row(page, fill)
    extra = _quick_add_extra(group)
    surface(page, "shop.store-group")
    store_sheet = open_store_sheet(page)
    assert store_sheet.locator("#store-sheet-title").inner_text() == "Mercadona"
    surface(page, "shop.store-sheet")
    _toggle_store_sheet_edit(store_sheet, group)
    surface(page, "shop.store-sheet-edit")
    _remove_extra_and_unmark(page, store_sheet, group, extra)
    _theme_toggle_works_from_every_header(page)
    _retired_tabs_reopen_on_new_home(page)
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
        surface(phone, "home.this-week-phone")
        assert errors == [], f"WebKit JS errors: {errors}"
    finally:
        webkit.close()


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
    surface(page, "items.list-empty")
    page.fill("#search", "")
    goto_mode(page, "stores")
    assert not page.locator("#items-add").is_visible()
    goto_mode(page, "shopping")
    assert not page.locator("#toolbar").is_visible()
    goto_mode(page, "settings")
    assert not page.locator("#toolbar").is_visible()
    assert page.locator("#open-sheet").is_visible()


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
                       lambda: open_fill_options(pg), lambda: open_fill_history(pg), lambda: open_store_sheet(pg),
                       lambda: open_transcript_menu(pg)):
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
    # Home's richer captures are in the This week test; the other tabs' plain landing views are here.
    if mode != "dashboard":
        surface(page, {"shopping": "shop.carts", "audit": "audit.manual", "list": "items.list"}[mode])
