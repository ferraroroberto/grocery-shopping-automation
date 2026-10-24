"""Audit: Voice (match, apply, stale review) and Manual's stepper failures."""

from __future__ import annotations

import pytest

import app.routers.audio as audio_router
from src.inventory_extract import ExtractionResult
from tests.e2e._helpers import (
    LIVE,
    UNMATCHED_PHRASE,
    goto_mode,
    open_transcript_menu,
)
from tests.e2e._surface import surface


TRANSCRIPT = "en la nevera, tengo dos yogures y un litro de leche. en el congelador, tres salmones."


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
    surface(page, "audit.transcript-menu")
    page.keyboard.press("Escape")
    menu.wait_for(state="hidden")
    assert page.locator("#pane-audit #audio-clear").count() == 0
    surface(page, "audit.voice")

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
    # The verb is a plain icon-button (fleet-config#1420): a glyph with a name
    # and a tooltip, no fill and no border at rest, a real 44px target.
    find = groups.first.locator(".item-row", has_text=UNMATCHED_PHRASE).locator(".voice-find")
    assert find.inner_text().strip() == ""
    assert UNMATCHED_PHRASE in find.get_attribute("aria-label")
    assert find.get_attribute("title") == find.get_attribute("aria-label")
    for theme in ("light", "dark"):
        page.evaluate("t => document.documentElement.setAttribute('data-theme', t)", theme)
        for width, height in ((390, 844), (1280, 900)):
            page.set_viewport_size({"width": width, "height": height})
            paint = find.evaluate(
                "el => { const s = getComputedStyle(el); const r = el.getBoundingClientRect();"
                " return {bg: s.backgroundColor, border: s.borderTopWidth, w: r.width, h: r.height}; }")
            where = f"{theme} {width}px"
            assert paint["bg"] == "rgba(0, 0, 0, 0)" and paint["border"] == "0px", where
            assert paint["w"] >= 44 and paint["h"] >= 44, where
    page.evaluate("() => document.documentElement.setAttribute('data-theme', 'light')")
    page.set_viewport_size({"width": 1100, "height": 950})
    surface(page, "audit.voice-results")
    find.click()
    assert page.locator("#pane-audit [data-mode='audit']").get_attribute("aria-pressed") == "true"
    assert page.input_value("#search") == UNMATCHED_PHRASE
    rows = page.locator("#pane-audit .item-row")
    assert rows.count() == 1 and "pulpo cocido" in rows.first.inner_text()
    surface(page, "audit.manual-find")
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
    surface(page, "audit.voice-applied")
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
def test_stale_audio_apply_shows_the_reason_and_drops_the_review(page, monkeypatch):
    """#313: Apply names the item for each id; when another device's delete moved
    the list the server refuses the batch (409), and the PWA says so and drops a
    review whose ids now point at other rows, instead of offering it again."""
    monkeypatch.setattr(audio_router, "extract", _stub_extract_named_but_uncounted)
    detail = "This list changed on another device; reload and try again."
    sent = []

    def refuse(route):
        sent.append(route.request.post_data_json)
        route.fulfill(status=409, content_type="application/json", headers={"X-Inventory-Stale": "1"},
                      body=f'{{"detail": "{detail}"}}')

    page.route("**/api/audio/apply", refuse)
    goto_mode(page, "audio")
    page.fill("#transcript", TRANSCRIPT)
    page.click("#match-transcript")
    page.wait_for_selector("[data-audio-count-idx]", timeout=120000)
    box = page.locator("[data-audio-count-idx]").first
    idx = box.get_attribute("data-audio-count-idx")
    box.fill("4")
    page.click("#apply-audio")
    page.wait_for_function("d => document.querySelector('#toast')?.textContent === d", arg=detail)
    body = sent[0]
    assert idx in body["updates"] and sorted(body["names"]) == sorted(body["updates"])
    assert all(body["names"].values())
    page.wait_for_selector("#voice-results", state="detached")
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
def test_stepper_save_failure_surfaces_the_server_detail(page):
    """#293: a failed stepper save (423 -- the spreadsheet is open in Excel) used
    to leave the status on "Saving..." with no toast and an unhandled rejection.
    The server's detail must reach the status line and an error toast."""
    detail = "Spreadsheet is open in Excel -- close it and retry"
    page.route(lambda url: "/current-delta?" in url, lambda route: route.fulfill(
        status=423, content_type="application/json", body=f'{{"detail": "{detail}"}}'))
    goto_mode(page, "audit")
    page.locator("#pane-audit .item-row [data-action='current-plus']").first.click()
    page.wait_for_function("d => document.querySelector('#toast')?.textContent === d", arg=detail)
    assert "error" in (page.locator("#toast").get_attribute("class") or "")
    assert page.locator("#status").text_content() == detail
    surface(page, "audit.save-failed")
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_stale_list_write_shows_the_reason_and_reloads(page):
    """#293: ids are row positions, so a stepper tap names the item it means and
    a stale list (another device deleted a row) gets a 409. The PWA must say so
    and re-read the inventory instead of leaving the stale rows up."""
    detail = "This list changed on another device; reload and try again."
    sent = []

    def refuse(route):
        sent.append(route.request.url)
        route.fulfill(status=409, content_type="application/json", headers={"X-Inventory-Stale": "1"},
                      body=f'{{"detail": "{detail}"}}')

    page.route(lambda url: "/current-delta?" in url, refuse)
    goto_mode(page, "audit")
    row = page.locator("#pane-audit .item-row").first
    with page.expect_request(lambda r: r.method == "GET" and r.url.endswith("/api/inventory")):
        row.locator("[data-action='current-plus']").click()
    page.wait_for_function("d => document.querySelector('#toast')?.textContent === d", arg=detail)
    assert "error" in (page.locator("#toast").get_attribute("class") or "")
    assert "name=" in sent[0]
    assert page._js_errors == [], f"JS errors: {page._js_errors}"
