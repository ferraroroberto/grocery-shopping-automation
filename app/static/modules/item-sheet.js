// The item sheet (#255): one home for an item's facts, opened from every row
// that shows an item (design.md "editor modal", the detail sheet). The main
// page holds the in-stock and target steppers, then link rows (design.md
// "settings group": glyph, label, muted value, chevron) to pages of the same
// sheet — Zone, Store, Buy link, Stores & prices, Quantity check, Name — and
// Delete as the in-body danger action behind the confirm sheet.
//
// The item's own cells are staged (design.md "dense collection"): Save is the
// one persistence boundary, posting the whole row exactly as the old Edit item
// form did, and Esc or × discards. The two store pages (stores/review.js) keep
// their per-store saves, which write at once as before.
import { showToast } from "../_vendored/toast/toast.js";
import { fetchJson } from "./api.js";
import { confirmSheet } from "./confirm.js";
import { c, render, state } from "./core.js";
import { esc, icon, pickerMarkup, syncPicker, text } from "./dom.js";
import { stepper } from "./rows.js";
import { dialogShell, setDialogStatus } from "./stores/dialog.js";
import { paintList } from "./stores/list.js";
import { afterReviewChange, closeReview, onReviewClick, onReviewSubmit, openReview, paintQuantity, reviewSummary } from "./stores/review.js";
import { itemById, jsonInit, local } from "./stores/state.js";

const SHEET_ID = "item-sheet";

// The cells Save posts: PUT /api/items/{id} takes the whole row.
const FIELDS = ["comida", "lugar", "super", "buscador", "cantidad", "tenemos"];
const NUMBERS = new Set(["cantidad", "tenemos"]);
const STEPPER_FIELD = { current: "tenemos", target: "cantidad" };

// The link rows, in the spec's order (#252); Name is last, as in the fleet's
// other sheets, since it keeps the old Edit item's rename reachable.
const PAGES = [
  { page: "zone", glyph: "map-pin", label: "Zone" },
  { page: "store", glyph: "store", label: "Store" },
  { page: "link", glyph: "link", label: "Buy link" },
  { page: "prices", glyph: "tag", label: "Stores & prices" },
  { page: "quantity", glyph: "scale", label: "Quantity check" },
  { page: "name", glyph: "pencil", label: "Name" },
];
const PAGE_TITLE = Object.fromEntries(PAGES.map(({ page, label }) => [page, label]));

const sheet = { id: null, page: "main", draft: {} };

function item() {
  return itemById(sheet.id);
}

// A cell as the spreadsheet holds it now — read from the latest payload, so a
// store page's own save (a new list-store link moves the buy link) is seen.
function saved(field) {
  const raw = item()?.[c()[field]];
  return NUMBERS.has(field) ? Number(raw) || 0 : String(raw ?? "");
}

function value(field) {
  return field in sheet.draft ? sheet.draft[field] : saved(field);
}

function setDraft(field, next) {
  sheet.draft[field] = next;
  paintValues();
}

function changed() {
  return FIELDS.some((field) => field in sheet.draft && sheet.draft[field] !== saved(field));
}

function dialog() {
  return dialogShell(SHEET_ID, "", "Save", (shell) => {
    shell.classList.add("item-sheet");
    shell.querySelector(".detail-header").insertAdjacentHTML("afterbegin",
      `<button type="button" class="icon-button detail-close item-sheet-back" aria-label="Back" data-sheet-back hidden>${icon("chevron-left")}</button>`);
    shell.querySelector(".stores-dialog-body").innerHTML = pagesMarkup();
    shell.querySelector(".detail-save-btn").addEventListener("click", save);
    shell.addEventListener("click", onSheetClick);
    shell.addEventListener("input", onSheetInput);
    shell.addEventListener("change", onSheetChange);
    shell.addEventListener("submit", (event) => {
      event.preventDefault();
      onReviewSubmit(event);
    });
    shell.addEventListener("close", onSheetClose);
  });
}

