// The shopping list: one folding panel per store, with Got-it marks, manual
// cart offsets and free-text quick-adds. All of it is client-side progress
// (persisted via core's shopping state) over the server's buy list.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { activePaneBody, c, items, state } from "./core.js";
import { html, text } from "./dom.js";

function shoppingItems() {
  const cols = c();
  return items().filter((item) => Number(item[cols.comprar]) > 0);
}

export function renderShopping() {
  const cols = c();
  const base = shoppingItems();
  const stores = [...new Set([...base.map((item) => item[cols.super]), ...Object.keys(state.shopping.extras)])].sort();
  if (!stores.length) {
    activePaneBody().replaceChildren(emptyStateEl("circle-check", "All stocked up."));
    return;
  }
  const missingLink = base.filter((item) => text(item[cols.buscador]) === "-" || !text(item[cols.buscador]).trim());
  const boughtCount = state.shopping.bought.size + Object.values(state.shopping.extraBought || {}).reduce((n, list) => n + (list?.length || 0), 0);
  // Header panel only when it has something to say (unmark-all / warnings) —
  // an empty card under the page title is noise.
  const header = (boughtCount || missingLink.length) ? `<section class="panel">
    <div class="row"><h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-shopping-cart"></use></svg>Shopping</h2>${boughtCount ? `<button class="secondary" id="shopping-unmark-all" type="button">Unmark all</button>` : ""}</div>
    ${missingLink.length ? `<div class="panel-status error">${missingLink.length} item(s) missing a buy link — their Buy button is disabled.</div>` : ""}
  </section>` : "";
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
          <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-shopping-basket"></use></svg>
          <h3 class="collapse-title">${html(store)}</h3>
          <span class="collapse-count">${doneItems}/${totalItems} items · ${doneUnits}/${totalUnits} units</span>
        </span>
        <span class="collapse-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="collapse-body">
        <div class="two">
          <label class="hint">Cart items offset<input class="field" data-action="offset-items" type="number" min="0" value="${Number(offset.items || 0)}"></label>
          <label class="hint">Cart units offset<input class="field" data-action="offset-units" type="number" min="0" value="${Number(offset.units || 0)}"></label>
        </div>
        <div class="grid">
          ${storeItems.map((item) => shoppingRow(item, cols)).join("")}
          ${extras.map((item) => extraRow(item, store, extraBought)).join("")}
        </div>
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

function shoppingRow(item, cols) {
  const bought = state.shopping.bought.has(item.id);
  const url = text(item[cols.buscador]) === "-" ? "" : text(item[cols.buscador]);
  return `<article class="item" data-id="${item.id}">
    <div><h3>${bought ? `<s>${html(item[cols.comida])}</s>` : html(item[cols.comida])}</h3><div class="meta">${html(item[cols.lugar])} · ${item[cols.comprar]}x</div></div>
    <div class="item-actions">
      <button class="secondary" data-action="open-buy" ${url ? `data-url="${html(url)}"` : "disabled"}>${bought ? "Again" : "Buy"}</button>
      <button class="secondary" data-action="${bought ? "undo-buy" : "mark-buy"}">${bought ? "Undo" : "Got it"}</button>
    </div>
  </article>`;
}

function extraRow(item, store, extraBought) {
  const bought = extraBought.has(item.id);
  return `<article class="item" data-extra-id="${item.id}" data-store="${html(store)}">
    <div><h3>${bought ? `<s>${html(item.name)}</s>` : html(item.name)} <span class="meta">+</span></h3><div class="meta">${item.qty}x</div></div>
    <div class="item-actions">
      <button class="danger" data-action="remove-extra">Remove</button>
      <button class="secondary" data-action="${bought ? "undo-extra" : "mark-extra"}">${bought ? "Undo" : "Got it"}</button>
    </div>
  </article>`;
}
