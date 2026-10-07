// Stores: the per-item review dialog (#165) — quantity check, every store's product / pack /
// price with your overrides, the server-side Checked mark, and the link / override editors.
import { emptyStateEl } from "../../_vendored/empty-state/empty-state.js";
import { setSwitch } from "../../_vendored/switch/switch.js";
import { fetchJson } from "../api.js";
import { c, state } from "../core.js";
import { esc, icon, switchMarkup } from "../dom.js";
import { dialogShell, setDialogStatus } from "./dialog.js";
import { FLAG_LABELS, LINK_NOTE, flagLabel } from "./list.js";
import { loadChecks, scheduleSimulate } from "./simulator.js";
import { eur, itemById, jsonInit, local, num, plural, storeName } from "./state.js";

const DETAIL_ID = "stores-detail-dialog";

const STATUS_TEXT = {
  baseline: "Current product",
  equivalent: "Equivalent",
  upgrade: "Upgrade",
  unverified: "Unverified",
  not_found: "Not found",
  override: "Your values",
};

const OVERRIDE_UNITS = ["kg", "l", "ud", "m"];

// After anything that can move prices or review flags: the checks (pills,
// badges) and the simulator (totals, chip prices — it repaints the list).
function afterReviewChange() {
  loadChecks();
  scheduleSimulate(0);
}

function detailDialog() {
  return dialogShell(DETAIL_ID, "", "Save target & in stock", (dialog) => {
    dialog.classList.add("review-dialog");
    dialog.querySelector(".detail-save-btn").addEventListener("click", saveQuantities);
    dialog.addEventListener("click", onReviewClick);
    dialog.addEventListener("input", onReviewInput);
    dialog.addEventListener("submit", (event) => {
      event.preventDefault();
      const form = event.target.closest("[data-review-form]");
      if (form) saveStoreEdit(form);
    });
    // A pane repaint may have replaced the opener; hand focus back to its row.
    dialog.addEventListener("close", () => {
      local.editing = null;
      document.querySelector(`.store-row[data-item-id="${local.detailId}"] [data-stores-action="detail"]`)?.focus();
    });
  });
}

export async function openItemDetail(id) {
  local.detailId = id;
  local.detail = null;
  local.detailError = "";
  local.editing = null;
  local.qtyDraft = {};
  const dialog = detailDialog();
  setDialogStatus(dialog, "");
  paintDetail();
  dialog.showModal();
  await loadDetail();
}

async function loadDetail() {
  const id = local.detailId;
  try {
    const body = await fetchJson(`/api/items/${id}/store-detail`);
    if (id !== local.detailId) return;
    local.detail = body;
    local.detailError = "";
  } catch (error) {
    if (id !== local.detailId) return;
    local.detailError = error.message;
  }
  paintDetail();
}

function paintDetail() {
  const dialog = document.getElementById(DETAIL_ID);
  if (!dialog) return;
  const body = dialog.querySelector(".stores-dialog-body");
  const d = local.detail;
  dialog.querySelector(`#${DETAIL_ID}-title`).textContent = d?.comida || itemById(local.detailId)?.[c().comida] || "Item";
  if (!d) {
    const failed = !!local.detailError;
    const block = emptyStateEl(failed ? "circle-alert" : "refresh-cw",
      failed ? "Couldn't read this item's store details." : "Reading the item's store details…",
      failed ? { actionLabel: "Retry" } : undefined);
    block.dataset.state = failed ? "error" : "loading";
    block.querySelector(".empty-state-action")?.setAttribute("data-review-retry", "");
    body.replaceChildren(block);
    if (failed) setDialogStatus(dialog, local.detailError);
    syncQtySave();
    return;
  }
  const evidenceOpen = !!body.querySelector(".review-evidence[open]");
  body.innerHTML = `
    <section class="review-head">${reviewHeadMarkup(d)}</section>
    <section class="review-section" aria-labelledby="review-qty-title">
      <h3 id="review-qty-title" class="review-subtitle">Quantity check</h3>
      ${quantityMarkup(d)}
    </section>
    <section class="review-section" aria-labelledby="review-stores-title">
      <h3 id="review-stores-title" class="review-subtitle">Stores</h3>
      ${storesMarkup(d)}
      ${addLinkMarkup(d)}
    </section>
    ${evidenceMarkup(d, evidenceOpen)}`;
  syncQtySave();
}