function pagesMarkup() {
  const links = PAGES.map(({ page, glyph, label }) => `<li class="action-row">
      <button type="button" class="action-row-main sheet-link" data-page-to="${page}">
        ${icon(glyph, "sheet-link-icon")}
        <span class="action-row-title">${label}</span>
        <span class="sheet-link-value" data-link-value="${page}"></span>
        ${icon("chevron-right", "sheet-link-chevron")}
      </button>
    </li>`).join("");
  return `<div class="item-sheet-page" data-page="main">
      <div class="item-sheet-qty" data-sheet-qty="tenemos"><span class="item-sheet-label">In stock</span></div>
      <div class="item-sheet-qty" data-sheet-qty="cantidad"><span class="item-sheet-label">Target</span></div>
      <ul class="action-rows item-sheet-links">${links}</ul>
      <button type="button" class="button-tint danger item-sheet-delete" data-sheet-delete>${icon("trash-2")}Delete item</button>
    </div>
    <div class="item-sheet-page" data-page="zone" hidden></div>
    <div class="item-sheet-page" data-page="store" hidden></div>
    <div class="item-sheet-page" data-page="link" hidden></div>
    <div class="item-sheet-page" data-page="name" hidden></div>
    <div class="item-sheet-page" data-page="quantity" hidden></div>
    <div class="item-sheet-page" data-page="prices" hidden></div>`;
}

// The four field pages, filled from the sheet's values each time it opens.
// Zone and Store are the Add item pickers (#241): the sheet's values, plus a
// "New…" escape for a first-time one.
function paintFieldPages() {
  const d = dialog();
  const summary = state.payload.summary;
  const options = (list, current) => [...new Set([...list, current].filter(Boolean))];
  d.querySelector('[data-page="zone"]').innerHTML =
    pickerMarkup("lugar", "Zone", options(summary.zones, value("lugar")), value("lugar"), "New zone…");
  d.querySelector('[data-page="store"]').innerHTML =
    pickerMarkup("super", "Store", options(summary.supermarkets, value("super")), value("super"), "New store…");
  d.querySelector('[data-page="link"]').innerHTML = `<label class="field-label">Buy link
      <input class="field" name="buscador" value="${esc(value("buscador"))}" placeholder="https://…"
             inputmode="url" autocomplete="off" spellcheck="false" data-sheet-field="buscador">
    </label>`;
  d.querySelector('[data-page="name"]').innerHTML = `<label class="field-label">Name
      <input class="field" name="comida" value="${esc(value("comida"))}" autocomplete="off" data-sheet-field="comida" required>
    </label>`;
}

function paintSteppers() {
  const d = dialog();
  const name = text(value("comida"));
  for (const [kind, field] of Object.entries(STEPPER_FIELD)) {
    const row = d.querySelector(`[data-sheet-qty="${field}"]`);
    row.querySelector(".stepper")?.remove();
    row.insertAdjacentHTML("beforeend", stepper(kind, value(field), name));
  }
}

// A store as the Stores registry names it ("Ametller Origen"), else as typed.
function storeLabel(raw) {
  const key = raw.trim().toLowerCase();
  return local.meta?.stores.find((s) => s.key === key)?.name || raw;
}

function buyLinkValue() {
  const url = value("buscador").trim();
  if (!url) return { text: "No link", tone: "attention" };
  try {
    return { text: new URL(url).hostname.replace(/^www\./, "") };
  } catch (_) {
    return { text: url }; // a plain search term, as some rows hold
  }
}

// Everything that shows a value: the steppers' figures, each link row's muted
// value, the title, and whether there is anything to Save.
function paintValues() {
  const d = dialog();
  for (const field of Object.values(STEPPER_FIELD)) {
    const output = d.querySelector(`[data-sheet-qty="${field}"] .stepper-value`);
    if (output) output.textContent = value(field);
  }
  const review = reviewSummary();
  const values = {
    zone: { text: value("lugar") },
    store: { text: storeLabel(value("super")) },
    link: buyLinkValue(),
    prices: review.prices,
    quantity: review.quantity,
    name: { text: value("comida") },
  };
  for (const [page, { text: label, tone }] of Object.entries(values)) {
    const node = d.querySelector(`[data-link-value="${page}"]`);
    node.textContent = label;
    if (tone) node.dataset.tone = tone;
    else delete node.dataset.tone;
  }
  paintTitle();
  d.querySelector(".detail-save-btn").disabled = !changed();
}

function paintTitle() {
  const d = dialog();
  d.querySelector(`#${SHEET_ID}-title`).textContent =
    sheet.page === "main" ? text(value("comida")) : PAGE_TITLE[sheet.page];
}

// One page at a time (home-automation's unit sheet, #881): a link row opens
// its page, whose header then names it and carries Back.
function showPage(page) {
  const d = dialog();
  sheet.page = page;
  d.dataset.page = page;
  d.querySelectorAll(".item-sheet-page").forEach((node) => { node.hidden = node.dataset.page !== page; });
  d.querySelector("[data-sheet-back]").hidden = page === "main";
  if (page === "quantity") paintQuantity();
  paintTitle();
}

