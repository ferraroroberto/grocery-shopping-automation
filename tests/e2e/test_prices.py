"""Items > Prices: the benchmark import, plan simulation, store pick and apply, and the dialog recipes."""

from __future__ import annotations

import pytest

from tests.e2e._geometry import assert_no_overlap
from tests.e2e._helpers import (
    goto_mode,
    open_add_sheet,
    open_benchmark_sheet,
)
from tests.e2e._surface import surface


_PILL_COUNT_JS = "(key) => document.querySelector(`[data-stores-filter='${key}'] .pill-count`)?.textContent"
_STORE_PILL = "[data-stores-store='{}']"


def _import_latest_run(page):
    bench = open_benchmark_sheet(page)
    bench.locator(".detail-save-btn").click()  # Import latest run, the sheet's one primary
    bench.locator(".panel-status.ok").wait_for()
    page.wait_for_selector("#stores-sim[data-state='ready']")
    # Benchmark status: run date and the review due 90 days later, plus the copyable steps.
    bench.locator(".stores-status").wait_for()
    status = bench.locator(".stores-status").inner_text()
    assert "2026-01-15" in status and "2026-04-15" in status
    assert bench.locator("[data-stores-action='copy-steps']").count() == 1
    assert "Pricing" not in bench.locator(".benchmark-section", has_text="Cost breakdown").inner_text()
    return bench


def _close_benchmark_sheet_and_check_footer(bench, glance):
    bench.locator("[data-dialog-close]").click()
    bench.wait_for(state="hidden")
    # The footer row names the run's exception (the fixture's review is overdue).
    foot = glance.locator("[data-stores-action='benchmark'] .sheet-link-value")
    assert (foot.inner_text(), foot.get_attribute("data-tone")) == ("Review due", "attention")
    # One primary on the glance card: the recommended plan until a pick differs.
    assert glance.locator(".button-primary").all_inner_texts() == ["Load recommended plan"]


def _check_store_chip_link(page):
    # LAYOUT-03 (#194, accepted in .fleet.toml): a store chip with a product
    # URL is a link that opens it in a new tab — the picked (today's) store's
    # chip here is the imported Ametller link.
    burger_row = page.locator(".store-row", has_text="burguer ternera").first
    ametller_chip = burger_row.locator(".store-chip.is-picked")
    assert ametller_chip.count() == 1
    assert ametller_chip.evaluate("el => el.tagName") == "A"
    assert ametller_chip.get_attribute("href") == \
        "https://www.ametllerorigen.com/es/american-burger-ametller-origen-150g-2uds/p?sc=14"
    assert ametller_chip.get_attribute("target") == "_blank"


def _check_search_filters_rows(page):
    # The re-homed search actually filters the Stores rows (not just present).
    all_row_count = page.locator(".store-row").count()
    page.fill("#search", "burguer ternera")
    page.wait_for_function("document.querySelectorAll('.store-row').length === 1")
    assert "burguer ternera" in page.locator(".store-row").inner_text()
    page.fill("#search", "")
    page.wait_for_function(f"document.querySelectorAll('.store-row').length === {all_row_count}")
    # #193 regression: typing character by character (not a programmatic
    # fill/dispatch) must not rebuild the Stores pane — the search keeps focus
    # and its full value, the rows filter as you type, and the Monthly cost
    # card is the exact same DOM node throughout (a tagged JS property proves
    # no renderStores() rebuild happened, as opposed to the list-only repaint).
    page.evaluate("document.querySelector('#stores-sim').__regression193 = true")
    page.click("#search")
    page.keyboard.type("burguer ternera", delay=50)
    assert page.evaluate("document.activeElement === document.querySelector('#search')")
    assert page.eval_on_selector("#search", "el => el.value") == "burguer ternera"
    page.wait_for_function("document.querySelectorAll('.store-row').length === 1")
    assert "burguer ternera" in page.locator(".store-row").inner_text()
    assert page.evaluate("document.querySelector('#stores-sim')?.__regression193") is True
    page.fill("#search", "")
    page.wait_for_function(f"document.querySelectorAll('.store-row').length === {all_row_count}")
    today_total = page.locator(".stores-total strong").inner_text()
    return today_total


