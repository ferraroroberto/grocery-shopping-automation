// The shopping list under Shop's carts glance (#258): one folding group per
// store, with Got-it marks and free-text quick-adds. The list comes second —
// the household fills the carts and rarely ticks items off after — so it stays
// folded and plain. Each group ends on an "Add to <store>" row and a Store
// options row opening the store sheet (store-sheet.js: the cart offsets, the
// quick-adds' Remove, Unmark all). All of it is client-side progress
// (persisted via core's shopping state) over the server's buy list.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { storeLabel } from "./automation.js";
import { activePaneBody, c, items, state } from "./core.js";
import { esc, html, icon, text } from "./dom.js";
import { itemList, itemRow, rowLink } from "./rows.js";

function shoppingItems() {
  const cols = c();
  return items().filter((item) => Number(item[cols.comprar]) > 0);
}

function buyUrl(item, cols) {
  const url = text(item[cols.buscador]);
  return url === "-" ? "" : url.trim();
}

// Shop's page-header exception (#254): items on the list with no buy link.
// Each such row also closes its meta line with a "No link" chip.
export function shoppingMissingLinkCount() {
  const cols = c();
  return shoppingItems().filter((item) => !buyUrl(item, cols)).length;
}

export function renderShopping() {
  const cols = c();
  const base = shoppingItems();
  const stores = [...new Set([...base.map((item) => item[cols.super]), ...Object.keys(state.shopping.extras)])]
    .sort((a, b) => storeLabel(a).localeCompare(storeLabel(b)));
  if (!stores.length) {
    activePaneBody().replaceChildren(emptyStateEl("circle-check", "All stocked up."));
    return;
  }
  const paneBody = activePaneBody();
  // Store panels fold by default (summary carries the done/total readout);
  // harvest the live open state so a Got-it re-render keeps your store open.
  const openStores = new Set(
    [...paneBody.querySelectorAll("details[data-store][open]")].map((d) => d.dataset.store),
  );
  paneBody.innerHTML = stores.map((store) => {
    const storeItems = base.filter((item) => item[cols.super] === store);
    const extras = state.shopping.extras[store] || [];
    const extraBought = new Set(state.shopping.extraBought[store] || []);
    const offset = state.shopping.offsets[store] || { items: 0, units: 0 };
    const totalItems = storeItems.length + extras.length;
    const totalUnits = storeItems.reduce((n, item) => n + Number(item[cols.comprar] || 0), 0) + extras.reduce((n, item) => n + Number(item.qty || 0), 0);
    const doneItems = storeItems.filter((item) => state.shopping.bought.has(item.id)).length + extras.filter((item) => extraBought.has(item.id)).length + Number(offset.items || 0);
    const doneUnits = storeItems.filter((item) => state.shopping.bought.has(item.id)).reduce((n, item) => n + Number(item[cols.comprar] || 0), 0) + extras.filter((item) => extraBought.has(item.id)).reduce((n, item) => n + Number(item.qty || 0), 0) + Number(offset.units || 0);
    const label = storeLabel(store);
    const rows = storeItems.map((item) => shoppingRow(item, cols)).join("")
      + extras.map((item) => extraRow(item, store, extraBought)).join("")
      + addRow(label) + optionsRow(offset);
    return `<details class="card card--collapsible" data-store="${html(store)}"${openStores.has(store) ? " open" : ""}>
      <summary class="collapse-summary">
        <span class="collapse-main">
          ${icon("shopping-basket")}
          <h3 class="collapse-title" data-store-label="${esc(store)}">${html(label)}</h3>
          <span class="collapse-count">${doneItems}/${totalItems} items · ${doneUnits}/${totalUnits} units</span>
        </span>
        <span class="collapse-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="collapse-body">${itemList(rows, { bare: true })}</div>
    </details>`;
  }).join("");
}

// A list row: Got it is the leading check (#252 decision 4), the buy link the
// one trailing item; a row with no link says so in its meta instead.
function shoppingRow(item, cols) {
  const bought = state.shopping.bought.has(item.id);
  const url = buyUrl(item, cols);
  const name = text(item[cols.comida]);
  return itemRow({
    title: name,
    struck: bought,
    meta: `${text(item[cols.lugar])} · buy ${item[cols.comprar]}`,
    chip: url ? null : { label: "No link", tone: "attention" },
    lead: { pressed: bought, action: bought ? "undo-buy" : "mark-buy", label: `Got ${name}` },
    trail: url ? rowLink(url, `Buy ${name}`) : "",
    attrs: `data-id="${item.id}"`,
  });
}

// A free-text quick-add: no spreadsheet row behind it, so nothing to open. Its
// Remove lives in the store sheet (#252 decision 6).
function extraRow(item, store, extraBought) {
  const bought = extraBought.has(item.id);
  return itemRow({
    title: item.name,
    struck: bought,
    meta: `Added here · buy ${item.qty}`,
    lead: { pressed: bought, action: bought ? "undo-extra" : "mark-extra", label: `Got ${item.name}` },
    attrs: `data-extra-id="${item.id}" data-store="${html(store)}"`,
    open: false,
  });
}

// The quick-add as the list's last row: a name, a quantity, one add button.
function addRow(label) {
  return `<li class="action-row store-add-row">
      <form class="quick-add">
        ${icon("plus", "store-add-icon")}
        <input class="field" name="name" placeholder="Add to ${esc(label)}" aria-label="Item to add to ${esc(label)}" required>
        <input class="field quick-add-qty" name="qty" type="number" min="1" value="1" aria-label="Quantity">
        <button class="icon-button" type="submit" aria-label="Add to ${esc(label)}" title="Add">${icon("arrow-right")}</button>
      </form>
    </li>`;
}

function optionsRow(offset) {
  const already = Number(offset.items || 0);
  return `<li class="action-row">
      <button type="button" class="action-row-main sheet-link" data-track="shop.store-options" data-store-sheet aria-haspopup="dialog">
        ${icon("sliders-horizontal", "sheet-link-icon")}
        <span class="action-row-title">Store options</span>
        <span class="sheet-link-value">${already ? `${already} already in cart` : ""}</span>
        ${icon("chevron-right", "sheet-link-chevron")}
      </button>
    </li>`;
}
