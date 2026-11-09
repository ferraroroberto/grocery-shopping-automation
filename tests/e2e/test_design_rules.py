"""Design rules the rendered app keeps: its copy, and the vendored recipes in both themes."""

from __future__ import annotations

import re

import pytest

from tests.e2e._helpers import (
    LOADED_JS,
    goto_mode,
    open_add_sheet,
    open_benchmark_sheet,
    open_fill_options,
)
from tests.e2e._surface import surface

_ALL_MODES = ["dashboard", "shopping", "audit", "audio", "list", "stores", "settings"]

_VISIBLE_COPY_JS = """
() => {
  const pane = [...document.querySelectorAll('main > section.pane')].find((p) => !p.hidden);
  const options = [...pane.querySelectorAll('option')].map((o) => o.textContent);
  return { text: pane.innerText, options };
}
"""
# Words a shopper would not use. The two sibling apps the user must start are
# named by their folder names, so those are masked before matching.
_DEV_TERMS = (
    r"\b(LLM|hub|whisper|tray|SSE|endpoint|argv|JSON|API|snake_case|Command)\b"
    r"|:\d{4}\b|\b[a-z]+_[a-z]+\b"
)
_CASE_JS = r"""
() => [...document.querySelectorAll('main > section.pane:not([hidden]) button, main > section.pane:not([hidden]) summary, main > section.pane:not([hidden]) h1, main > section.pane:not([hidden]) h2')]
  .filter((e) => e.getClientRects().length)
  .filter((e) => !e.matches('.action-row-main'))
  .map((e) => e.textContent.replace(/\s+/g, ' ').trim())
  .filter(Boolean)
"""
_PROPER = {"Ametller", "Origen", "Mercadona", "Carrefour", "CSV"}


def _check_copy_rules(page):
    """No internal identifier, port or developer vocabulary in what a shopper
    reads, and buttons, summaries and headings in sentence case (first word
    capitalised, the rest lower, store names and CSV aside), on every view --
    the Audio audit's service status included (the e2e server has no voice
    recorder / hub / whisper, so its error banner is on screen). An item row's
    tap target is skipped: its text is the item's own name."""
    developer, mixed = {}, {}
    for mode in _ALL_MODES:
        goto_mode(page, mode)
        page.wait_for_timeout(400)
        copy = page.evaluate(_VISIBLE_COPY_JS)
        text = "\n".join([copy["text"], *copy["options"]])
        text = text.replace("local-llm-hub", "").replace("voice-transcriber", "")
        found = sorted({m.group(0) for m in re.finditer(_DEV_TERMS, text)})
        if found:
            developer[mode] = found
        for label in page.evaluate(_CASE_JS):
            words = [w for w in label.split(" ")[1:] if w and w[0].isalpha()]
            if [w for w in words if w[0].isupper() and w not in _PROPER and not w.isupper()]:
                mixed.setdefault(mode, []).append(label)
    assert not developer, f"developer terms visible: {developer}"
    assert not mixed, f"Title Case labels: {mixed}"


def _check_color_scheme_follows_chosen_theme(browser, server, chosen, os_theme):
    """Native controls (option lists, date pickers, scrollbars) draw in the app's chosen
    theme: `color-scheme` on :root must follow `data-theme` even when the OS says otherwise,
    and native inputs inherit it."""
    ctx = browser.new_context(color_scheme=os_theme)
    ctx.add_init_script(f"localStorage.setItem('grocery.theme', '{chosen}')")
    pg = ctx.new_page()
    pg.goto(server.url)
    pg.wait_for_selector("[data-tab='audit']")
    scheme = pg.evaluate(
        """() => {
          const probe = (tag, type) => { const el = document.createElement(tag); if (type) el.type = type;
            document.body.appendChild(el); const v = getComputedStyle(el).colorScheme; el.remove(); return v; };
          return { root: getComputedStyle(document.documentElement).colorScheme,
                   theme: document.documentElement.dataset.theme,
                   select: probe('select'), date: probe('input', 'date') };
        }"""
    )
    ctx.close()
    assert scheme == {"root": chosen, "theme": chosen, "select": chosen, "date": chosen}