def _load_recommended_plan(page, glance, today_total):
    glance.locator("[data-stores-action='recommended']").click()
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent !== before", arg=today_total,
    )
    assert "Saves" in page.locator(".stores-delta").inner_text()
    # The plan differs from the list now, so the one action is Review & apply.
    page.wait_for_function("document.querySelector('#stores-sim .button-primary')?.dataset.storesAction === 'review'")
    assert glance.locator(".button-primary").count() == 1


def _pick_carrefour_for_the_burger(page, bench, today_total):
    # Back to the list (from the Benchmark sheet, which closes), then one
    # what-if pick: nothing is written until Apply.
    open_benchmark_sheet(page).locator("[data-stores-action='reset']").click()
    bench.wait_for(state="hidden")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent === before", arg=today_total,
    )
    burger = page.locator(".store-row", has_text="burguer ternera").first
    # Only the stores the list buys from are shown until "Show all stores".
    assert burger.locator(".store-chip", has_text="Carrefour").count() == 0
    page.click("[data-stores-showall]")
    page.wait_for_selector(".store-row .store-chip:has-text('Carrefour')")
    burger = page.locator(".store-row", has_text="burguer ternera").first
    # The store links wrap as reference pills (#257): none starts off-screen.
    assert burger.locator(".store-chips").evaluate("el => el.scrollWidth <= el.clientWidth")
    # TOUCH-02: the pills' expanded hit areas never reach into the row's tap target.
    assert_no_overlap(burger.locator(".action-row-main, .store-chip, [data-stores-pick]"))
    burger.locator("[data-stores-pick]").select_option("carrefour")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent !== before", arg=today_total,
    )
    assert burger.locator(".store-chip.is-picked").inner_text().startswith("Carrefour")
    return burger


def _open_apply_dialog(page, glance):
    assert glance.locator(".button-primary").inner_text() == "Review & apply (1)"
    glance.locator("[data-stores-action='review']").click()
    row = page.locator("#stores-apply-dialog .apply-row")
    row.first.wait_for()
    assert row.count() == 1
    assert row.first.locator(".apply-qty").input_value() == "2"


def _apply_the_change(page, server, glance):
    with page.expect_request(lambda r: r.url.endswith("/api/stores/apply")) as sent:
        page.click("#stores-apply-dialog .detail-save-btn")
    # The batch names the item it means, so a moved row is refused (#313).
    assert [c["name"] for c in sent.value.post_data_json["changes"]] == ["burguer ternera"]
    page.wait_for_function("document.querySelector('#toast')?.textContent === 'Applied 1 change'")
    assert not page.locator("#stores-apply-dialog").is_visible()
    assert glance.locator(".button-primary").inner_text() == "Load recommended plan"

    item = next(i for i in page.request.get(f"{server.url}/api/inventory").json()["items"]
                if i["comida"] == "burguer ternera")
    assert (item["super"], item["cantidad"], item["tenemos"]) == ("carrefour", 2, 1)  # stock 2 × 0.3 / 0.6
    assert item["buscador"] == item["urls"]["carrefour"]
    return item


def _check_store_filter(page):
    # Store filter (#170): one store → only its rows; a second adds its rows; none → all.
    page.wait_for_selector(_STORE_PILL.format("carrefour"))
    all_rows = page.locator(".store-row").count()
    page.click(_STORE_PILL.format("carrefour"))
    assert page.locator(_STORE_PILL.format("carrefour")).get_attribute("aria-pressed") == "true"
    assert page.locator(".store-row").count() == 1
    assert "Carrefour" in page.locator(".store-row").first.inner_text()
    page.click(_STORE_PILL.format("ametller"))
    ametller = int(page.locator(f"{_STORE_PILL.format('ametller')} .pill-count").inner_text())
    assert page.locator(".store-row").count() == 1 + ametller
    page.click(_STORE_PILL.format("carrefour"))
    page.click(_STORE_PILL.format("ametller"))
    assert page.locator(".store-row").count() == all_rows
    return all_rows


