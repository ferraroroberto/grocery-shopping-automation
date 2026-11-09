"""The app shell: tabs and page headers, the toolbar, phone geometry and each tab's landing view."""

from __future__ import annotations

import pytest

from tests.e2e._geometry import (
    assert_min_target,
    assert_no_horizontal_overflow,
    assert_no_overlap,
)
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

_ALL_MODES = ["dashboard", "audit", "list", "stores", "shopping", "audio", "settings"]

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


def _check_tabs_and_page_headers(page):
    tabs = page.locator("nav.tabs .tab")
    assert tabs.count() == 4
    # A label may also carry the badge's spoken count (", 1 not in cart").
    assert [t.split(",")[0] for t in tabs.locator(".tab-label").all_inner_texts()] == ["Home", "Shop", "Audit", "Items"]
    assert page.locator("nav.tabs [data-tab='settings']").count() == 0

    # Every pane opens on its own page header, not a bare control row -- the
    # vendored home-head (icon + tab-naming title + context line + theme toggle
    # + Settings gear) as each pane's first element, and only the active
    # pane's is ever visible at once.
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

    # Audit opens on Voice, Voice leading its two segments; a hop to Manual
    # lasts the session, never the next launch.
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


def _check_standalone_page_head_meta(page):
    """The installed iOS PWA needs the opaque `default` status bar + viewport-fit=cover,
    or the nav bar rides the viewport growth after a cold launch (_vendored/nav
    README). Desktop browsers can't reproduce that, so assert the pair here."""
    status_bar = page.locator('meta[name="apple-mobile-web-app-status-bar-style"]')
    assert status_bar.get_attribute("content") == "default"
    assert "viewport-fit=cover" in page.locator('meta[name="viewport"]').get_attribute("content")


def _check_search_only_on_filterable_modes(page):
    goto_mode(page, "dashboard")
    assert page.locator("#toolbar").is_visible()
    assert not page.locator("#items-add").is_visible()
    # Items' list: the search filters the one zone-grouped list, and Add item
    # rides the search's row on this mode only.
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


def _theme_toggle_works_from_every_header(page):
    # Theme toggle works from every pane's own header, not just Home's
    # (#theme-toggle id stays on Home's for back-compat; the rest share the
    # .theme-toggle class).
    goto_mode(page, "shopping")
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


@pytest.mark.e2e
def test_shell_tabs_headers_and_landing_views(page):
    """The four tabs, each pane's own page header and landing view, and every
    capability of the retired Search/Auto tabs in reach -- product search in
    Items' Add item sheet, Fill carts at the bottom of Shop, Email Watch in
    Settings (the header gear, never a tab). Items is two segments and Audit
    opens on Voice. A saved retired tab or Items mode reopens on its new
    home instead of falling back to Home or an empty pane. Run in this
    order: the segment checks need a session that has not yet visited Audit."""
    _check_tabs_and_page_headers(page)
    _check_items_and_audit_segments(page)
    for mode in _ALL_MODES:
        goto_mode(page, mode)
    _check_standalone_page_head_meta(page)
    _check_search_only_on_filterable_modes(page)
    # Below the page header, a tab's first heading-or-control in reading order
    # is a heading -- never a mode pill, the search box or a collapsible card
    # header (a heading inside a <summary> reads as a control).
    for mode, tab, name in (("dashboard", "inventory", None), ("shopping", "shopping", "shop.carts"),
                            ("audit", "audit", "audit.manual"), ("list", "items", "items.list")):
        goto_mode(page, mode)
        page.wait_for_function(f"({_FIRST_AFTER_HEADER_JS})('{tab}') !== null")
        first = page.evaluate(_FIRST_AFTER_HEADER_JS, tab)
        assert first.startswith("h2:"), f"{tab} opens on {first}"
        if name:
            surface(page, name)
    _theme_toggle_works_from_every_header(page)
    _retired_tabs_reopen_on_new_home(page)
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


