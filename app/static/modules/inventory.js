// The three inventory views: the Home dashboard, the manual Audit, and Items'
// list (#256) — every item, grouped by zone; each row opens the item sheet
// (#255), and the Add item sheet (add-sheet.js) adds one.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { activePaneBody, c, filteredItems, items, state } from "./core.js";
import { html, icon, text } from "./dom.js";
import { itemList, itemRow, rowValue, stepper } from "./rows.js";

function summaryRow(label, value) {
  return `<div class="summary-row"><dt>${label}</dt><dd class="summary-value">${value}</dd></div>`;
}

function renderSummary() {
  const s = state.payload.summary;
  // One card of label/value rows, not five separate stat cards — five cards
  // read two-to-a-row at phone width, breaking single reading order (#198).
  return `<section class="summary card">
    <dl class="summary-list">
      ${summaryRow("Tracked items", s.total_items)}
      ${summaryRow("Stocked", s.total_items - s.shopping_items)}
      ${summaryRow("Need buying", s.shopping_items)}
      ${summaryRow("Units to buy", s.shopping_units)}
      ${summaryRow("Zones", s.zones.length)}
    </dl>
  </section>`;
}

export function renderDashboard() {
  const cols = c();
  const source = filteredItems();
  const rows = source.map((item) => valueRow(item, cols)).join("");
  const body = activePaneBody();
  // The full item list folds by default (home-automation pattern: heavy cards
  // are disclosures). A re-render must not slam it shut, so harvest the live
  // open state first; an active search force-opens it — a filter whose
  // results you can't see is a dead control.
  const itemsOpen = !!body.querySelector("#dash-items[open]") || !!state.query;
  body.innerHTML = `${renderSummary()}${renderStoreCards()}
    <details id="dash-items" class="card card--collapsible"${itemsOpen ? " open" : ""}>
      <summary class="collapse-summary">
        <span class="collapse-main">
          ${icon("package")}
          <h3 class="collapse-title">All items</h3>
          <span class="collapse-count">${source.length}</span>
        </span>
        <span class="collapse-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="collapse-body">${rows ? itemList(rows, { bare: true }) : emptyStateEl("search", "No matching items.").outerHTML}</div>
    </details>`;
}

// Store progress — ONE shared card, one block per store, with clear air
// between the store name and its progress bar. Carries the cart-offset-aware
// done counts the old sidebar stats showed.
function renderStoreCards() {
  const stats = state.payload.summary.supermarket_stats;
  const stores = Object.keys(stats).sort();
  if (!stores.length) return emptyStateEl("shopping-basket", "No shopping items right now.").outerHTML;
  return `<article class="card">${stores.map((store) => {
    const s = stats[store];
    const offset = state.shopping.offsets[store] || { items: 0, units: 0 };
    const doneItems = s.got_it_unique + Number(offset.items || 0);
    const doneUnits = s.got_it_quantity + Number(offset.units || 0);
    const pct = s.total_unique ? Math.min(100, Math.round((doneItems / s.total_unique) * 100)) : 0;
    return `<div class="store-block">
      <div class="card-head"><h3 class="card-title">${html(store)}</h3><span class="card-head-meta">${doneItems}/${s.total_unique} items · ${doneUnits}/${s.total_quantity} units</span></div>
      <div class="progress"><span style="width:${pct}%"></span></div>
    </div>`;
  }).join("")}</article>`;
}

// "Buy 2" closes a row's meta line while the item is short; a stocked item
// says nothing (a normal state gets no mark, design.md status chip).
function buyNote(item, cols) {
  const buy = Number(item[cols.comprar]) || 0;
  return buy > 0 ? ` · buy ${buy}` : "";
}

// Home's list and Items' list: the zone and store, the stock/target pair as
// the row's value; the tap opens the item sheet, where all of it is edited.
// Under a zone's own group header the zone is left out of the meta.
function valueRow(item, cols, { zone = true } = {}) {
  const where = zone ? `${text(item[cols.lugar])} · ${text(item[cols.super])}` : text(item[cols.super]);
  return itemRow({
    title: item[cols.comida],
    meta: `${where}${buyNote(item, cols)}`,
    trail: rowValue(`${text(item[cols.tenemos])}/${text(item[cols.cantidad])}`),
    attrs: `data-id="${item.id}"`,
  });
}