def _check_only_items_i_buy(page, server, all_rows):
    # Only items I buy (#172): a target-0 row hides and the Store counts follow; off → it returns.
    bought = [i for i in page.request.get(f"{server.url}/api/inventory").json()["items"] if i["cantidad"] > 0]
    zero_row = page.locator(".store-row", has_text="amoniaco")  # target 0 in the fixture
    assert zero_row.count() == 1
    page.click("[data-stores-targetonly]")
    page.wait_for_function(f"document.querySelectorAll('.store-row').length === {len(bought)}")
    assert zero_row.count() == 0
    mercadona = sum(i["super"] == "mercadona" for i in bought)
    assert page.locator(f"{_STORE_PILL.format('mercadona')} .pill-count").inner_text() == str(mercadona)
    page.click("[data-stores-targetonly]")
    page.wait_for_function(f"document.querySelectorAll('.store-row').length === {all_rows}")
    assert zero_row.count() == 1


def _filter_needs_checking(page):
    # ── Per-item review (#165) ──
    page.wait_for_function(f"({_PILL_COUNT_JS})('needs') === '1'")
    page.click("[data-stores-filter='needs']")
    burger = page.locator(".store-row", has_text="burguer ternera").first
    assert page.locator(".store-row").count() == 1
    # The reason is the one status chip, in the attention tone (#254), and the
    # Items header names the exception instead of the mode.
    assert burger.locator(".chip[data-tone='attention']").inner_text().startswith("Moved store")
    assert page.locator("#items-context").inner_text() == "1 needs checking"
    return burger


def _open_baseline_dialog(page):
    # ── Baseline (#183), in the Benchmark sheet's cost breakdown: freeze the
    # list as the new "today", confirm shows the unchecked count (over the
    # sheet), then reset restores the exact prior simulate result.
    bench = open_benchmark_sheet(page)
    assert "benchmarked" in bench.locator(".stores-today").inner_text()
    before_baseline_total = page.locator(".stores-total strong").inner_text()
    bench.locator("[data-stores-action='set-baseline']").click()
    baseline_dialog = page.locator("#stores-baseline-dialog")
    baseline_dialog.wait_for()
    assert "1 item" in baseline_dialog.inner_text() and "checking" in baseline_dialog.inner_text()
    return bench, before_baseline_total, baseline_dialog


def _set_and_reset_baseline(page, bench, baseline_dialog, before_baseline_total):
    hint = "document.querySelector('#stores-benchmark-sheet .stores-today')?.textContent"
    baseline_dialog.locator(".detail-save-btn").click()
    page.wait_for_function("() => !document.querySelector('#stores-baseline-dialog').open")
    page.wait_for_function(f"() => {hint}.includes('baseline')")
    assert bench.is_visible()
    assert "Same as today" in page.locator(".stores-delta").inner_text()
    bench.locator("[data-stores-action='reset-baseline']").click()
    page.wait_for_function(f"() => !{hint}.includes('baseline')")
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent === before", arg=before_baseline_total,
    )
    bench.locator("[data-dialog-close]").click()


def _open_item_sheet_on_prices(page, burger):
    # A row tap opens the item sheet (#255) straight onto Stores & prices (#257).
    burger.locator(".action-row-main").click()
    dialog = page.locator("#item-sheet")
    dialog.locator("tr[data-review-store='carrefour']").wait_for()
    assert dialog.get_attribute("data-page") == "prices"
    # The cut the cart picks for that product is shown on its store row (#179).
    assert dialog.locator("tr[data-review-store='carrefour'] .review-option").inner_text().startswith("Cut: Fileteado")
    assert dialog.locator("tr[data-review-store='ametller'] .review-option").count() == 0
    # TOUCH-01 (#257): a store's link is a labelled 44px target, not the raw URL.
    link = dialog.locator("tr[data-review-store='ametller'] .review-url").first
    assert link.inner_text() == "Open at Ametller Origen"
    assert link.get_attribute("href").startswith("https://www.ametllerorigen.com/")
    assert link.bounding_box()["height"] >= 44
    return dialog


