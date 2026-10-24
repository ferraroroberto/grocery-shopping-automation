"""Shop: the carts glance, Fill history, Mark purchase done and the run log."""

from __future__ import annotations

import pytest

import src.data as data
from tests.e2e._geometry import assert_min_target, assert_no_horizontal_overflow, assert_no_overlap
from tests.e2e._helpers import (
    LOADED_JS,
    goto_mode,
    open_fill_history,
    open_fill_options,
)
from tests.e2e._surface import surface


@pytest.mark.e2e
def test_fill_history_and_mark_done_on_a_phone(pw, server, tmp_path, monkeypatch):
    """#323: Shop's History row opens the last fill days, and Mark purchase done
    clears the red state everywhere at once while History keeps the fill. Runs on
    the WebKit iPhone profile against invented run records in a temp dir -- never
    the shared server's fill, which other tests read."""
    import json

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
def test_empty_automation_log_says_what_fills_it(page):
    """J-09: the idle run log names the control that fills it -- the carts
    glance's Fill carts (#258)."""
    goto_mode(page, "shopping")
    log = page.locator("#automation-log")
    log.wait_for(state="attached")
    text = log.text_content()
    assert "Fill carts" in text and "progress appears here" in text
    surface(page, "shop.run-log-idle")


@pytest.mark.e2e
def test_automation_start_conflict_toasts_and_leaves_the_button_ready(page):
    """#339: a 409 ("automation already running" -- another device started a
    run, or a double tap) from /api/automation/start used to reject silently:
    no toast, no status line, and the elapsed timer left armed with nothing
    actually running. The PWA must say so and never show a run in flight that
    never started."""
    detail = "automation already running"
    page.route(lambda url: "/api/automation/start" in url, lambda route: route.fulfill(
        status=409, content_type="application/json", body=f'{{"detail": "{detail}"}}'))
    goto_mode(page, "shopping")
    fill = page.locator("#pane-shopping #fill-carts")
    fill.wait_for()
    fill.locator("#automation-start").click()
    page.wait_for_function("d => document.querySelector('#toast')?.textContent === d", arg=detail)
    assert "error" in (page.locator("#toast").get_attribute("class") or "")
    assert fill.locator("#automation-elapsed").inner_text() == ""
    assert fill.locator("#automation-start").inner_text().strip() == "Fill carts"
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_automation_log_is_a_code_listing(page, theme):
    """#291 / COMP-17: the run log is the design.md `code-listing` frame in both
    themes -- 13px / 1.5 mono on the card surface and a hairline, never the
    12px caption on canvas-subtle (true black inside a dark card)."""
    page.evaluate("t => { localStorage.setItem('grocery.theme', t); document.documentElement.dataset.theme = t; }", theme)
    goto_mode(page, "shopping")
    page.locator("#automation-log").wait_for(state="attached")
    probe = page.evaluate("""() => {
      const log = getComputedStyle(document.querySelector('#automation-log'));
      const swatch = document.createElement('i');
      document.body.append(swatch);
      const of = (v) => { swatch.style.background = `var(${v})`; return getComputedStyle(swatch).backgroundColor; };
      const out = {
        size: log.fontSize, lineHeight: log.lineHeight, background: log.backgroundColor,
        border: log.borderTopColor, borderWidth: log.borderTopWidth,
        card: of('--card'), line: of('--line'), canvasSubtle: of('--card-off'),
      };
      swatch.remove();
      return out;
    }""")
    assert probe["size"] == "13px", probe
    assert probe["lineHeight"] == "19.5px", probe
    assert probe["background"] == probe["card"] != probe["canvasSubtle"], probe
    assert probe["borderWidth"] == "1px" and probe["border"] == probe["line"], probe


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
