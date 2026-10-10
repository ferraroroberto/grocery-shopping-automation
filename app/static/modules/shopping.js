// The shopping list: one folding panel per store, with Got-it marks, manual
// cart offsets and free-text quick-adds. All of it is client-side progress
// (persisted via core's shopping state) over the server's buy list.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { activePaneBody, c, items, state } from "./core.js";
import { html, icon, text } from "./dom.js";
import { itemList, itemRow, rowLink, rowVerb } from "./rows.js";

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
  const stores = [...new Set([...base.map((item) => item[cols.super]), ...Object.keys(state.shopping.extras)])].sort();
  if (!stores.length) {
    activePaneBody().replaceChildren(emptyStateEl("circle-check", "All stocked up."));
    return;
  }
  const boughtCount = state.shopping.bought.size + Object.values(state.shopping.extraBought || {}).reduce((n, list) => n + (list?.length || 0), 0);
  // The missing-link count lives in the page header now (one home per fact),
  // so the only thing left above the stores is Unmark all, once something is got.
  const header = boughtCount ? `<div class="actions"><button class="secondary" id="shopping-unmark-all" type="button">Unmark all</button></div>` : "";
  const paneBody = activePaneBody();
  // Store panels fold by default (summary carries the done/total readout);
  // harvest the live open state so a Got-it re-render keeps your store open.
  const openStores = new Set(
    [...paneBody.querySelectorAll("details[data-store][open]")].map((d) => d.dataset.store),
  );
  paneBody.innerHTML = header + stores.map((store) => {
    const storeItems = base.filter((item) => item[cols.super] === store);
    const extras = state.shopping.extras[store] || [];
    const extraBought = new Set(state.shopping.extraBought[store] || []);
    const offset = state.shopping.offsets[store] || { items: 0, units: 0 };
    const totalItems = storeItems.length + extras.length;
    const totalUnits = storeItems.reduce((n, item) => n + Number(item[cols.comprar] || 0), 0) + extras.reduce((n, item) => n + Number(item.qty || 0), 0);
    const doneItems = storeItems.filter((item) => state.shopping.bought.has(item.id)).length + extras.filter((item) => extraBought.has(item.id)).length + Number(offset.items || 0);
    const doneUnits = storeItems.filter((item) => state.shopping.bought.has(item.id)).reduce((n, item) => n + Number(item[cols.comprar] || 0), 0) + extras.filter((item) => extraBought.has(item.id)).reduce((n, item) => n + Number(item.qty || 0), 0) + Number(offset.units || 0);
    return `<details class="card card--collapsible" data-store="${html(store)}"${openStores.has(store) ? " open" : ""}>
      <summary class="collapse-summary">
        <span class="collapse-main">
          ${icon("shopping-basket")}
          <h3 class="collapse-title">${html(store)}</h3>
          <span class="collapse-count">${doneItems}/${totalItems} items · ${doneUnits}/${totalUnits} units</span>
        </span>
        <span class="collapse-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="collapse-body">
        <div class="two">
          <label class="hint">Items already in cart<input class="field" data-action="offset-items" type="number" min="0" value="${Number(offset.items || 0)}"></label>
          <label class="hint">Units already in cart<input class="field" data-action="offset-units" type="number" min="0" value="${Number(offset.units || 0)}"></label>
        </div>
        ${itemList(storeItems.map((item) => shoppingRow(item, cols)).join("") + extras.map((item) => extraRow(item, store, extraBought)).join(""), { bare: true })}
        <form class="form quick-add">
          <div class="three">
            <input class="field" name="name" placeholder="Quick-add item" required>
            <input class="field" name="qty" type="number" min="1" value="1">
            <button class="secondary" type="submit">Add</button>
          </div>
        </form>
      </div>
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

// A free-text quick-add: no spreadsheet row behind it, so nothing to open.
function extraRow(item, store, extraBought) {
  const bought = extraBought.has(item.id);
  return itemRow({
    title: item.name,
    struck: bought,
    meta: `Added here · buy ${item.qty}`,
    lead: { pressed: bought, action: bought ? "undo-extra" : "mark-extra", label: `Got ${item.name}` },
    trail: rowVerb("remove-extra", `Remove ${item.name}`, "x"),
    attrs: `data-extra-id="${item.id}" data-store="${html(store)}"`,
    open: false,
  });
}
