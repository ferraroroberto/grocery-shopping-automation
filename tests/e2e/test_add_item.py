"""The Add item sheet: adding by hand, new zones and stores, and product search."""

from __future__ import annotations

import pytest

from tests.e2e._helpers import (
    open_add_sheet,
)
from tests.e2e._surface import surface


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
def test_add_item_product_search(page, server):
    """The Add item sheet opens on the product search (#256; #182, it was the
    Search tab). The store search is stubbed at the HTTP seam (no Chrome): a
    new term's "Use" pre-fills Add by hand below it and nothing is saved until
    the sheet's Add item, which closes it; reopened, the run is still there
    with that card marked Added. A term already on the list keeps its confirm
    row -> /select. Last in this file on purpose: it adds a row to the shared
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
         # The real server always sends the row's actual name (#339: the
         # frontend echoes it back on /select, so the server can refuse a
         # pick whose row moved under it — a mismatched value here would
         # 409 the confirm below as stale).
         "inventory_name": existing["comida"],
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
    surface(page, "items.add-sheet-results")

    new_card.locator("[data-action='search-use']").click()
    form = sheet.locator("#add-form")
    assert form.locator("[name='comida']").input_value() == "zzz sandia e2e"
    assert form.locator("[name='super']").input_value() == "carrefour"
    assert form.locator("[name='buscador']").input_value() == new_url
    assert form.locator("[name='cantidad']").input_value() == "1"
    assert "Filled in Add by hand below" in search.locator("#search-status").inner_text()
    surface(page, "items.add-sheet-prefilled")
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
    assert f"Matches your item {existing['comida']}" in page.locator(".search-group-hint").inner_text()

    old_card = page.locator(".candidate", has_text="Existing e2e")
    old_card.locator("[data-action='search-use']").click()
    old_card.locator("[data-action='search-add-new']").click()  # → Add by hand, under the searched term
    assert form.locator("[name='comida']").input_value() == existing["comida"]
    assert form.locator("[name='buscador']").input_value() == existing["buscador"]
    old_card.locator("[data-action='search-use']").click()
    with page.expect_response(lambda r: r.url.endswith("/api/product-search/select")) as resp:
        old_card.locator("[data-action='search-confirm']").click()
    assert resp.value.ok and resp.value.request.post_data_json["inventory_idx"] == existing["id"]
    assert resp.value.request.post_data_json["inventory_name"] == existing["comida"]  # #339
    old_card.locator("button:has-text('Updated')").wait_for()
    assert page._js_errors == [], f"JS errors: {page._js_errors}"
