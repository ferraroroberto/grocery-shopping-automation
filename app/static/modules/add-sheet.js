// The Add item sheet: Items' list opens it from its Add item button.
// The store product search comes first (search.js renders into
// #product-search; a picked new product pre-fills the form below it), then
// adding by hand. The sheet's one primary submits that form with the same
// POST /api/items the retired Add item mode used. Esc or × discards.
//
// The sheet lives at <body> level like the item sheet, outside .app, so it
// carries its own listeners for the search and the form instead of app.js's
// delegation.
import { showToast } from "../_vendored/toast/toast.js";
import { fetchJson } from "./api.js";
import { defaultZone, render, state } from "./core.js";
import { icon, pickerMarkup, syncPicker } from "./dom.js";
import {
  addCandidateAsNew,
  cancelProductSearch,
  forgetPrefill,
  handleSearchInput,
  markPrefillAdded,
  renderSearch,
  startProductSearch,
  toggleSearchRecording,
  useCandidate,
  useCandidateClick,
} from "./search.js";
import { dialogShell, setDialogStatus } from "./stores/dialog.js";

const SHEET_ID = "add-sheet";

// A visible caption above a form input (wrapping <label>, so the name is
// programmatic too) — placeholders alone vanish once the box holds a value.
const labelled = (caption, input) => `<label class="field-label">${caption}${input}</label>`;

function dialog() {
  return dialogShell(SHEET_ID, "Add item", "Add item", (shell) => {
    shell.classList.add("add-sheet");
    const add = shell.querySelector(".detail-save-btn");
    add.type = "submit";
    add.setAttribute("form", "add-form");
    add.disabled = false;
    shell.addEventListener("click", onSheetClick);
    shell.addEventListener("input", (event) => handleSearchInput(event.target));
    shell.addEventListener("change", (event) => {
      if (event.target.matches("[data-picker] select")) syncPicker(event.target.closest("[data-picker]"));
    });
    shell.addEventListener("keydown", (event) => {
      if (event.target.id === "search-term" && event.key === "Enter") {
        event.preventDefault();
        startProductSearch();
      }
    });
    shell.addEventListener("submit", onSubmit);
    shell.addEventListener("close", () => document.querySelector("#items-add")?.focus());
  });
}

// The form, fresh on every open: what a closed sheet staged is gone.
function formMarkup() {
  const zones = state.payload.summary.zones;
  const stores = state.payload.summary.supermarkets;
  return `<section id="product-search" class="add-sheet-section" aria-label="Find a store product"></section>
    <section class="add-sheet-section" aria-labelledby="add-by-hand-title">
      <h3 class="card-title" id="add-by-hand-title">${icon("pencil")}Add by hand</h3>
      <form id="add-form" class="form">
        ${labelled("Item", `<input class="field" name="comida" placeholder="Item name" required />`)}
        <div class="add-form-pair">
          ${pickerMarkup("super", "Supermarket", stores, stores[0], "New supermarket…")}
          ${pickerMarkup("lugar", "Zone", zones, defaultZone(), "New zone…")}
        </div>
        <div class="add-form-pair">
          ${labelled("Target", `<input class="field" name="cantidad" type="number" min="0" value="0" placeholder="Target" />`)}
          ${labelled("In stock", `<input class="field" name="tenemos" type="number" min="0" value="0" placeholder="In stock" />`)}
        </div>
        ${labelled("URL", `<input class="field" name="buscador" placeholder="URL" inputmode="url" autocomplete="off" spellcheck="false" />`)}
      </form>
    </section>`;
}

export function openAddSheet() {
  const d = dialog();
  d.querySelector(".stores-dialog-body").innerHTML = formMarkup();
  setDialogStatus(d, "");
  forgetPrefill();
  d.showModal();
  renderSearch();
}

async function onSheetClick(event) {
  // Buttons carry inline sprite icons, so the click target can be the <svg>
  // — resolve the owning button before dispatching on id.
  const button = event.target.closest("button");
  if (!button) return;
  if (button.id === "search-record") await toggleSearchRecording(button);
  if (button.id === "search-run") await startProductSearch();
  if (button.id === "search-cancel") await cancelProductSearch();
  const card = button.closest(".candidate");
  if (button.dataset.action === "search-use") useCandidateClick(card);
  if (button.dataset.action === "search-confirm") await useCandidate(card);
  if (button.dataset.action === "search-add-new") addCandidateAsNew(card);
}

async function onSubmit(event) {
  event.preventDefault();
  if (!event.target.matches("#add-form")) return;
  const d = dialog();
  const button = d.querySelector(".detail-save-btn");
  const data = Object.fromEntries(new FormData(event.target).entries());
  data.cantidad = Number(data.cantidad);
  data.tenemos = Number(data.tenemos);
  button.disabled = true;
  setDialogStatus(d, "");
  try {
    state.payload = await fetchJson("/api/items", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(data),
    });
  } catch (error) {
    // e.g. 423 — the spreadsheet is open in Excel; the server's hint says so.
    setDialogStatus(d, error.message);
    return;
  } finally {
    button.disabled = false;
  }
  markPrefillAdded(data.buscador); // a product-search pick that filled this form → Added
  d.close();
  render();
  showToast(`${data.comida} added`);
}
