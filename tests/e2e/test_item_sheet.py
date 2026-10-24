"""The item sheet: its pages, staged Save, delete confirm and the fallback store."""

from __future__ import annotations

import pytest

from tests.e2e._helpers import (
    goto_mode,
    open_add_sheet,
)
from tests.e2e._surface import surface


@pytest.mark.e2e
def test_item_forms_label_every_field(page):
    """The Add item sheet's form (#256) names all six fields (#213): the
    numbers read "Target" / "In stock" even when the box holds a value. The
    item sheet (#255) names the field on each of its pages the same way."""
    form = open_add_sheet(page).locator("#add-form")
    for label in ["Item", "Supermarket", "Zone", "Target", "In stock", "URL"]:
        assert form.get_by_label(label, exact=True).count() == 1, label
    surface(page, "items.add-sheet")
    page.keyboard.press("Escape")
    goto_mode(page, "list")
    page.locator("#pane-items .item-row .action-row-main").first.click()
    sheet = page.locator("#item-sheet")
    for to, label in (("zone", "Zone"), ("store", "Store"), ("link", "Buy link"), ("name", "Name")):
        sheet.locator(f"[data-page-to='{to}']").click()
        assert sheet.locator(f"[data-page='{to}']").get_by_label(label, exact=True).count() == 1, label
        surface(page, f"items.item-sheet-{to}")
        sheet.locator("[data-sheet-back]").click()
    surface(page, "items.item-sheet")
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


def _check_buy_link_opens_from_the_sheet(page, name, row):
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

    _check_buy_link_opens_from_the_sheet(page, name, row)

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
    surface(page, "items.delete-confirm")
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
def test_item_sheet_sets_and_clears_a_fallback(page, server):
    """The item sheet's Fallback if short page (#284): pick a store and paste a
    product link, Save fallback writes both cells at once, a link that is not a
    product page is flagged, and Clear fallback (a plain action in the page, no
    trailing remove on the row) blanks both. Esc leaves an unsaved form unwritten."""
    name = "zzz e2e fallback"
    created = page.request.post(
        f"{server.url}/api/items",
        data={"super": "mercadona", "lugar": "nevera", "comida": name, "cantidad": 2, "tenemos": 0, "buscador": ""},
    )
    assert created.ok, created.text()
    page.reload()
    page.wait_for_selector("[data-tab='audit']")
    product = "https://www.carrefour.es/supermercado/fixture-pavo/R-FIX-pavo/p"
    search = "https://www.carrefour.es/search?q=pavo"

    def record():
        payload = page.request.get(f"{server.url}/api/inventory").json()
        cols = payload["columns"]
        return next(i for i in payload["items"] if i[cols["comida"]] == name)

    sheet = _open_sheet_from(page, "list", name)
    value = sheet.locator("[data-link-value='fallback']")
    assert value.inner_text() == "None"
    # A link row like the others: no trailing icon button on it (the row-verb rule).
    assert sheet.locator("[data-page-to='fallback']").locator("xpath=..").locator(".icon-button").count() == 0
    sheet.locator("[data-page-to='fallback']").click()
    form = sheet.locator("[data-page='fallback'] [data-fallback-form]")
    assert form.locator("[data-fallback-clear]").count() == 0  # nothing to clear yet
    surface(page, "items.item-sheet-fallback")

    # Unsaved typing is dropped with the sheet: nothing is written.
    form.locator("[data-fallback-url]").fill(product)
    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")
    assert record()["fallback"] is None

    sheet = _open_sheet_from(page, "list", name)
    sheet.locator("[data-page-to='fallback']").click()
    form = sheet.locator("[data-page='fallback'] [data-fallback-form]")
    form.locator("[data-fallback-store]").select_option("carrefour")
    form.locator("[data-fallback-url]").fill(product)
    with page.expect_response(lambda r: r.request.method == "PUT" and "/fallback?name=" in r.url) as resp:
        form.get_by_role("button", name="Save fallback").click()
    assert resp.value.request.post_data_json == {"store": "carrefour", "url": product}
    assert resp.value.ok
    saved = record()
    assert saved["fallback"] == {"store": "carrefour", "url": product, "kind": "product"}
    assert (saved["fallback_super"], saved["fallback_url"]) == ("carrefour", product)
    assert "Fallback saved" in sheet.locator(".stores-dialog-status").inner_text()
    assert "Carrefour" in value.inner_text() and value.get_attribute("data-tone") is None
    assert sheet.locator("[data-page='fallback'] .review-warn").count() == 0

    # A search page is saved as typed but flagged, never posed as a product link.
    form = sheet.locator("[data-page='fallback'] [data-fallback-form]")
    form.locator("[data-fallback-url]").fill(search)
    form.get_by_role("button", name="Save fallback").click()
    flag = sheet.locator("[data-page='fallback'] .review-warn")
    flag.wait_for()
    assert "search page" in flag.inner_text()
    assert record()["fallback"]["kind"] == "search"
    assert value.get_attribute("data-tone") == "attention" and "check link" in value.inner_text()
    surface(page, "items.item-sheet-fallback-flagged")

    # Clear fallback blanks both cells.
    sheet.locator("[data-fallback-clear]").click()
    sheet.locator("[data-page='fallback'] [data-fallback-clear]").wait_for(state="detached")
    cleared = record()
    assert cleared["fallback"] is None
    assert (cleared["fallback_super"] or "", cleared["fallback_url"] or "") == ("", "")
    assert value.inner_text() == "None"
    assert sheet.locator("[data-fallback-store]").input_value() == ""
    assert sheet.locator("[data-fallback-url]").input_value() == ""

    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")
    delete = page.request.get(f"{server.url}/api/inventory").json()
    item_id = next(i["id"] for i in delete["items"] if i[delete["columns"]["comida"]] == name)
    assert page.request.delete(f"{server.url}/api/items/{item_id}").ok
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_fallback_typing_survives_the_store_detail_load(page, server):
    """#293: typing in the item sheet's Fallback form while the store detail is
    still loading must not be repainted away when the load resolves (the guard
    read dataset.touched, which is "" -- falsy -- once the attribute is set)."""
    name = "zzz e2e fallback typing"
    created = page.request.post(
        f"{server.url}/api/items",
        data={"super": "mercadona", "lugar": "nevera", "comida": name, "cantidad": 2, "tenemos": 0, "buscador": ""},
    )
    assert created.ok, created.text()
    page.reload()
    page.wait_for_selector("[data-tab='audit']")
    held = []
    page.route("**/api/items/*/store-detail", lambda route: held.append(route))

    sheet = _open_sheet_from(page, "list", name)
    sheet.locator("[data-page-to='fallback']").click()
    url = sheet.locator("[data-fallback-url]")
    url.fill("https://www.carrefour.es/supermercado/fixture-pavo/R-FIX-pavo/p")
    for _ in range(100):
        if held:
            break
        page.wait_for_timeout(50)
    assert held, "the store detail request should be waiting"
    with page.expect_response("**/api/items/*/store-detail"):
        for route in held:
            route.continue_()
    page.wait_for_timeout(300)  # let the resolved openReview run its repaint guard
    assert url.input_value() == "https://www.carrefour.es/supermercado/fixture-pavo/R-FIX-pavo/p"

    page.unroute("**/api/items/*/store-detail")
    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")
    payload = page.request.get(f"{server.url}/api/inventory").json()
    item_id = next(i["id"] for i in payload["items"] if i[payload["columns"]["comida"]] == name)
    assert page.request.delete(f"{server.url}/api/items/{item_id}").ok
    assert page._js_errors == [], f"JS errors: {page._js_errors}"
