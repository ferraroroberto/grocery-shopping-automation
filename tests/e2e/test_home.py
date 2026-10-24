"""Home: the This week card and the search over every item."""

from __future__ import annotations

import pytest

from tests.e2e._surface import surface


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
    surface(page, "home.this-week")
    # Nothing under the search until something is typed.
    assert page.locator("#pane-inventory .item-row").count() == 0
    page.fill("#search", "burguer")
    titles = page.locator("#pane-inventory .item-row .action-row-title").all_inner_texts()
    assert titles and all("burguer" in t.lower() for t in titles)
    surface(page, "home.search-results")
    page.fill("#search", "zzz nothing matches")
    assert page.locator("#pane-inventory .empty-state").inner_text().strip() == "No matching items."
    surface(page, "home.search-empty")
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