function reviewHeadMarkup(d) {
  const badges = d.flags.map((f) =>
    `<span class="review-badge" title="${esc(FLAG_LABELS[f]?.[1] || "")}">${esc(flagLabel(f))}</span>`).join("");
  return `<div class="review-checked">
      ${switchMarkup(!!d.checked, `Checked ${d.comida}`, { id: "review-checked-switch", "data-review-checked": "" })}
      <label for="review-checked-switch" class="review-checked-label">Checked</label>
      <span class="meta">${d.checked ? `on ${esc(d.checked)}` : "not yet"}</span>
    </div>
    ${badges ? `<div class="review-badges">${badges}</div>` : `<p class="hint">Nothing flagged for this item.</p>`}`;
}

// "4 × 0.38 kg = 1.52 kg", degrading to what is known.
function amountText(side) {
  const unit = side.unit || "";
  if (side.packs === null || side.packs === undefined) {
    return side.pack_size ? `${num(side.pack_size)} ${unit} packs` : "pack unknown";
  }
  if (!side.pack_size) return `${plural(side.packs, "pack")} (pack size unknown)`;
  return `${side.packs} × ${num(side.pack_size)} ${unit} = ${num(side.total)} ${unit}`;
}

function deltaText(pct) {
  if (pct === null || pct === undefined) return "";
  if (pct === 0) return " (same amount)";
  return ` (${pct > 0 ? "+" : "−"}${num(Math.abs(pct))}%)`;
}

function quantityMarkup(d) {
  const q = d.quantity;
  const nowStore = q.now.store ? storeName(q.now.store) : "No store in your list";
  const lines = [];
  if (q.before) lines.push(["Before", `${storeName(q.before.store)} ${amountText(q.before)}`]);
  lines.push(["Now", `${nowStore} ${amountText(q.now)}${deltaText(q.delta_pct)}`]);
  if (d.monthly?.qty) {
    const packs = d.monthly.packs_at_list_store;
    lines.push(["You use", `≈ ${num(d.monthly.qty)} ${d.monthly.unit || ""} a month${packs ? ` (≈ ${num(packs)} packs at ${nowStore})` : ""}`]);
  }
  let note = "";
  if (!d.in_basket) note = "This item isn't in the benchmark basket, so there is no before to compare with.";
  else if (q.unit_mismatch) note = `The packs are in different units (${q.before.unit} before, ${q.now.unit} now), so the amounts can't be compared. Set the target by hand.`;
  else if (!q.now.pack_size) note = "The pack size at your list's store is unknown. Save it as your override below to compare.";
  const packCtx = q.now.pack_size ? `packs of ${num(q.now.pack_size)} ${q.now.unit || ""}` : "packs";
  const suggested = q.suggested || {};
  return `<dl class="review-qty-lines">${lines.map(([label, value]) =>
      `<div><dt>${label}</dt><dd>${esc(value)}</dd></div>`).join("")}</dl>
    ${note ? `<p class="review-warn">${icon("circle-alert")}${esc(note)}</p>` : ""}
    <div class="review-qty-fields">
      ${qtyFieldMarkup("cantidad", "Target", d.list.cantidad, suggested.cantidad, packCtx)}
      ${qtyFieldMarkup("tenemos", "In stock", d.list.tenemos, suggested.tenemos, packCtx)}
    </div>`;
}

