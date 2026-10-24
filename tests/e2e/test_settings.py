"""Settings: the gear, store logins and email watch, text size, copy link, export and the usage log."""

from __future__ import annotations

import pytest

import app.routers.system as system_router
from app.api import app
from tests.e2e._helpers import (
    LOADED_JS,
    goto_mode,
)
from tests.e2e._surface import surface


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
    surface(page, "settings.main")

    # Email watch: its sheet holds what the folded card did.
    page.click("#open-email-watch")
    email = page.locator("#email-watch-sheet")
    email.locator("#email-check-now, .panel-status.error").first.wait_for()
    surface(page, "settings.email-watch")
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
    logins.wait_for()
    surface(page, "settings.store-logins")
    logins.locator("#check-logins").click()
    logins.locator(".login-rows .action-row").nth(1).wait_for()
    assert logins.locator(".login-rows .chip[data-tone='attention']").inner_text() == "Logged out"
    assert logins.locator(".login-ok").count() == 1
    surface(page, "settings.store-logins-checked")
    page.wait_for_function("document.querySelector('#toast')?.textContent === '1 store logged out'")
    assert "error" not in (page.locator("#toast").get_attribute("class") or "")
    page.keyboard.press("Escape")
    logins.wait_for(state="hidden")
    assert page.locator("#store-logins-value").inner_text() == "1 logged out"
    assert page.locator("#store-logins-value").get_attribute("data-tone") == "attention"
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_copy_link_falls_back_without_a_secure_context(page):
    """#339: the documented plain-HTTP LAN access (docs/running-and-access.md)
    has no secure context, so `navigator.clipboard` is undefined there and
    "Copy link" used to throw unhandled with nothing on screen, instead of
    falling back to a selected textarea + `execCommand('copy')` the way
    Prices' "Copy steps" already does."""
    goto_mode(page, "settings")
    page.evaluate("() => Object.defineProperty(navigator, 'clipboard', { value: undefined, configurable: true })")
    page.click("#copy-link")
    page.wait_for_function("document.querySelector('#toast')?.textContent === 'Link copied'")
    assert "error" not in (page.locator("#toast").get_attribute("class") or "")
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_export_csv_downloads_with_a_token_configured(browser, server, monkeypatch):
    """#293: Export CSV was a bare navigation carrying no bearer token, so off the
    PC (the cf-ray header makes the loopback test server look like tunnel
    traffic) it landed on a 401 JSON page. It must fetch with the stored token
    and save the file."""
    cfg = app.state.webapp_config
    monkeypatch.setattr(cfg, "auth_token", "e2e-token")
    context = browser.new_context(viewport={"width": 1100, "height": 950}, extra_http_headers={"cf-ray": "e2e-LHR"})
    context.add_init_script("localStorage.setItem('grocery.authToken', 'e2e-token')")
    try:
        pg = context.new_page()
        pg.goto(server.url)
        pg.wait_for_function(LOADED_JS)
        goto_mode(pg, "settings")
        with pg.expect_download() as download:
            pg.click("#export-csv")
        assert download.value.suggested_filename == "inventory.csv"
        with open(download.value.path(), encoding="utf-8") as saved:
            assert saved.readline().strip()
    finally:
        context.close()


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