def _check_item_sheet_width(page, dialog):
    # Wide on a desktop, no sideways scroll on a phone.
    page.set_viewport_size({"width": 1280, "height": 900})
    assert dialog.bounding_box()["width"] >= 900
    page.set_viewport_size({"width": 390, "height": 844})
    assert dialog.evaluate("d => d.scrollWidth <= d.clientWidth"), "item sheet scrolls sideways at 390px"
    page.set_viewport_size({"width": 1100, "height": 950})


def _open_price_editor(page, burger, dialog):
    # Your pack price at Carrefour prices the simulator and the chip, until reset.
    carrefour_chip = burger.locator(".store-chip", has_text="Carrefour")
    assert "€14.40/mo" in carrefour_chip.inner_text()
    dialog.locator("[data-review-edit='carrefour']").click()
    dialog.locator("[data-review-form='carrefour'] [name='pack_price']").fill("6")
    return carrefour_chip


def _override_and_reset_price(page, dialog, carrefour_chip, total):
    dialog.locator("[data-review-form='carrefour'] button[type='submit']").click()
    dialog.locator("tr[data-review-store='carrefour'] .chip:has-text('yours')").wait_for()
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent !== before", arg=total,
    )
    assert "€12.00/mo" in carrefour_chip.inner_text()  # 1.2 kg a month / 0.6 kg × €6
    dialog.locator("[data-review-edit='carrefour']").click()
    dialog.locator("[data-review-reset='carrefour']").click()
    page.wait_for_function(
        "(before) => document.querySelector('.stores-total strong')?.textContent === before", arg=total,
    )
    assert dialog.locator(".chip:has-text('yours')").count() == 0


def _check_needs_checking_and_quantity(dialog):
    # Back on the main page the row names what needs you; the quantity maths
    # is one page away.
    dialog.locator("[data-sheet-back]").click()
    prices_value = dialog.locator("[data-link-value='prices']")
    assert (prices_value.inner_text(), prices_value.get_attribute("data-tone")) == ("Needs checking", "attention")
    dialog.locator("[data-page-to='quantity']").click()
    qty = dialog.locator(".review-qty-lines").inner_text()
    assert "Ametller Origen 2 × 0.3 kg = 0.6 kg" in qty
    assert "Carrefour 2 × 0.6 kg = 1.2 kg (+100%)" in qty


def _save_target_and_stock(page, server, dialog, item):
    dialog.locator("[data-sheet-back]").click()
    # Target and stock are staged on the steppers and saved to the list; the
    # rest of the row is untouched.
    dialog.locator("[data-sheet-qty='cantidad'] [data-action='target-plus']").click()
    dialog.locator("[data-sheet-qty='tenemos'] [data-action='current-plus']").click()
    with page.expect_response(lambda r: r.request.method == "PUT" and f"/api/items/{item['id']}?name=" in r.url) as resp:
        dialog.locator(".detail-save-btn").click()
    assert resp.value.ok
    dialog.wait_for(state="hidden")
    saved = next(i for i in page.request.get(f"{server.url}/api/inventory").json()["items"]
                 if i["comida"] == "burguer ternera")
    assert (saved["cantidad"], saved["tenemos"]) == (3, 2)
    assert (saved["super"], saved["buscador"]) == ("carrefour", item["buscador"])