def _use_theme(page, theme):
    """Reload on `theme`, so each theme starts from a clean page."""
    page.evaluate("t => localStorage.setItem('grocery.theme', t)", theme)
    page.reload()
    page.wait_for_selector("[data-tab='audit']")
    goto_mode(page, "dashboard")  # the page reopens on the tab it was left on
    page.wait_for_function(LOADED_JS)
    page.evaluate("t => { document.documentElement.dataset.theme = t; }", theme)


def _check_automation_log_is_a_code_listing(page):
    """The run log is the design.md `code-listing` frame in both themes -- 13px / 1.5
    mono on the card surface and a hairline, never the 12px caption on canvas-subtle
    (true black inside a dark card)."""
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


# What every switch that is on must draw: the theme's accent-fill (design.md
# switch.trackOn) -- resolved from the token, never hard-coded.
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


def _check_on_switch_is_the_accent(page):
    """An on switch is the accent blue in both themes, never success green."""
    sheet = open_fill_options(page)
    dry_run = sheet.locator("#automation-dry-run")
    if dry_run.get_attribute("aria-checked") != "true":
        dry_run.click()  # client-side only: it just rewrites "what will run"
    page.wait_for_timeout(400)  # the track's 0.15s background transition must settle
    probe = page.evaluate(_ACCENT_PROBE)
    assert probe["on"], "no switch is on to measure"
    assert set(probe["on"]) == {probe["accent"]}, probe
    page.keyboard.press("Escape")
    sheet.wait_for(state="hidden")


# Every dropdown, inline input and labelled button in one open dialog, checked
# against the vendored recipes (select-native/, button/, modal/), with the
# expected values resolved from the theme's own tokens -- never hard-coded.
# A button must match one button.css tier (or the shared disabled recipe; a
# .danger one, the outlined destructive recipe: transparent fill); glyph-only controls (.icon-button),
# switches and list rows (.action-row-main, the item sheet's link rows) are out
# of scope; the x close is only checked for being unpainted.
_DIALOG_CONTROLS_PROBE = r"""(id) => {
  const dialog = document.getElementById(id);
  const card = dialog.querySelector('.detail-card');
  const probe = document.createElement('div');
  card.appendChild(probe);
  const color = (name) => { probe.style.cssText = `background-color: var(${name})`; return getComputedStyle(probe).backgroundColor; };
  const len = (name) => { probe.style.cssText = `width: var(${name})`; return parseFloat(getComputedStyle(probe).width); };
  const tiers = {
    primary: ['--accent-fill', '--accent-fg', '--accent-border-strong'],
    tint: ['--accent-soft', '--accent-text', '--accent-border-soft'],
    surface: ['--input-bg', document.documentElement.dataset.theme === 'dark' ? '--ink' : '--muted', '--line'],
    disabled: ['--input-bg', '--muted', '--line'],
    danger: ['--card', '--danger-text', '--deficit'],
  };
  const expect = Object.fromEntries(Object.entries(tiers).map(([k, [bg, fg, border]]) =>
    [k, { bg: color(bg), fg: color(fg), border: color(border) }]));
  // Outlined (project-scaffolding#379): a transparent fill, never a tint or a solid.
  expect.danger.bg = 'rgba(0, 0, 0, 0)';
  const radius = len('--radius-md'), controlH = len('--control-h'), fieldH = len('--field-h'), primaryH = len('--primary-h');
  const field = { bg: color('--input-bg'), border: color('--control-border') };
  probe.remove();

  const cardBox = card.getBoundingClientRect();
  const shown = (el) => el.getClientRects().length > 0;
  const bad = [];
  const name = (el) => `${el.tagName.toLowerCase()}.${el.className} "${(el.textContent || el.getAttribute('aria-label') || '').trim().slice(0, 24)}"`;
  const inside = (el, box) => {
    if (box.right > cardBox.right + 1 || box.left < cardBox.left - 1) bad.push(`${name(el)} overflows the dialog`);
  };

  const buttons = [...card.querySelectorAll('button')]
    .filter((b) => shown(b) && !b.matches('.detail-close, .icon-button, [role=switch], .action-row-main'));
  for (const b of buttons) {
    const s = getComputedStyle(b), box = b.getBoundingClientRect();
    const got = { bg: s.backgroundColor, fg: s.color, border: s.borderTopColor };
    const pool = b.disabled ? ['disabled'] : b.matches('.danger') ? ['danger'] : ['primary', 'tint', 'surface'];
    const tier = pool.find((k) => ['bg', 'fg', 'border'].every((p) => got[p] === expect[k][p]));
    if (!tier) bad.push(`${name(b)} wears no button tier: ${JSON.stringify(got)}`);
    if (Math.abs(parseFloat(s.borderTopLeftRadius) - radius) > 0.5) bad.push(`${name(b)} radius ${s.borderTopLeftRadius}`);
    if (box.height < controlH - 0.5) bad.push(`${name(b)} is ${box.height}px tall`);
    // the label's own box, not scrollWidth: the ::before hit area extends the scrollable overflow
    const range = document.createRange(); range.selectNodeContents(b);
    const label = range.getBoundingClientRect();
    if (label.height > b.clientHeight + 1 || label.width > b.clientWidth + 1) bad.push(`${name(b)} label overflows`);
    inside(b, box);
  }
  // the x close is an .icon-button (project-scaffolding#339): unpainted at rest
  const close = card.querySelector('.detail-close');
  // The sign-in sheet has no x by contract: the app cannot be used signed out.
  if (!close) { if (!dialog.classList.contains('sign-in')) bad.push('no close button'); }
  else {
    const s = getComputedStyle(close);
    if (s.backgroundColor !== 'rgba(0, 0, 0, 0)' || s.borderTopWidth !== '0px') bad.push(`close is painted: ${s.backgroundColor} ${s.borderTopWidth}`);
  }
  const footer = card.querySelector('.detail-actions');
  const save = footer?.querySelector('.detail-save-btn');
  if (!save) bad.push('no footer primary');
  else {
    const box = save.getBoundingClientRect(), fs = getComputedStyle(footer);
    const content = footer.clientWidth - parseFloat(fs.paddingLeft) - parseFloat(fs.paddingRight);
    if (box.height < primaryH - 0.5) bad.push(`footer primary is ${box.height}px tall`);
    if (Math.abs(box.width - content) > 1) bad.push(`footer primary is ${box.width}px of ${content}px`);
  }

  const fields = [...card.querySelectorAll('select, .input-native')].filter(shown);
  for (const f of fields) {
    const s = getComputedStyle(f), box = f.getBoundingClientRect();
    if (Math.abs(box.height - fieldH) > 0.5) bad.push(`${name(f)} is ${box.height}px tall`);
    if (s.backgroundColor !== field.bg) bad.push(`${name(f)} fill ${s.backgroundColor}`);
    if (s.borderTopColor !== field.border) bad.push(`${name(f)} border ${s.borderTopColor}`);
    if (Math.abs(parseFloat(s.borderTopLeftRadius) - radius) > 0.5) bad.push(`${name(f)} radius ${s.borderTopLeftRadius}`);
    inside(f, box);
  }
  return {
    buttons: buttons.map((b) => b.textContent.trim()),
    selects: fields.filter((f) => f.tagName === 'SELECT').length,
    inputs: fields.filter((f) => f.tagName === 'INPUT').length,
    bad,
  };
}"""