function qtyFieldMarkup(field, label, saved, suggested, packCtx) {
  const value = local.qtyDraft[field] ?? saved;
  const suggest = suggested !== undefined && suggested !== null && suggested !== saved
    ? `<button type="button" class="button-surface review-suggest" data-review-suggest="${field}" data-value="${suggested}">Use suggested (${suggested})</button>`
    : "";
  return `<div class="review-qty-field">
      <label class="field-label" for="review-${field}">${label} <span class="meta">${esc(packCtx)}</span></label>
      <div class="review-qty-control">
        <input id="review-${field}" class="field review-num" type="number" min="0" step="1" inputmode="numeric"
               value="${esc(value)}" data-review-qty="${field}">
        ${suggest}
      </div>
    </div>`;
}

function statusText(s) {
  const label = s.status ? (STATUS_TEXT[s.status] || s.status.replaceAll("_", " ")) : (s.source === "link-only" ? "Link only" : "–");
  const parts = [label];
  if (s.confidence) parts.push(`${s.confidence} confidence`);
  if (s.status && !s.priced) parts.push("not priced");
  return parts.join(" · ");
}

function packOf(v) {
  return v?.pack_size ? `${num(v.pack_size)} ${v.unit || ""}`.trim() : "";
}

function isSet(value) {
  return value !== null && value !== undefined && value !== "";
}

// "yours" beside a value the household overrode, with the benchmark's value.
function yoursMarkup(s, fields, benchText) {
  if (!s.override || !fields.some((f) => isSet(s.override[f]))) return "";
  return ` <span class="chip chip-match">yours</span>${benchText ? ` <span class="review-bench">benchmark ${esc(benchText)}</span>` : ""}`;
}

function storesMarkup(d) {
  if (!d.stores.length) return emptyStateEl("link", "No store has this item yet. Add a link below.").outerHTML;
  const head = ["Store", "Product & link", "Pack", "Pack price", "Per unit", "Per month", "Status"];
  return `<table class="review-stores">
    <colgroup><col class="col-store"><col class="col-product"><col class="col-num"><col class="col-num"><col class="col-num"><col class="col-num"><col class="col-status"><col class="col-edit"></colgroup>
    <thead><tr>${head.map((h) => `<th scope="col">${h}</th>`).join("")}<th scope="col"><span class="visually-hidden">Edit</span></th></tr></thead>
    <tbody>${d.stores.map((s) => storeRowMarkup(d, s)).join("")}</tbody>
  </table>`;
}

function storeRowMarkup(d, s) {
  const editing = local.editing === s.store;
  const bench = s.benchmark || {};
  const markers = [
    s.is_list_store ? `<span class="chip chip-match">in list</span>` : "",
    s.is_basket_store ? `<span class="chip chip-neutral">benchmarked from</span>` : "",
  ].join("");
  const link = s.url
    ? `<a class="review-url" href="${esc(s.url)}" target="_blank" rel="noopener noreferrer">${esc(s.url)}</a>`
    : `<span class="meta">No link yet</span>`;
  const kind = s.url_kind && s.url_kind !== "product"
    ? `<span class="review-note">${icon(s.url_kind === "search" ? "search" : "circle-alert")}This link ${esc(LINK_NOTE[s.url_kind] || "may not open the product")}</span>` : "";
  const benchLink = s.benchmark_url && s.benchmark_url !== s.url
    ? `<span class="review-note">Benchmark priced <a class="review-url" href="${esc(s.benchmark_url)}" target="_blank" rel="noopener noreferrer">${esc(s.benchmark_url)}</a></span>` : "";
  const changed = s.benchmark_changed
    ? `<span class="review-warn">${icon("circle-alert")}The benchmark changed since you saved this override.</span>` : "";
  const unit = s.unit || "";
  const row = `<tr class="review-store${s.is_list_store ? " is-list" : ""}" data-review-store="${esc(s.store)}">
    <td class="review-cell-store" data-label="Store"><span class="review-store-name">${esc(s.store_name)}</span>${markers}</td>
    <td class="review-cell-product" data-label="Product">
      <span class="review-product-name">${esc(s.name || "No product")}${yoursMarkup(s, ["name"], bench.name)}</span>
      ${optionsMarkup(s)}${link}${kind}${benchLink}${changed}
    </td>
    <td class="review-cell-num" data-label="Pack">${esc(packOf(s) || "–")}${yoursMarkup(s, ["pack_size", "unit"], packOf(bench))}</td>
    <td class="review-cell-num" data-label="Pack price">${eur(s.pack_price)}${yoursMarkup(s, ["pack_price"], isSet(bench.pack_price) ? eur(bench.pack_price) : "")}</td>
    <td class="review-cell-num" data-label="Per unit">${s.price_per_unit === null ? "–" : `${eur(s.price_per_unit)}/${esc(unit)}`}</td>
    <td class="review-cell-num" data-label="Per month">${eur(s.monthly_cost)}</td>
    <td class="review-cell-status" data-label="Status">${esc(statusText(s))}</td>
    <td class="review-cell-edit"><button type="button" class="icon-button" data-review-edit="${esc(s.store)}"
        aria-expanded="${editing}" aria-label="Edit ${esc(s.store_name)}">${icon(editing ? "x" : "pencil")}</button></td>
  </tr>`;
  return editing ? row + `<tr class="review-edit-row"><td colspan="8">${editFormMarkup(d, s)}</td></tr>` : row;
}

