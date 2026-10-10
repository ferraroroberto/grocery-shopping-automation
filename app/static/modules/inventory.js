// The four inventory views: the Home dashboard, the audit/target editors, the
// per-row edit form, and the add form (with the product-search host above it).
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { activePaneBody, c, defaultZone, filteredItems, items, state } from "./core.js";
import { esc, html, icon, pickerMarkup, text } from "./dom.js";
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
  const rows = source.map((item) => dashboardRow(item, cols)).join("");
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

function dashboardRow(item, cols) {
  return itemRow({
    title: item[cols.comida],
    meta: `${text(item[cols.lugar])} · ${text(item[cols.super])}${buyNote(item, cols)}`,
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

// The manual Audit (stock = true) and Items → Targets share one list: the
// zone's items, each with ONE stepper — the in-stock count while auditing, the
// target under Targets (#252 decision 3) — and the other figure in its meta.
// The audit counts only what is kept (target above 0); Targets lists the whole
// zone, so a target can be set on an item that has none yet.
export function renderAudit(stock = false) {
  const cols = c();
  const source = filteredItems(items()
    .filter((item) => item[cols.lugar] === state.zone)
    .filter((item) => !stock || Number(item[cols.cantidad]) > 0))
    .sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])));
  const rows = source.map((item) => itemRow({
    title: item[cols.comida],
    meta: stock
      ? `Target ${text(item[cols.cantidad])} · ${text(item[cols.super])}${buyNote(item, cols)}`
      : `In stock ${text(item[cols.tenemos])} · ${text(item[cols.super])}`,
    trail: stepper(stock ? "current" : "target", text(stock ? item[cols.tenemos] : item[cols.cantidad]), text(item[cols.comida])),
    attrs: `data-id="${item.id}"`,
  })).join("");
  activePaneBody().innerHTML = `${zoneTabs()}${rows ? itemList(rows) : emptyStateEl("package", "No items in this zone.").outerHTML}`;
}

// A visible caption above a form input (wrapping <label>, so the name is
// programmatic too) — placeholders alone vanish once the box holds a value (#213).
const labelled = (caption, input) => `<label class="field-label">${caption}${input}</label>`;

export function renderEdit() {
  const cols = c();
  const source = filteredItems().sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])));
  activePaneBody().innerHTML = `<section class="grid">${source.map((item) => `
    <article class="card" data-id="${item.id}">
      <form class="form edit-form">
        <div class="row"><h3>${html(item[cols.comida])}</h3><button class="danger" type="button" data-action="delete">Delete</button></div>
        <div class="three">
          ${labelled("Item", `<input class="field" name="comida" value="${esc(item[cols.comida])}" placeholder="Item" />`)}
          ${labelled("Supermarket", `<input class="field" name="super" value="${esc(item[cols.super])}" placeholder="Supermarket" />`)}
          ${labelled("Zone", `<input class="field" name="lugar" value="${esc(item[cols.lugar])}" placeholder="Zone" />`)}
        </div>
        <div class="three-link">
          ${labelled("Target", `<input class="field" name="cantidad" type="number" min="0" value="${esc(item[cols.cantidad])}" placeholder="Target" />`)}
          ${labelled("In stock", `<input class="field" name="tenemos" type="number" min="0" value="${esc(item[cols.tenemos])}" placeholder="In stock" />`)}
          ${labelled("URL", `<input class="field" name="buscador" value="${esc(item[cols.buscador])}" placeholder="URL" />`)}
        </div>
        <button class="primary" type="submit">Save</button>
      </form>
    </article>`).join("") || emptyStateEl("search", "No matching items.").outerHTML}</section>`;
}

// Add Item = the store product search (#182; search.js fills #product-search,
// and a picked new product pre-fills this form) above the manual form.
export function renderAdd() {
  const zones = state.payload.summary.zones;
  const stores = state.payload.summary.supermarkets;
  activePaneBody().innerHTML = `<section id="product-search" class="panel" aria-label="Find a store product"></section>
  <section class="panel" aria-label="Add by hand">
    <form id="add-form" class="form">
      <div class="three">
        ${labelled("Item", `<input class="field" name="comida" placeholder="Item name" required />`)}
        ${pickerMarkup("super", "Supermarket", stores, stores[0], "New supermarket…")}
        ${pickerMarkup("lugar", "Zone", zones, defaultZone(), "New zone…")}
      </div>
      <div class="three-link">
        ${labelled("Target", `<input class="field" name="cantidad" type="number" min="0" value="0" placeholder="Target" />`)}
        ${labelled("In stock", `<input class="field" name="tenemos" type="number" min="0" value="0" placeholder="In stock" />`)}
        ${labelled("URL", `<input class="field" name="buscador" placeholder="URL" />`)}
      </div>
      <button class="big-btn" type="submit">Add item</button>
    </form>
  </section>`;
}