def _check_dialog(page, theme, dialog_id):
    """Run the controls probe on a desktop and at phone width; return the desktop result."""
    found = {}
    for width, height in ((1100, 950), (390, 844)):
        page.set_viewport_size({"width": width, "height": height})
        page.wait_for_timeout(150)
        found = page.evaluate(_DIALOG_CONTROLS_PROBE, dialog_id)
        assert not found["bad"], f"#{dialog_id} at {width}px ({theme}): {found['bad']}"
    page.set_viewport_size({"width": 1100, "height": 950})
    return found


def _open_sign_in_sheet(page):
    # The vendored sign-in sheet is built on demand; the fixture server has auth
    # off, so ask for it directly and answer its login route with a stub.
    page.route("**/api/login", lambda route: route.fulfill(
        status=200, content_type="application/json", body='{"token": "e2e-token"}'))
    page.evaluate("""import('/static/_vendored/sign-in/sign-in.js').then((m) => {
      m.requestSignIn({ endpoint: '/api/login', hint: 'Enter the password.', onToken: () => {} });
      document.querySelector('dialog.sign-in').id = 'sign-in-sheet';
    })""")
    page.wait_for_selector("dialog.sign-in[open]")


def _sign_in(page):
    page.fill("dialog.sign-in input[type=password]", "stub")
    page.press("dialog.sign-in input[type=password]", "Enter")
    page.wait_for_selector("dialog.sign-in", state="hidden")
    page.unroute("**/api/login")