def _mark_checked(page, burger, dialog):
    # Checked is stored server-side and empties the Needs-checking filter.
    burger.locator(".action-row-main").click()
    dialog.locator("[data-review-checked]").click()
    page.wait_for_function(f"({_PILL_COUNT_JS})('needs') === '0' && ({_PILL_COUNT_JS})('checked') === '1'")
    assert dialog.locator("[data-review-checked]").get_attribute("aria-checked") == "true"
    # Nothing left to check: the header falls back to the plain count.
    assert page.locator("#items-context").inner_text().endswith(" items")
    dialog.locator("[data-dialog-close]").click()
    assert page.locator(".store-row").count() == 0
    assert page._js_errors == [], f"JS errors: {page._js_errors}"


@pytest.mark.e2e
def test_stores_plan_simulate_and_apply(page, server):
    """Items → Stores (#148): import the fixture run, load the recommended plan
    and see the simulated total move, reveal every store with the Show-all
    switch (#163), then review and apply one store change —
    the burger to Carrefour, its target converted by pack size (3 × 0.3 kg →
    2 × 0.6 kg). Then the per-item review (#165): the moved burger needs
    checking; its detail shows the quantity maths, a price override moves the
    simulator and the chip until it is reset, and Checked clears it.

    The glance card (#257) leads with the cost, its delta and the one action;
    the run's status, the import, the plan actions and the breakdown live in
    the Benchmark sheet its footer row opens."""
    goto_mode(page, "stores")
    glance = page.locator("#stores-sim")
    assert glance.evaluate("el => el.closest('.pane-body').firstElementChild === el"), "glance card is not first"
    bench = _import_latest_run(page)
    surface(page, "items.benchmark-sheet")
    _close_benchmark_sheet_and_check_footer(bench, glance)
    # LAYOUT-02: the Stores list gets its own filter input in the list's own
    # card (the re-homed global toolbar search), not a second search box.
    assert page.locator(".stores-list-card input[type='search']").count() == 1
    assert page.locator("#toolbar input[type='search']").count() == 1  # the same node, moved
    surface(page, "items.prices")
    _check_store_chip_link(page)
    today_total = _check_search_filters_rows(page)
    _load_recommended_plan(page, glance, today_total)
    surface(page, "items.prices-plan")
    burger = _pick_carrefour_for_the_burger(page, bench, today_total)
    _open_apply_dialog(page, glance)
    surface(page, "items.apply-dialog")
    item = _apply_the_change(page, server, glance)
    all_rows = _check_store_filter(page)
    _check_only_items_i_buy(page, server, all_rows)
    burger = _filter_needs_checking(page)
    surface(page, "items.prices-needs-checking")
    bench, before_baseline_total, baseline_dialog = _open_baseline_dialog(page)
    surface(page, "items.baseline-dialog")
    _set_and_reset_baseline(page, bench, baseline_dialog, before_baseline_total)
    total = page.locator(".stores-total strong").inner_text()

    # A row tap opens the item sheet (#255) straight onto Stores & prices (#257).
    dialog = _open_item_sheet_on_prices(page, burger)
    surface(page, "items.item-sheet-prices")
    _check_item_sheet_width(page, dialog)
    carrefour_chip = _open_price_editor(page, burger, dialog)
    surface(page, "items.item-sheet-price-editor")
    _override_and_reset_price(page, dialog, carrefour_chip, total)
    _check_needs_checking_and_quantity(dialog)
    surface(page, "items.item-sheet-quantity")
    _save_target_and_stock(page, server, dialog, item)
    _mark_checked(page, burger, dialog)



# Every dropdown, inline input and labelled button in one open dialog, checked
# against the vendored recipes (#207: select-native/, button/, modal/), with
# the expected values resolved from the theme's own tokens -- never hard-coded.
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
    // the label's own box, not scrollWidth: the ::before hit area (#230) extends the scrollable overflow
    const range = document.createRange(); range.selectNodeContents(b);
    const label = range.getBoundingClientRect();
    if (label.height > b.clientHeight + 1 || label.width > b.clientWidth + 1) bad.push(`${name(b)} label overflows`);
    inside(b, box);
  }
  // the x close is an .icon-button (project-scaffolding#339): unpainted at rest
  const close = card.querySelector('.detail-close');
  if (!close) bad.push('no close button');
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