// A page option the cart automation picks for this product (#179), e.g. the
// fish cut; set in config/product_options.json, shown here so it isn't hidden.
function optionsMarkup(s) {
  const cut = s.options?.cut;
  if (!cut) return "";
  const note = s.options.note ? ` <span class="meta">${esc(s.options.note)}</span>` : "";
  return `<span class="review-option"><span class="chip chip-neutral">Cut: ${esc(cut)}</span>${note}
    <span class="review-bench">The cart picks this cut. Set in config/product_options.json.</span></span>`;
}

// The inline editor for one store: its link, and (basket items only) your
// override. Saving sends the whole form — the override is replaced, and blank
// fields fall back to the benchmark.
function editFormMarkup(d, s) {
  const ov = s.override || {};
  const bench = s.benchmark || {};
  const id = (field) => `review-edit-${esc(s.store)}-${field}`;
  const val = (v) => (isSet(v) ? esc(v) : "");
  const overrideFields = d.in_basket ? `
      <p class="hint">Your values replace the benchmark's for this store. Leave a field blank to keep the benchmark's.</p>
      <label class="field-label" for="${id("name")}">Product name
        <input id="${id("name")}" class="field" name="name" value="${val(ov.name)}" placeholder="${val(bench.name)}" autocomplete="off"></label>
      <div class="review-edit-grid">
        <label class="field-label" for="${id("pack_size")}">Pack size
          <input id="${id("pack_size")}" class="field" name="pack_size" type="number" min="0" step="any" inputmode="decimal"
                 value="${val(ov.pack_size)}" placeholder="${val(bench.pack_size)}"></label>
        <label class="field-label" for="${id("unit")}">Unit
          <select id="${id("unit")}" class="select-native" name="unit">
            <option value="">${bench.unit ? `Benchmark (${esc(bench.unit)})` : "Benchmark"}</option>
            ${OVERRIDE_UNITS.map((u) => `<option value="${u}"${ov.unit === u ? " selected" : ""}>${u}</option>`).join("")}
          </select></label>
        <label class="field-label" for="${id("pack_price")}">Pack price (€)
          <input id="${id("pack_price")}" class="field" name="pack_price" type="number" min="0" step="0.01" inputmode="decimal"
                 value="${val(ov.pack_price)}" placeholder="${val(bench.pack_price)}"></label>
      </div>
      <label class="field-label" for="${id("note")}">Note
        <input id="${id("note")}" class="field" name="note" value="${val(ov.note)}" placeholder="Where you checked, why" autocomplete="off"></label>`
    : `<p class="hint">This item isn't in the benchmark basket, so only its link can be changed here.</p>`;
  return `<form class="review-edit" data-review-form="${esc(s.store)}" novalidate>
      <label class="field-label" for="${id("url")}">Link at ${esc(s.store_name)}
        <input id="${id("url")}" class="field" name="url" type="url" inputmode="url" autocomplete="off" spellcheck="false"
               value="${val(s.url)}" placeholder="https://…"></label>
      ${overrideFields}
      <p class="panel-status error review-edit-status" role="status"></p>
      <div class="review-edit-actions">
        <button type="submit" class="button-tint">Save</button>
        ${s.override ? `<button type="button" class="button-surface" data-review-reset="${esc(s.store)}">Reset to benchmark</button>` : ""}
        <button type="button" class="button-surface" data-review-cancel>Cancel</button>
      </div>
    </form>`;
}

