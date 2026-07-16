// The four inventory views: the Home dashboard, the audit/target editors, the
// per-row edit form, and the add form.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { activePaneBody, c, filteredItems, items, state } from "./core.js";
import { html, qtyMarkup, text } from "./dom.js";

function metric(label, value) {
  return `<article class="metric"><span>${label}</span><strong>${value}</strong></article>`;
}

function renderSummary() {
  const s = state.payload.summary;
  return `<section class="summary">
    ${metric("Tracked items", s.total_items)}
    ${metric("Stocked", s.total_items - s.shopping_items)}
    ${metric("Need buying", s.shopping_items)}
    ${metric("Units to buy", s.shopping_units)}
    ${metric("Zones", s.zones.length)}
  </section>`;
}

export function renderDashboard() {
  const cols = c();
  const source = filteredItems();
  const cards = source.map((item) => itemCard(item, cols)).join("");
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
          <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-package"></use></svg>
          <h3 class="collapse-title">All items</h3>
          <span class="collapse-count">${source.length}</span>
        </span>
        <span class="collapse-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="collapse-body"><section class="grid">${cards || emptyStateEl("search", "No matching items.").outerHTML}</section></div>
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

function itemCard(item, cols) {
  const buy = Number(item[cols.comprar]) || 0;
  return `<article class="item" data-id="${item.id}">
    <div><h3>${html(item[cols.comida])}</h3><div class="meta">${html(item[cols.lugar])} · ${html(item[cols.super])}</div></div>
    <div class="qty"><div>${qtyMarkup(item[cols.tenemos], item[cols.cantidad])}</div><div class="${buy > 0 ? "buy" : "ok"}">${buy > 0 ? `Buy ${buy}` : "Stocked"}</div></div>
  </article>`;
}

function zoneTabs() {
  // .pills, not .tabs — the vendored nav owns the .tabs class app-wide.
  // zone-pills keeps all zones on one swipeable line.
  return `<div class="pills zone-pills">${state.payload.summary.zones.map((zone) =>
    `<button type="button" class="pill ${zone === state.zone ? "active" : ""}" data-zone="${html(zone)}">${html(zone)}</button>`,
  ).join("")}</div>`;
}

export function renderAudit(targetsOnly = false) {
  const cols = c();
  const source = filteredItems(items()
    .filter((item) => item[cols.lugar] === state.zone)
    .filter((item) => !targetsOnly || Number(item[cols.cantidad]) > 0))
    .sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])));
  const header = targetsOnly ? "have − + · have/target · target − + · need" : "have/target · target − + · need";
  const controlsClass = targetsOnly ? "audit-controls audit-controls--full" : "audit-controls audit-controls--targets";
  activePaneBody().innerHTML = `<section class="panel"><div class="row"><h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-${targetsOnly ? "list-checks" : "package"}"></use></svg>${targetsOnly ? "Audit Inventory" : "Edit Targets"}</h2><span class="hint">${html(state.zone)} · ${source.length} items</span></div>${zoneTabs()}<div class="hint">${header}</div></section>
    <section class="grid">${source.map((item) => `
      <article class="item audit-item" data-id="${item.id}">
        <div class="audit-name"><h3>${html(item[cols.comida])}</h3><div class="meta">${html(item[cols.super])}</div></div>
        <div class="${controlsClass}">
          ${targetsOnly ? `<button class="icon-btn" data-action="current-minus">-</button><button class="icon-btn" data-action="current-plus">+</button>` : ""}
          <span class="qty">${qtyMarkup(item[cols.tenemos], item[cols.cantidad])}</span>
          <button class="icon-btn" data-action="target-minus">-</button>
          <button class="icon-btn" data-action="target-plus">+</button>
          <span class="audit-verdict ${Number(item[cols.comprar]) > 0 ? "buy" : "ok"}">${Number(item[cols.comprar]) > 0 ? `−${item[cols.comprar]}` : "OK"}</span>
        </div>
      </article>`).join("") || emptyStateEl("package", "No items in this zone.").outerHTML}</section>`;
}

export function renderEdit() {
  const cols = c();
  const source = filteredItems().sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])));
  activePaneBody().innerHTML = `<section class="grid">${source.map((item) => `
    <article class="card" data-id="${item.id}">
      <form class="form edit-form">
        <div class="row"><h3>${html(item[cols.comida])}</h3><button class="danger" type="button" data-action="delete">Delete</button></div>
        <div class="three">
          <input class="field" name="comida" value="${html(item[cols.comida])}" placeholder="Item" />
          <input class="field" name="super" value="${html(item[cols.super])}" placeholder="Supermarket" />
          <input class="field" name="lugar" value="${html(item[cols.lugar])}" placeholder="Zone" />
        </div>
        <div class="three-link">
          <input class="field" name="cantidad" type="number" min="0" value="${html(item[cols.cantidad])}" placeholder="Target" />
          <input class="field" name="tenemos" type="number" min="0" value="${html(item[cols.tenemos])}" placeholder="Current" />
          <input class="field" name="buscador" value="${html(item[cols.buscador])}" placeholder="URL" />
        </div>
        <button class="primary" type="submit">Save</button>
      </form>
    </article>`).join("") || emptyStateEl("search", "No matching items.").outerHTML}</section>`;
}

export function renderAdd() {
  const zones = state.payload.summary.zones;
  const stores = state.payload.summary.supermarkets;
  activePaneBody().innerHTML = `<section class="panel">
    <h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-plus"></use></svg>Add Item</h2>
    <form id="add-form" class="form">
      <div class="three">
        <input class="field" name="comida" placeholder="Item name" required />
        <input class="field" name="super" list="stores" placeholder="Supermarket" required />
        <input class="field" name="lugar" list="zones" placeholder="Zone" required />
      </div>
      <div class="three-link">
        <input class="field" name="cantidad" type="number" min="0" value="0" placeholder="Target" />
        <input class="field" name="tenemos" type="number" min="0" value="0" placeholder="Current" />
        <input class="field" name="buscador" placeholder="URL" />
      </div>
      <button class="big-btn" type="submit">Add Item</button>
    </form>
    <datalist id="stores">${stores.map((x) => `<option value="${html(x)}"></option>`).join("")}</datalist>
    <datalist id="zones">${zones.map((x) => `<option value="${html(x)}"></option>`).join("")}</datalist>
  </section>`;
}