function zoneTabs() {
  // .pills, not .tabs — the vendored nav owns the .tabs class app-wide.
  // zone-pills keeps all zones on one swipeable line.
  return `<div class="pills zone-pills">${state.payload.summary.zones.map((zone) =>
    `<button type="button" class="pill ${zone === state.zone ? "active" : ""}" data-zone="${html(zone)}">${html(zone)}</button>`,
  ).join("")}</div>`;
}

// The manual Audit: the zone's kept items (target above 0), each with ONE
// in-stock stepper and the target in its meta (#252 decision 3; the target is
// edited in the item sheet). A search looks in every zone instead, the zone
// leading the meta — typed, or a mention Voice heard and matched to no item,
// sent here from "Needs you" (#259).
export function renderAudit() {
  const cols = c();
  const kept = items().filter((item) => Number(item[cols.cantidad]) > 0);
  const byName = (a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida]));
  const source = state.query
    ? auditSearch(kept, cols, byName)
    : kept.filter((item) => item[cols.lugar] === state.zone).sort(byName);
  const rows = source.map((item) => itemRow({
    title: item[cols.comida],
    meta: `${state.query ? `${text(item[cols.lugar])} · ` : ""}Target ${text(item[cols.cantidad])} · ${text(item[cols.super])}${buyNote(item, cols)}`,
    trail: stepper("current", text(item[cols.tenemos]), text(item[cols.comida])),
    attrs: `data-id="${item.id}"`,
  })).join("");
  const empty = state.query
    ? emptyStateEl("search", "No matching items. Clear the search to count by zone.")
    : emptyStateEl("package", "No items in this zone.");
  activePaneBody().innerHTML = `${state.query ? "" : zoneTabs()}${rows ? itemList(rows) : empty.outerHTML}`;
}

// Lower case, accents off: "Maíz" and "maiz" are one word.
function fold(value) {
  return text(value).normalize("NFD").replace(/[\u0300-\u036f]/g, "").toLowerCase();
}

// Spoken numbers say how many, not which item.
const COUNT_WORDS = new Set(["una", "uno", "dos", "tres", "cuatro", "cinco", "seis", "siete", "ocho", "nueve", "diez"]);

// The search box's own match (the text appears in the name, zone or store);
// when nothing has it, a word match, so a phrase as it was said ("dos
// yogures de fresa") still finds "yogur fresa danone": every word of three
// letters or more counts by its first four letters against the name's words,
// and the items sharing the most words lead.
function auditSearch(kept, cols, byName) {
  const exact = filteredItems(kept);
  if (exact.length) return exact.sort(byName);
  const stems = fold(state.query).split(/[^a-z0-9]+/)
    .filter((word) => word.length >= 3 && !COUNT_WORDS.has(word) && !/^\d+$/.test(word))
    .map((word) => word.slice(0, 4));
  if (!stems.length) return [];
  return kept
    .map((item) => {
      const words = fold(item[cols.comida]).split(/[^a-z0-9]+/);
      return { item, hits: stems.filter((stem) => words.some((word) => word.startsWith(stem))).length };
    })
    .filter(({ hits }) => hits > 0)
    .sort((a, b) => b.hits - a.hits || byName(a.item, b.item))
    .map(({ item }) => item);
}

// Items' list (#256): every item the search lets through, one group per zone
// in the zones' own order, an overline header over each group's card
// (design.md "settings group" inset groups), A–Z inside a group.
export function renderItems() {
  const cols = c();
  const byZone = new Map();
  for (const item of filteredItems()) {
    const zone = text(item[cols.lugar]);
    if (!byZone.has(zone)) byZone.set(zone, []);
    byZone.get(zone).push(item);
  }
  const order = state.payload.summary.zones;
  const zones = [...byZone.keys()].sort((a, b) => rank(order, a) - rank(order, b) || a.localeCompare(b));
  const groups = zones.map((zone) => {
    const rows = byZone.get(zone)
      .sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])))
      .map((item) => valueRow(item, cols, { zone: false }))
      .join("");
    return `<section class="item-group" aria-label="${html(zone)}">
      <h3 class="overline item-group-head">${html(zone)}</h3>
      ${itemList(rows)}
    </section>`;
  }).join("");
  activePaneBody().innerHTML = groups
    || emptyStateEl("search", state.query ? "No matching items." : "No items yet.").outerHTML;
}

// A zone's place in the summary's order; one it doesn't list goes last.
function rank(order, zone) {
  const at = order.indexOf(zone);
  return at === -1 ? order.length : at;
}
