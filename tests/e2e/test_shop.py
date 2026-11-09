"""Shop: the carts glance, Fill carts options, store sheet, Fill history and Mark purchase done."""

from __future__ import annotations

import json

import pytest

import src.data as data
from tests.e2e._geometry import (
    assert_min_target,
    assert_no_horizontal_overflow,
    assert_no_overlap,
)
from tests.e2e._helpers import (
    LOADED_JS,
    goto_mode,
    open_fill_history,
    open_fill_options,
    open_store_sheet,
)
from tests.e2e._surface import surface


def _check_shop_tab_badge(page):
    # The last fill left an item out, and Shop hasn't been seen since -- the
    # Shop tab carries the count badge, spoken as part of its name.
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


def _check_idle_log_and_start_conflict(page, fill):
    # The idle run log names the control that fills it -- the carts glance's Fill carts.
    log = page.locator("#automation-log")
    log.wait_for(state="attached")
    text = log.text_content()
    assert "Fill carts" in text and "progress appears here" in text
    surface(page, "shop.run-log-idle")

    # A 409 ("automation already running" -- another device started a run, or a
    # double tap) must toast, and never show a run in flight that never started.
    detail = "automation already running"
    matches_start = lambda url: "/api/automation/start" in url  # noqa: E731
    page.route(matches_start, lambda route: route.fulfill(
        status=409, content_type="application/json", body=f'{{"detail": "{detail}"}}'))
    fill.locator("#automation-start").click()
    page.wait_for_function("d => document.querySelector('#toast')?.textContent === d", arg=detail)
    assert "error" in (page.locator("#toast").get_attribute("class") or "")
    assert fill.locator("#automation-elapsed").inner_text() == ""
    assert fill.locator("#automation-start").inner_text().strip() == "Fill carts"
    page.unroute(matches_start)


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
    # Complete is the row's leading check: pressed, and the name struck.
    got = group.locator(".item-row", has=page.locator("[data-action='undo-buy']")).first
    assert got.locator(".action-row-check").get_attribute("aria-pressed") == "true"
    assert got.locator(".action-row-title s").count() == 1
    # The check is named for its verb, the link for what it opens.
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
    # offsets and Unmark all live in the store sheet.
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
    # No Remove at rest; the Edit toggle in the Added here header reveals it,
    # and removing the last quick-add leaves edit mode.
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