function openPage(page) {
  showPage(page);
  const d = dialog();
  (d.querySelector(`[data-page="${page}"] :is(input:not([hidden]), select)`) || d.querySelector("[data-sheet-back]")).focus();
}

function backToMain() {
  const from = sheet.page;
  showPage("main");
  dialog().querySelector(`[data-page-to="${from}"]`)?.focus();
}

// Open the sheet on an item, on its main page or straight onto one of its
// pages (the Stores list's pencil opens Stores & prices).
export async function openItemSheet(id, page = "main") {
  if (!itemById(id)) return;
  const d = dialog();
  sheet.id = id;
  sheet.draft = {};
  setDialogStatus(d, "");
  paintFieldPages();
  paintSteppers();
  showPage(page);
  if (!d.open) d.showModal();
  if (page !== "main") openPage(page);
  await openReview(id, { dialog: d, value, setDraft, onDetail: paintValues });
}

async function onSheetClick(event) {
  if (await onReviewClick(event)) return;
  const target = event.target;
  const to = target.closest("[data-page-to]");
  if (to) {
    openPage(to.dataset.pageTo);
    return;
  }
  if (target.closest("[data-sheet-back]")) {
    backToMain();
    return;
  }
  if (target.closest("[data-sheet-delete]")) {
    await remove();
    return;
  }
  const action = target.closest(".stepper [data-action]")?.dataset.action;
  if (action) {
    const [kind, step] = action.split("-");
    const field = STEPPER_FIELD[kind];
    setDraft(field, Math.max(0, value(field) + (step === "plus" ? 1 : -1)));
  }
}

function onSheetInput(event) {
  const field = event.target.dataset.sheetField || event.target.closest("[data-picker]")?.querySelector("input[name]")?.name;
  if (field && event.target.matches("input")) setDraft(field, event.target.value);
}

function onSheetChange(event) {
  const picker = event.target.closest("[data-picker]");
  if (!picker || !event.target.matches("select")) return;
  syncPicker(picker);
  const input = picker.querySelector("input[name]");
  setDraft(input.name, input.value);
}

async function save() {
  const d = dialog();
  const button = d.querySelector(".detail-save-btn");
  const row = Object.fromEntries(FIELDS.map((field) => [field, value(field)]));
  for (const [field, label] of [["comida", "Name"], ["lugar", "Zone"], ["super", "Store"]]) {
    if (!String(row[field]).trim()) {
      setDialogStatus(d, `${label} can't be empty.`);
      openPage(field === "comida" ? "name" : field === "lugar" ? "zone" : "store");
      return;
    }
  }
  button.disabled = true;
  setDialogStatus(d, "");
  try {
    state.payload = await fetchJson(`/api/items/${sheet.id}`, jsonInit("PUT", row));
    sheet.draft = {};
    d.close();
    showToast("Saved");
    // A target moving to or from 0 moves the item in or out of the what-if.
    afterReviewChange();
  } catch (error) {
    // e.g. 423 — the spreadsheet is open in Excel; the server's hint says so.
    setDialogStatus(d, error.message);
    button.disabled = !changed();
  }
}

async function remove() {
  const d = dialog();
  const name = text(saved("comida"));
  const yes = await confirmSheet({
    title: "Delete item?",
    message: `${name} is removed from the spreadsheet. This cannot be undone.`,
    confirmLabel: "Delete",
    danger: true,
  });
  if (!yes) return;
  setDialogStatus(d, "");
  try {
    state.payload = await fetchJson(`/api/items/${sheet.id}`, { method: "DELETE" });
    sheet.draft = {};
    d.close();
    showToast(`${name} deleted`);
    afterReviewChange();
  } catch (error) {
    setDialogStatus(d, error.message);
  }
}

// Esc, × and Save all close here. A staged change not saved is dropped; the
// list behind repaints (a store page may have saved), and focus goes back to
// the row that opened the sheet — a repaint may have replaced it.
function onSheetClose() {
  const id = sheet.id;
  sheet.draft = {};
  closeReview();
  showPage("main");
  if (state.mode === "stores") paintList();
  else render();
  (document.querySelector(`.store-row[data-item-id="${id}"] [data-stores-action="detail"]`)
    || document.querySelector(`.item-row[data-id="${id}"] .action-row-main`))?.focus();
}