def _check_chromium_phone_geometry(browser, server):
    """Rendered geometry at phone width (design-lint `rendered-leg`): no view
    scrolls sideways, and the nav tabs and the header buttons keep a >=44px
    effective target without overlapping."""
    pg = browser.new_page(viewport={"width": 390, "height": 844})
    try:
        pg.goto(server.url)
        pg.wait_for_selector("[data-tab='audit']")
        pg.wait_for_function(LOADED_JS)
        for mode in ["dashboard", "shopping", "audit", "audio", "list", "stores", "settings"]:
            goto_mode(pg, mode)
            assert_no_horizontal_overflow(pg)
            # --control-h is the spec's 36px: every control still presents a >=44px
            # effective target, via the shared ::before hit area or real 44px geometry.
            assert_min_target(pg.locator("button:visible, select:visible, textarea:visible, summary:visible, "
                                         "input:not([type=hidden]):visible"))
            if mode in ("audit", "list", "stores"):
                # One stepper (or value, or Prices' picker) per row leaves the
                # item's name the room to be read at 390px.
                widths = pg.locator("main > .pane:not([hidden]) .item-row .action-row-title").evaluate_all(
                    "els => els.map((e) => e.getBoundingClientRect().width)")
                assert widths and min(widths) >= 150, f"{mode}: name squeezed to {min(widths or [0]):.0f}px"
        # With every checklist zone open, a zone's summary and the switches
        # under it keep their hit areas apart (TOUCH-02).
        goto_mode(pg, "audio")
        pg.locator(".checklist-zone").evaluate_all("els => els.forEach((d) => { d.open = true; })")
        assert_no_overlap(pg.locator(".voice-checklist summary, .voice-checklist .toggle"))
        # Items' list: the search and Add item share one row at 390px,
        # side by side, the search still wider than the button.
        goto_mode(pg, "list")
        search, add = pg.locator("#search").bounding_box(), pg.locator("#items-add").bounding_box()
        assert abs(search["y"] - add["y"]) < 1 and search["x"] + search["width"] <= add["x"], (search, add)
        assert search["width"] > add["width"] and add["x"] + add["width"] <= 390, (search, add)

        def open_item_sheet():
            pg.locator("#pane-items .item-row .action-row-main").first.click()
            return pg.locator("#item-sheet")

        # Shop's Fill carts options and store sheets too, and an open store
        # group's "Add to <store>" row keeps its controls apart.
        goto_mode(pg, "shopping")
        group = pg.locator("#pane-shopping details[data-store='mercadona']")
        group.locator("> summary").click()
        add_row = group.locator(".store-add-row")
        assert_min_target(add_row.locator("button:visible, input:visible"))
        assert_no_overlap(add_row.locator("button:visible, input:visible"))
        assert_no_horizontal_overflow(pg)
        # Every sheet: each button keeps the floor without overlapping, and no
        # sheet scrolls sideways.
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
        # Home fits one phone screen -- the This week card and the search end
        # above the bottom nav pill (the viewport's edge where the nav is
        # drawn as top tabs), nothing to scroll for.
        assert pg.evaluate(_HOME_FITS_JS), pg.evaluate(_HOME_RECTS_JS)
    finally:
        pg.close()


@pytest.mark.e2e
def test_phone_geometry_fits_and_targets_meet_the_floor(browser, pw, server):
    """Phone width on both engines: Chromium at 390px sweeps every view and
    sheet for sideways scroll, touch targets and overlaps; WebKit (iPhone,
    390px) checks the four tabs keep full labels and 44px targets."""
    _check_chromium_phone_geometry(browser, server)
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
        # Home -- the This week card and the search -- fits above the pill.
        assert phone.evaluate(_HOME_FITS_JS), phone.evaluate(_HOME_RECTS_JS)
        surface(phone, "home.this-week-phone")
        assert errors == [], f"WebKit JS errors: {errors}"
    finally:
        webkit.close()