def _check_sheets_and_dialogs_are_styled(page, theme):
    """The Add item sheet, the Benchmark sheet and its baseline dialog, the item
    sheet's pages and the apply dialog all render on the vendored recipes."""
    # The Add item sheet: the search's tint button, the two pickers, and the
    # footer primary that submits the form.
    open_add_sheet(page)
    found = _check_dialog(page, theme, "add-sheet")
    assert found["buttons"] == ["Search", "Add item"] and found["selects"] == 2, found
    page.keyboard.press("Escape")

    # The Benchmark sheet: the plan and baseline buttons, full width so no
    # label wraps (COMP-07), and the import as its one footer primary.
    bench = open_benchmark_sheet(page)
    bench.locator(".detail-save-btn").click()
    page.wait_for_selector("#stores-sim[data-state='ready']")
    bench.locator("[data-stores-action='set-baseline']").wait_for()
    assert _check_dialog(page, theme, "stores-benchmark-sheet")["buttons"] == [
        "Copy steps", "Load recommended plan", "Reset to my list", "Set as baseline", "Import latest run"]

    bench.locator("[data-stores-action='set-baseline']").click()
    page.locator("#stores-baseline-dialog").wait_for()
    assert _check_dialog(page, theme, "stores-baseline-dialog")["buttons"] == ["Set baseline"]
    page.locator("#stores-baseline-dialog [data-dialog-close]").click()
    bench.locator("[data-dialog-close]").click()

    burger = page.locator(".store-row", has_text="burguer ternera")
    burger.locator(".action-row-main").click()
    # The item sheet: its Stores & prices page with a store's editor open, its
    # main page (the in-body Delete), and a picker page.
    detail = page.locator("#item-sheet")
    detail.locator("[data-review-edit]").first.click()
    detail.locator("[data-review-form]").wait_for()
    found = _check_dialog(page, theme, "item-sheet")
    assert {"Save", "Cancel"} <= set(found["buttons"]), found
    assert found["selects"] >= 1, found
    detail.locator("[data-review-cancel]").click()
    detail.locator("[data-sheet-back]").click()
    assert _check_dialog(page, theme, "item-sheet")["buttons"] == ["Delete item", "Save"]
    detail.locator("[data-page-to='zone']").click()
    assert _check_dialog(page, theme, "item-sheet")["selects"] == 1
    detail.locator("[data-dialog-close]").click()

    pick = burger.locator("[data-stores-pick]")
    original = pick.input_value()
    other = pick.evaluate("s => [...s.options].map((o) => o.value).find((v) => v && v !== s.value)")
    pick.select_option(other)
    page.click("#stores-sim [data-stores-action='review']")
    page.locator("#stores-apply-dialog .apply-row").first.wait_for()
    found = _check_dialog(page, theme, "stores-apply-dialog")
    assert len(found["buttons"]) == 1 and found["buttons"][0].startswith("Apply") and found["inputs"] >= 2, found
    page.locator("#stores-apply-dialog [data-dialog-close]").click()
    pick.select_option(original)  # the pending pick would outlive a reload, and the other theme's pass


@pytest.mark.e2e
def test_copy_rules_and_themed_surfaces(page, browser, server):
    """The shopper-facing copy rules hold on every view. Then, in both themes: native
    controls draw in the chosen theme even when the OS says otherwise, the run log is
    the code-listing frame, an on switch is the accent, and every dialog's dropdowns
    and buttons render on the vendored select-native / button recipes, on a desktop
    and at phone width (modal.css no longer styles them itself, so a modal re-vendor
    without its two companions leaves bare UA controls)."""
    _check_copy_rules(page)
    for chosen, os_theme in (("dark", "light"), ("light", "dark")):
        _check_color_scheme_follows_chosen_theme(browser, server, chosen, os_theme)
    for theme in ("light", "dark"):
        _use_theme(page, theme)
        _check_automation_log_is_a_code_listing(page)
        _check_on_switch_is_the_accent(page)
        _open_sign_in_sheet(page)
        assert _check_dialog(page, theme, "sign-in-sheet")["buttons"] == ["Sign in"]
        surface(page, "app.sign-in")
        _sign_in(page)
        _check_sheets_and_dialogs_are_styled(page, theme)
    assert page._js_errors == [], f"JS errors: {page._js_errors}"
