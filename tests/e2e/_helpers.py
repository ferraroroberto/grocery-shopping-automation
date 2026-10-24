"""Constants and page-navigation helpers shared by the PWA e2e story files."""

from __future__ import annotations

import os

from src.inventory_extract import ExtractionResult

LIVE = os.environ.get("GROCERY_E2E_LIVE") == "1"
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


def open_fill_history(page):
    """Shop -> the carts glance's History row -> the Fill history sheet (#323)."""
    if not page.locator("#pane-shopping #fill-carts").is_visible():
        goto_mode(page, "shopping")
    page.click("#automation-history")
    sheet = page.locator("#fill-history")
    sheet.wait_for()
    sheet.locator(".fill-day").first.wait_for()
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


# A mention no row took, as speech says it: Manual's word search (#259) finds
# the fixture's "pulpo cocido" from it.
UNMATCHED_PHRASE = "pulpo a la gallega"


def stub_extract(transcript, df, *, base_url, model, max_tokens, timeout):
    first = int(df.index[0])
    return ExtractionResult(
        items=[{"idx": first, "count": 2, "zone": str(df.loc[first, "lugar"]), "evidence": "dos"}],
        zones_mentioned=[str(df.loc[first, "lugar"])],
        unmatched_mentions=[{"phrase": UNMATCHED_PHRASE, "idx": None, "approx_count": None, "note": "no match",
                             "comida": "", "resolved_by": "", "match_score": None}],
        raw_text="{}",
    )