@pytest.mark.e2e
def test_fill_history_and_mark_done_on_a_phone(pw, server, tmp_path, monkeypatch):
    """Shop's History row opens the last fill days, and Mark purchase done
    clears the red state everywhere at once while History keeps the fill. Runs on
    the WebKit iPhone profile against invented run records in a temp dir -- never
    the shared server's fill, which the other Shop test reads."""
    runs = tmp_path / "purchase_logs" / "runs"
    runs.mkdir(parents=True)

    def record(stamp: str, store: str, items: list[tuple[str, str]], mode: str = "keep") -> None:
        (runs / f"{stamp}_{store}.json").write_text(json.dumps({
            "run": stamp, "store": store, "cart_mode": mode,
            "items": [{"comida": name, "status": status} for name, status in items],
        }), encoding="utf-8")

    record("2026-10-07T180000", "carrefour", [("invented rice", "added_verified")])
    record("2026-10-14T191701", "mercadona", [("invented soup", "added_verified"), ("invented broth", "out_of_stock")])
    record("2026-10-14T191701", "carrefour",
           [("invented rice", "added_verified"), ("invented oats", "added_unverified")], mode="clean")
    monkeypatch.setitem(data.CONFIG["automation"], "purchase_logs_dir", str(tmp_path / "purchase_logs"))

    webkit = pw.webkit.launch()
    try:
        phone = webkit.new_page(**pw.devices["iPhone 13"])
        errors: list[str] = []
        phone.on("pageerror", lambda exc: errors.append(str(exc)))
        phone.goto(server.url)
        phone.wait_for_function(LOADED_JS)
        goto_mode(phone, "shopping")
        fill = phone.locator("#pane-shopping #fill-carts")
        fill.wait_for()
        mark = fill.locator("#automation-mark-done")
        mark.wait_for()
        assert mark.inner_text().strip() == "Mark purchase done"
        assert phone.locator("#shop-context").inner_text().startswith("1 not in cart")
        assert fill.locator("#automation-history-value").inner_text() == "Wed 14 Oct"

        # History: newest day first, a line per store, only the misses folded.
        sheet = open_fill_history(phone)
        days = sheet.locator(".fill-day")
        assert days.locator(".fill-day-title").all_inner_texts() == ["Wed 14 Oct · 19:17", "Wed 7 Oct · 18:00"]
        assert sheet.locator(".chip", has_text="Done").count() == 0
        newest = days.first.locator(".fill-store")
        assert newest.locator(".fill-store-name").all_inner_texts() == ["Carrefour", "Mercadona"]
        assert "2 in cart" in newest.first.inner_text() and "Clean cart" in newest.first.inner_text()
        assert newest.first.locator(".chip[data-tone='attention']").inner_text() == "1 not confirmed"
        assert newest.nth(1).locator(".chip[data-tone='danger']").inner_text() == "1 not in cart"
        assert newest.first.locator("details").count() == 0
        broth = newest.nth(1).locator("details")
        broth.locator("summary").click()
        assert " ".join(broth.locator("li").inner_text().split()) == "invented broth Out of stock"
        assert_no_horizontal_overflow(phone)
        assert_min_target(sheet.locator("button:visible, summary:visible"))
        assert_no_overlap(sheet.locator("button:visible, summary:visible"))
        surface(phone, "shop.fill-history")
        sheet.locator(".detail-save-btn").click()
        sheet.wait_for(state="hidden")

        # Mark done: one tap, a toast, and every red surface lets go together.
        mark.click()
        phone.locator("#toast").filter(has_text="Marked done. Still in History.").wait_for()
        mark.wait_for(state="hidden")
        assert fill.locator("#carts-last-fill").inner_text() == "Last fill Wed 14 Oct · done"
        assert fill.locator("#carts-stores .chip").count() == 0
        assert "in cart" not in fill.locator("#carts-stores").inner_text()
        assert not phone.locator("#shop-context").inner_text().startswith("1 not in cart")
        assert phone.locator("nav.tabs [data-tab='shopping'] .tab-badge").count() == 0
        surface(phone, "shop.carts-done")
        goto_mode(phone, "dashboard")
        assert phone.locator("#week-carts").inner_text() == "Last fill Wed 14 Oct · done"
        assert phone.locator("#week-carts").get_attribute("data-tone") is None
        assert phone.locator("#status").inner_text() != "1 not in cart"
        surface(phone, "home.this-week-done")

        # History keeps the cleared fill, with its outcome and a Done chip.
        sheet = open_fill_history(phone)
        assert sheet.locator(".fill-day").first.locator(".chip", has_text="Done").count() == 1
        assert sheet.locator(".fill-day").first.locator(".chip[data-tone='danger']").inner_text() == "1 not in cart"
        surface(phone, "shop.fill-history-done")
        phone.keyboard.press("Escape")
        sheet.wait_for(state="hidden")

        # Another device (a fresh load) reads the same mark from the server.
        phone.reload()  # reopens on Shop, the tab it was left on
        phone.locator("#carts-last-fill").filter(has_text="Last fill Wed 14 Oct · done").wait_for()
        assert phone.locator("#automation-mark-done").is_hidden()
        goto_mode(phone, "dashboard")
        phone.wait_for_function(LOADED_JS)
        assert phone.locator("#week-carts").inner_text() == "Last fill Wed 14 Oct · done"
        assert errors == [], f"WebKit JS errors: {errors}"
    finally:
        webkit.close()


@pytest.mark.e2e
def test_shop_carts_options_and_store_sheet(page):
    """Shop leads with the carts glance (the last fill's miss as a danger chip and
    a tab badge, cleared once Shop is seen); a rejected Fill carts start toasts
    and leaves the button ready. The Options sheet names what will run in words,
    and a clean-cart pick blocks Fill carts until confirmed. Completing a row
    keeps the picks, a quick-add lives in its store group, and the store sheet
    holds the cart offsets, Remove and Unmark."""
    shop_tab = _check_shop_tab_badge(page)
    fill = _check_carts_glance(page, shop_tab)
    surface(page, "shop.carts-miss")
    _check_idle_log_and_start_conflict(page, fill)
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
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