// A link for a registry store the item has nothing for yet (the old links
// editor listed every store; this keeps that reachable).
function addLinkMarkup(d) {
  const have = new Set(d.stores.map((s) => s.store));
  const missing = (local.meta?.stores || []).filter((s) => !have.has(s.key));
  if (!missing.length) return "";
  return `<div class="review-add">
      <label class="field-label" for="review-add-url">Link another store</label>
      <div class="review-add-row">
        <select class="select-native" data-review-add-store aria-label="Store">${missing.map((s) =>
          `<option value="${esc(s.key)}">${esc(s.name)}</option>`).join("")}</select>
        <input id="review-add-url" class="field" type="url" inputmode="url" autocomplete="off" spellcheck="false"
               placeholder="https://…" data-review-add-url>
        <button type="button" class="button-surface" data-review-add>Add link</button>
      </div>
    </div>`;
}

function evidenceMarkup(d, open) {
  const stores = d.stores.filter((s) => s.quality_vs_current || s.evidence || s.notes);
  if (!d.spec && !d.tier && !stores.length) return "";
  return `<details class="review-evidence"${open ? " open" : ""}>
      <summary class="review-evidence-summary"><span>Evidence &amp; quality spec</span><span class="inline-chevron" aria-hidden="true">›</span></summary>
      <div class="review-evidence-body">
        ${d.tier || d.spec ? `<p>${d.tier ? `<strong>Tier ${esc(d.tier)}</strong>${d.spec ? " · " : ""}` : ""}${esc(d.spec || "")}</p>` : ""}
        ${stores.length ? `<ul class="review-evidence-list">${stores.map((s) => `<li>
            <strong>${esc(s.store_name)}</strong>${s.quality_vs_current ? ` <span class="chip chip-neutral">quality: ${esc(s.quality_vs_current)}</span>` : ""}
            ${s.evidence ? `<p>${esc(s.evidence)}</p>` : ""}${s.notes ? `<p class="hint">${esc(s.notes)}</p>` : ""}
          </li>`).join("")}</ul>` : ""}
      </div>
    </details>`;
}

function qtyValues() {
  const d = local.detail;
  return {
    cantidad: local.qtyDraft.cantidad ?? String(d.list.cantidad),
    tenemos: local.qtyDraft.tenemos ?? String(d.list.tenemos),
  };
}

function syncQtySave() {
  const save = document.querySelector(`#${DETAIL_ID} .detail-save-btn`);
  if (!save) return;
  const d = local.detail;
  if (!d) {
    save.disabled = true;
    return;
  }
  const { cantidad, tenemos } = qtyValues();
  save.disabled = cantidad === String(d.list.cantidad) && tenemos === String(d.list.tenemos);
}

function onReviewInput(event) {
  const field = event.target.dataset?.reviewQty;
  if (!field) return;
  local.qtyDraft[field] = event.target.value.trim();
  syncQtySave();
}