@pytest.mark.e2e
@pytest.mark.parametrize("theme", ["light", "dark"])
def test_dialog_controls_are_styled(page, theme):
    """#207: every dialog's dropdowns and Save/Cancel buttons render on the
    vendored select-native / button recipes in both themes, on a desktop and
    at phone width. modal.css no longer styles them itself, so a modal
    re-vendor without its two companions leaves them as bare UA controls."""
    page.evaluate("t => { localStorage.setItem('grocery.theme', t); document.documentElement.dataset.theme = t; }", theme)

    def check(dialog_id: str) -> dict:
        found = {}
        for width, height in ((1100, 950), (390, 844)):
            page.set_viewport_size({"width": width, "height": height})
            page.wait_for_timeout(150)
            found = page.evaluate(_DIALOG_CONTROLS_PROBE, dialog_id)
            assert not found["bad"], f"#{dialog_id} at {width}px ({theme}): {found['bad']}"
        page.set_viewport_size({"width": 1100, "height": 950})
        return found

    # Login is static markup; the fixture server has auth off, so open it directly.
    page.evaluate("document.getElementById('login-dialog').showModal()")
    assert check("login-dialog")["buttons"] == ["Unlock"]
    surface(page, "app.login-dialog")
    page.evaluate("document.getElementById('login-dialog').close()")

    # The Add item sheet (#256): the search's tint button, the two pickers,
    # and the footer primary that submits the form.
    open_add_sheet(page)
    found = check("add-sheet")
    assert found["buttons"] == ["Search", "Add item"] and found["selects"] == 2, found
    page.keyboard.press("Escape")

    # The Benchmark sheet (#257): the plan and baseline buttons, full width so
    # no label wraps (COMP-07), and the import as its one footer primary.
    bench = open_benchmark_sheet(page)
    bench.locator(".detail-save-btn").click()
    page.wait_for_selector("#stores-sim[data-state='ready']")
    bench.locator("[data-stores-action='set-baseline']").wait_for()
    assert check("stores-benchmark-sheet")["buttons"] == [
        "Copy steps", "Load recommended plan", "Reset to my list", "Set as baseline", "Import latest run"]

    bench.locator("[data-stores-action='set-baseline']").click()
    page.locator("#stores-baseline-dialog").wait_for()
    assert check("stores-baseline-dialog")["buttons"] == ["Set baseline"]
    page.locator("#stores-baseline-dialog [data-dialog-close]").click()
    bench.locator("[data-dialog-close]").click()

    burger = page.locator(".store-row", has_text="burguer ternera")
    burger.locator(".action-row-main").click()
    # The item sheet (#255): its Stores & prices page with a store's editor
    # open, its main page (the in-body Delete), and a picker page.
    detail = page.locator("#item-sheet")
    detail.locator("[data-review-edit]").first.click()
    detail.locator("[data-review-form]").wait_for()
    found = check("item-sheet")
    assert {"Save", "Cancel"} <= set(found["buttons"]), found
    assert found["selects"] >= 1, found
    detail.locator("[data-review-cancel]").click()
    detail.locator("[data-sheet-back]").click()
    assert check("item-sheet")["buttons"] == ["Delete item", "Save"]
    detail.locator("[data-page-to='zone']").click()
    assert check("item-sheet")["selects"] == 1
    detail.locator("[data-dialog-close]").click()

    pick = burger.locator("[data-stores-pick]")
    other = pick.evaluate("s => [...s.options].map((o) => o.value).find((v) => v && v !== s.value)")
    pick.select_option(other)
    page.click("#stores-sim [data-stores-action='review']")
    page.locator("#stores-apply-dialog .apply-row").first.wait_for()
    found = check("stores-apply-dialog")
    assert len(found["buttons"]) == 1 and found["buttons"][0].startswith("Apply") and found["inputs"] >= 2, found
    page.locator("#stores-apply-dialog [data-dialog-close]").click()
    assert page._js_errors == [], f"JS errors: {page._js_errors}"
