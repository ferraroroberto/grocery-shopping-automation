// The store sheet (#258, #252 decision 6): what a store group keeps out of the
// way — the counts already in the cart before you started (the cart offsets),
// the quick-adds with their Remove, and Unmark all for the store. Opened by the
// Store options row at the foot of each group in Shop.
//
// Every change saves as it happens (design.md "settings group"); Done closes.
// The sheet lives at <body> level, outside .app, so it carries its own
// listeners and re-renders Shop through core's render().
import { storeLabel } from "./automation.js";
import { c, items, render, saveShoppingState, state } from "./core.js";
import { esc, icon } from "./dom.js";
import { dialogShell } from "./stores/dialog.js";

const SHEET_ID = "store-sheet";

let current = null; // the store (as the list holds it) the sheet is open on

function sheet() {
  return dialogShell(SHEET_ID, "", "Done", (shell) => {
    shell.classList.add("store-sheet");
    const done = shell.querySelector(".detail-save-btn");
    done.disabled = false;
    done.addEventListener("click", () => shell.close());
    shell.addEventListener("click", onClick);
    shell.addEventListener("change", onChange);
    shell.addEventListener("close", () => {
      document.querySelector(`#pane-shopping details[data-store="${CSS.escape(current)}"] [data-store-sheet]`)?.focus();
    });
  });
}

export function openStoreSheet(store) {
  current = store;
  const d = sheet();
  d.querySelector(`#${SHEET_ID}-title`).textContent = storeLabel(store);
  paint();
  if (!d.open) d.showModal();
}

function boughtHere() {
  const cols = c();
  return items().filter((item) => item[cols.super] === current && state.shopping.bought.has(item.id)).map((item) => item.id);
}

function paint() {
  const d = document.getElementById(SHEET_ID);
  const offset = state.shopping.offsets[current] || { items: 0, units: 0 };
  const extras = state.shopping.extras[current] || [];
  const got = boughtHere().length + (state.shopping.extraBought[current] || []).length;
  const extraRows = extras.map((x) => `<li class="action-row" data-extra-id="${x.id}">
      <div class="action-row-main"><span class="action-row-title">${esc(x.name)}</span><span class="action-row-meta">Buy ${esc(x.qty)}</span></div>
      <button type="button" class="icon-button action-row-trail" data-action="remove-extra" aria-label="Remove ${esc(x.name)}" title="Remove ${esc(x.name)}">${icon("x")}</button>
    </li>`).join("");
  d.querySelector(".stores-dialog-body").innerHTML = `
    <section class="store-sheet-section" aria-labelledby="store-sheet-cart">
      <h3 class="overline" id="store-sheet-cart">Already in cart</h3>
      <div class="two">
        <label class="field-label">Items<input class="field" data-action="offset-items" type="number" min="0" value="${Number(offset.items || 0)}"></label>
        <label class="field-label">Units<input class="field" data-action="offset-units" type="number" min="0" value="${Number(offset.units || 0)}"></label>
      </div>
    </section>
    ${extras.length ? `<section class="store-sheet-section" aria-labelledby="store-sheet-added">
      <h3 class="overline" id="store-sheet-added">Added here</h3>
      <ul class="action-rows">${extraRows}</ul>
    </section>` : ""}
    <button type="button" class="button-surface btn-block" data-store-unmark${got ? "" : " disabled"}>Unmark all${got ? ` (${got})` : ""}</button>`;
}

function onChange(event) {
  const action = event.target.dataset.action;
  if (action !== "offset-items" && action !== "offset-units") return;
  const offset = (state.shopping.offsets[current] ||= { items: 0, units: 0 });
  offset[action === "offset-items" ? "items" : "units"] = Number(event.target.value || 0);
  saveShoppingState();
  render();
}

function onClick(event) {
  const remove = event.target.closest('[data-action="remove-extra"]');
  if (remove) {
    const id = Number(remove.closest("[data-extra-id]").dataset.extraId);
    state.shopping.extras[current] = (state.shopping.extras[current] || []).filter((x) => x.id !== id);
    state.shopping.extraBought[current] = (state.shopping.extraBought[current] || []).filter((x) => x !== id);
  } else if (event.target.closest("[data-store-unmark]")) {
    for (const id of boughtHere()) state.shopping.bought.delete(id);
    state.shopping.extraBought[current] = [];
  } else {
    return;
  }
  saveShoppingState();
  render();
  paint();
}