async function onReviewClick(event) {
  const target = event.target;
  const dialog = document.getElementById(DETAIL_ID);
  if (target.closest("[data-review-retry]")) {
    local.detailError = "";
    setDialogStatus(dialog, "");
    paintDetail();
    await loadDetail();
    return;
  }
  const checked = target.closest("[data-review-checked]");
  if (checked) {
    await toggleChecked(checked);
    return;
  }
  const suggest = target.closest("[data-review-suggest]");
  if (suggest) {
    const field = suggest.dataset.reviewSuggest;
    const input = dialog.querySelector(`[data-review-qty="${field}"]`);
    input.value = suggest.dataset.value;
    local.qtyDraft[field] = input.value;
    syncQtySave();
    input.focus();
    return;
  }
  const edit = target.closest("[data-review-edit]");
  if (edit) {
    const store = edit.dataset.reviewEdit;
    local.editing = local.editing === store ? null : store;
    paintDetail();
    const focus = local.editing
      ? dialog.querySelector(`[data-review-form="${store}"] input`)
      : dialog.querySelector(`[data-review-edit="${store}"]`);
    focus?.focus();
    return;
  }
  if (target.closest("[data-review-cancel]")) {
    const store = local.editing;
    local.editing = null;
    paintDetail();
    dialog.querySelector(`[data-review-edit="${store}"]`)?.focus();
    return;
  }
  const reset = target.closest("[data-review-reset]");
  if (reset) {
    await resetOverride(reset);
    return;
  }
  if (target.closest("[data-review-add]")) await addStoreLink(dialog);
}

async function toggleChecked(sw) {
  const dialog = document.getElementById(DETAIL_ID);
  const id = local.detailId;
  sw.disabled = true;
  setDialogStatus(dialog, "");
  try {
    const body = await fetchJson(`/api/items/${id}/checked`, jsonInit("PUT", { checked: sw.getAttribute("aria-checked") !== "true" }));
    if (id !== local.detailId) return;
    setSwitch(sw, !!body.checked);
    local.detail.checked = body.checked;
    loadChecks();
    // Checking clears the stock heuristic's flag: re-read the flags, and
    // repaint only the header so an open store form keeps its input.
    local.detail = await fetchJson(`/api/items/${id}/store-detail`).catch(() => local.detail);
    const head = dialog.querySelector(".review-head");
    if (head) head.innerHTML = reviewHeadMarkup(local.detail);
    head?.querySelector("[data-review-checked]")?.focus();
  } catch (error) {
    setDialogStatus(dialog, error.message);
    sw.disabled = false;
  }
}

async function saveQuantities() {
  const dialog = document.getElementById(DETAIL_ID);
  const save = dialog.querySelector(".detail-save-btn");
  const d = local.detail;
  const item = itemById(d?.id);
  if (!d || !item) return;
  const values = qtyValues();
  const parsed = {};
  for (const [field, label] of [["cantidad", "Target"], ["tenemos", "In stock"]]) {
    const n = Number(values[field]);
    if (values[field] === "" || !Number.isInteger(n) || n < 0) {
      setDialogStatus(dialog, `${label} must be a whole number of 0 or more.`);
      dialog.querySelector(`[data-review-qty="${field}"]`)?.focus();
      return;
    }
    parsed[field] = n;
  }
  // PUT /api/items/{id} takes the whole row: the other fields go back unchanged.
  const cols = c();
  const field = (key) => String(item[cols[key]] ?? "");
  save.disabled = true;
  setDialogStatus(dialog, "");
  try {
    state.payload = await fetchJson(`/api/items/${d.id}`, jsonInit("PUT", {
      super: field("super"), lugar: field("lugar"), comida: field("comida"), buscador: field("buscador"), ...parsed,
    }));
    local.qtyDraft = {};
    await loadDetail();
    setDialogStatus(dialog, "Target and in stock saved.", "ok");
    // A target moving to or from 0 moves the item in or out of the what-if.
    afterReviewChange();
  } catch (error) {
    // e.g. 423 — the spreadsheet is open in Excel; the server's hint says so.
    setDialogStatus(dialog, error.message);
    syncQtySave();
  }
}

function overrideFields(ov) {
  return {
    name: ov?.name ?? null, pack_size: ov?.pack_size ?? null, unit: ov?.unit ?? null,
    pack_price: ov?.pack_price ?? null, note: ov?.note ?? null,
  };
}

async function saveStoreEdit(form) {
  const dialog = document.getElementById(DETAIL_ID);
  const d = local.detail;
  const store = form.dataset.reviewForm;
  const entry = d.stores.find((s) => s.store === store);
  const status = form.querySelector(".review-edit-status");
  const read = (name) => (form.elements[name]?.value ?? "").trim();
  const number = (name) => (read(name) === "" ? null : Number(read(name)));
  const url = read("url");
  const fields = d.in_basket ? {
    name: read("name") || null, pack_size: number("pack_size"), unit: read("unit") || null,
    pack_price: number("pack_price"), note: read("note") || null,
  } : null;
  const urlChanged = url !== (entry.url || "");
  const ovChanged = !!fields && JSON.stringify(fields) !== JSON.stringify(overrideFields(entry.override));
  if (!urlChanged && !ovChanged) {
    local.editing = null;
    paintDetail();
    return;
  }
  const buttons = [...form.querySelectorAll("button")];
  buttons.forEach((b) => { b.disabled = true; });
  status.textContent = "";
  let saved = false;
  try {
    if (urlChanged) {
      state.payload = await fetchJson(`/api/items/${d.id}/store-url`, jsonInit("PUT", { store, url }));
      saved = true;
    }
    let detail = null;
    if (ovChanged && Object.values(fields).some((v) => v !== null)) {
      detail = await fetchJson(`/api/items/${d.id}/store-override`, jsonInit("PUT", { store, ...fields }));
    } else if (ovChanged && entry.override) {
      detail = await fetchJson(`/api/items/${d.id}/store-override?store=${encodeURIComponent(store)}`, { method: "DELETE" });
    }
    saved = true;
    local.detail = detail || await fetchJson(`/api/items/${d.id}/store-detail`);
    local.editing = null;
    paintDetail();
    setDialogStatus(dialog, `${entry.store_name} saved.`, "ok");
    dialog.querySelector(`[data-review-edit="${store}"]`)?.focus();
  } catch (error) {
    status.textContent = error.message;
    buttons.forEach((b) => { b.disabled = false; });
  }
  if (saved) afterReviewChange();
}

async function resetOverride(button) {
  const dialog = document.getElementById(DETAIL_ID);
  const store = button.dataset.reviewReset;
  const status = button.closest("form")?.querySelector(".review-edit-status");
  button.disabled = true;
  try {
    local.detail = await fetchJson(`/api/items/${local.detailId}/store-override?store=${encodeURIComponent(store)}`, { method: "DELETE" });
    local.editing = null;
    paintDetail();
    setDialogStatus(dialog, `${storeName(store)} is back to the benchmark's values.`, "ok");
    dialog.querySelector(`[data-review-edit="${store}"]`)?.focus();
    afterReviewChange();
  } catch (error) {
    if (status) status.textContent = error.message;
    button.disabled = false;
  }
}

async function addStoreLink(dialog) {
  const store = dialog.querySelector("[data-review-add-store]").value;
  const input = dialog.querySelector("[data-review-add-url]");
  const url = input.value.trim();
  if (!url) {
    setDialogStatus(dialog, "Paste the product link first.");
    input.focus();
    return;
  }
  setDialogStatus(dialog, "");
  try {
    state.payload = await fetchJson(`/api/items/${local.detailId}/store-url`, jsonInit("PUT", { store, url }));
    await loadDetail();
    setDialogStatus(dialog, `${storeName(store)} link added.`, "ok");
    afterReviewChange();
  } catch (error) {
    setDialogStatus(dialog, error.message);
  }
}
