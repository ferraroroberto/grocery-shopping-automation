// Items → Stores (issue #148): every item's per-store product links, a what-if
// store picker, and the monthly cost simulator priced from the latest
// supermarket-benchmark run. Picks are a local what-if — nothing is written to
// the spreadsheet until the Review & apply dialog posts them.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { fetchJson } from "./api.js";
import { activePaneBody, c, filteredItems, render, state } from "./core.js";

const PICKS_KEY = "grocery.storePicks";
const FREQ_KEY = "grocery.storeFrequency";
const SIM_DEBOUNCE_MS = 350;
const FREQ_LABELS = { weekly: "Weekly", "2-weekly": "2-weekly", monthly: "Monthly" };
const FLAG_TEXT = {
  unit_mismatch: "Pack units differ, so the target was kept. Check it.",
  pack_unknown: "Pack size unknown, so the target was kept. Check it.",
};
const money = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" });

// Module-local view state. `picks` (item id → store) and the frequency are the
// what-if; localStorage only remembers them per viewer, best-effort.
const local = {
  meta: null,         // GET /api/stores
  metaState: "idle",  // idle | loading | ready | error
  sim: null,          // last POST /api/stores/simulate response
  simState: "loading", // loading | ready | empty | stale | error
  simUpdated: null,
  picks: readStored(PICKS_KEY, {}),
  frequency: readStored(FREQ_KEY, ""),
  note: null,         // { kind: "ok" | "error", text, warning? } — header feedback
  actionNote: null,   // { kind, text } — actions card feedback
  timer: 0,
  seq: 0,
  linksItemId: null,  // item whose links the editor dialog is showing
  preview: [],        // apply-preview changes shown in the apply dialog
};

// ------------------------------------------------------------ persistence
function readStored(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (_) {
    return fallback;
  }
}

function writeStored(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {
    // private mode / blocked storage — the what-if still works for this visit
  }
}

// --------------------------------------------------------------- helpers
function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function icon(name, cls = "icon") {
  return `<svg class="${cls}" aria-hidden="true" focusable="false"><use href="#i-${name}"></use></svg>`;
}

function eur(value) {
  return value === null || value === undefined ? "–" : money.format(value);
}

function storeName(key) {
  return local.meta?.stores.find((s) => s.key === key)?.name || key || "?";
}

function hasHandler(key) {
  return !!local.meta?.stores.find((s) => s.key === key)?.has_handler;
}

function itemById(id) {
  return (state.payload?.items || []).find((item) => item.id === id);
}

function currentStore(item) {
  return String(item?.[c().super] ?? "").trim().toLowerCase();
}

function pickFor(item) {
  return local.picks[item.id] || currentStore(item);
}

// Only picks that change something — stale entries (the item was deleted or
// already moved to that store) are dropped as a side effect.
function activePicks() {
  const out = {};
  for (const [id, store] of Object.entries(local.picks)) {
    const item = itemById(Number(id));
    if (item && store && store !== currentStore(item)) out[id] = store;
  }
  local.picks = out;
  return out;
}

function frequency() {
  const options = local.meta?.frequencies || {};
  return local.frequency && options[local.frequency] ? local.frequency : (local.meta?.default_frequency || "weekly");
}

function setPick(id, store) {
  const item = itemById(id);
  if (!item) return;
  if (!store || store === currentStore(item)) delete local.picks[id];
  else local.picks[id] = store;
  writeStored(PICKS_KEY, local.picks);
}

// -------------------------------------------------------------- loading
async function loadMeta() {
  local.metaState = "loading";
  try {
    local.meta = await fetchJson("/api/stores");
    local.metaState = "ready";
  } catch (_) {
    local.metaState = "error";
  }
  if (state.mode === "stores") render();
}

export function scheduleSimulate(delay = SIM_DEBOUNCE_MS) {
  window.clearTimeout(local.timer);
  local.timer = window.setTimeout(runSimulate, delay);
}

async function runSimulate() {
  if (!local.meta?.run_date) {
    local.simState = "empty";
    paintSim();
    return;
  }
  const seq = ++local.seq;
  document.querySelector("#stores-sim")?.setAttribute("aria-busy", "true");
  try {
    const body = await fetchJson("/api/stores/simulate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ picks: activePicks(), frequency: frequency() }),
    });
    if (seq !== local.seq) return;
    local.sim = body;
    local.simState = "ready";
    local.simUpdated = new Date();
  } catch (_) {
    if (seq !== local.seq) return;
    // A failed refresh after a good read keeps the last numbers, labelled.
    local.simState = local.sim ? "stale" : "error";
  }
  if (state.mode !== "stores") return;
  paintSim();
  paintUnpriced();
  paintList();
}

// ------------------------------------------------------------- rendering
export function renderStores() {
  const body = activePaneBody();
  if (local.metaState === "idle") loadMeta();
  if (!local.meta) {
    const failed = local.metaState === "error";
    const block = emptyStateEl(failed ? "circle-alert" : "refresh-cw",
      failed ? "Store data unavailable." : "Reading store links…",
      failed ? { actionLabel: "Retry", onAction: () => { local.metaState = "idle"; render(); } } : undefined);
    block.dataset.state = failed ? "error" : "loading";
    body.replaceChildren(block);
    return;
  }
  body.innerHTML = `
    <section class="card stores-head">${headMarkup()}</section>
    <section id="stores-sim" class="card stores-sim"></section>
    <div id="stores-unpriced"></div>
    <section class="card stores-actions">${actionsMarkup()}</section>
    <section class="card stores-list-card">
      <div class="card-head">
        <h2 class="card-title">${icon("package")}Items</h2>
        <span class="card-head-meta" id="stores-list-count"></span>
      </div>
      <ul id="stores-list" class="store-rows"></ul>
    </section>`;
  paintSim();
  paintUnpriced();
  paintList();
  if (!local.sim && local.simState !== "empty") scheduleSimulate(0);
}

function headMarkup() {
  const run = local.meta.run_date;
  const note = local.note;
  return `<div class="card-head"><h2 class="card-title">${icon("shopping-basket")}Store prices</h2></div>
    <p class="hint">${run ? `Prices from benchmark run ${esc(run)}` : "No benchmark run yet. Run the supermarket benchmark, then import it here."}</p>
    <button type="button" class="secondary btn-block" data-stores-action="import">${icon("download")}Import latest run</button>
    ${note ? `<div class="panel-status ${note.kind}" role="status">${esc(note.text)}</div>` : ""}
    ${note?.warning ? `<div class="panel-status warn">${icon("circle-alert")} ${esc(note.warning)}</div>` : ""}`;
}

function actionsMarkup() {
  const count = Object.keys(activePicks()).length;
  const noRun = !local.meta.run_date;
  const note = local.actionNote;
  return `<div class="stores-buttons">
      <button type="button" class="big-btn" data-stores-action="recommended"${noRun ? " disabled" : ""}>Load recommended plan</button>
      <button type="button" class="secondary" data-stores-action="reset"${count ? "" : " disabled"}>Reset to today</button>
    </div>
    <button type="button" class="primary btn-block" data-stores-action="review"${count ? "" : " disabled"}>Review &amp; apply… (${count})</button>
    ${note ? `<div id="stores-action-status" class="panel-status ${note.kind}" role="status">${esc(note.text)}</div>` : ""}`;
}

function paintActions() {
  const card = document.querySelector(".stores-actions");
  if (card) card.innerHTML = actionsMarkup();
}

function freqMarkup() {
  const current = frequency();
  return `<div class="stores-freq" role="radiogroup" aria-label="Ordering frequency">${
    Object.keys(local.meta.frequencies).map((key) =>
      `<button type="button" class="pill${key === current ? " active" : ""}" role="radio" aria-checked="${key === current}" data-stores-freq="${esc(key)}">${esc(FREQ_LABELS[key] || key)}</button>`,
    ).join("")}</div>`;
}

function paintSim() {
  const card = document.querySelector("#stores-sim");
  if (!card) return;
  card.removeAttribute("aria-busy");
  card.dataset.state = local.simState;
  const head = `<div class="card-head"><h2 class="card-title">${icon("shopping-cart")}Monthly cost</h2></div>${freqMarkup()}`;
  if (local.simState === "empty") {
    card.innerHTML = head + emptyStateEl("shopping-basket", "No benchmark prices to simulate yet.").outerHTML;
    return;
  }
  if (!local.sim) {
    const failed = local.simState === "error";
    const block = emptyStateEl(failed ? "circle-alert" : "refresh-cw", failed ? "Simulation unavailable." : "Pricing your picks…",
      failed ? { actionLabel: "Retry" } : undefined);
    card.innerHTML = head + block.outerHTML;
    card.querySelector(".empty-state-action")?.setAttribute("data-stores-action", "retry-sim");
    return;
  }
  const { picks: whatIf, today, delta } = local.sim;
  const saves = delta.total < 0;
  const deltaText = delta.total === 0 ? "Same as today" : `${saves ? "Saves" : "Costs"} ${eur(Math.abs(delta.total))} a month vs today`;
  const rows = [
    ["Goods", "goods"], ["Delivery", "delivery"], ["Total", "total"], ["Total, fee-optimised", "total_optimised"],
  ].map(([label, key]) => `<tr><th scope="row">${label}</th><td>${eur(whatIf[key])}</td><td>${eur(today[key])}</td></tr>`).join("");
  const perStore = Object.entries(whatIf.per_store).map(([key, s]) => `<li>
      <span class="stores-store-name">${esc(storeName(key))}</span>
      ${hasHandler(key) ? "" : `<span class="chip chip-neutral">manual order</span>`}
      <span class="stores-store-figures">${eur(s.goods)} goods · ${s.orders} orders · ${eur(s.monthly_fee)} fees</span>
    </li>`).join("");
  const stale = local.simState === "stale"
    ? `<p class="panel-status warn">Last updated ${local.simUpdated?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) || ""} · simulation unavailable</p>` : "";
  const notComparable = local.sim.comparable ? "" :
    `<p class="hint">Some items have no price on one side, so the two totals leave out different items.</p>`;
  card.innerHTML = `${head}
    <div class="stores-total">
      <div><span class="meta">What-if total</span><strong>${eur(whatIf.total)}</strong></div>
      <span class="stores-delta${saves ? " ok" : ""}" role="status">${deltaText}</span>
    </div>
    <table class="stores-table">
      <thead><tr><th scope="col"><span class="meta">Per month</span></th><th scope="col">What-if</th><th scope="col">Today</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <ul class="stores-per-store">${perStore}</ul>
    ${notComparable}${stale}`;
}

function paintUnpriced() {
  const host = document.querySelector("#stores-unpriced");
  if (!host) return;
  const unpriced = local.sim?.picks.unpriced || [];
  if (!unpriced.length) {
    host.replaceChildren();
    return;
  }
  const open = !!host.querySelector("details[open]");
  host.innerHTML = `<details class="card card--collapsible"${open ? " open" : ""}>
    <summary class="collapse-summary">
      <span class="collapse-main">${icon("circle-alert")}
        <h3 class="collapse-title">No price</h3>
        <span class="collapse-count">${unpriced.length} item${unpriced.length === 1 ? "" : "s"} left out of the totals</span>
      </span>
      <span class="collapse-chevron" aria-hidden="true">›</span>
    </summary>
    <div class="collapse-body"><ul class="stores-unpriced-list">${unpriced.map((u) =>
      `<li><span>${esc(u.comida)}</span><span class="meta">${esc(storeName(u.store))}</span></li>`).join("")}</ul></div>
  </details>`;
}

function rowMarkup(item) {
  const cols = c();
  const urls = item.urls || {};
  const today = currentStore(item);
  const pick = pickFor(item);
  const prices = local.sim?.item_prices?.[String(item.id)] || {};
  const name = item[cols.comida] ?? "";
  const options = [...new Set([today, ...Object.keys(urls)].filter(Boolean))];
  const meta = !options.length || !Object.keys(urls).length
    ? `${today ? `${esc(storeName(today))} · ` : ""}No store links yet`
    : pick !== today ? `What-if · today ${esc(storeName(today))}` : `Bought at ${esc(storeName(today))}`;
  const select = options.length > 1
    ? `<select class="field store-pick" data-stores-pick aria-label="Buy ${esc(name)} at">${options.map((key) =>
        `<option value="${esc(key)}"${key === pick ? " selected" : ""}>${esc(storeName(key))}${key === today ? " (today)" : ""}</option>`).join("")}</select>`
    : "";
  // The picked store leads, then the cheapest: with up to nine links on one
  // swipeable line, what matters must not start off-screen.
  const rank = ([key]) => (key === pick ? -1 : prices[key] ?? Number.MAX_VALUE);
  const chips = Object.entries(urls).sort((a, b) => rank(a) - rank(b)).map(([key, url]) => {
    const price = prices[key];
    const picked = key === pick;
    const label = `Open ${name} at ${storeName(key)}${price !== undefined ? `, ${eur(price)} a month` : ""}`;
    return `<a class="store-chip${picked ? " is-picked" : ""}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(label)}">${picked ? icon("check") : ""}<span>${esc(storeName(key))}</span>${price !== undefined ? `<span class="store-chip-price">${eur(price)}</span>` : ""}</a>`;
  }).join("");
  return `<li class="store-row" data-item-id="${item.id}">
    <div class="store-row-head">
      <div class="store-row-text"><span class="store-row-title">${esc(name)}</span><span class="store-row-meta">${meta}</span></div>
      ${select}
      <button type="button" class="icon-btn" data-stores-action="edit-links" aria-label="Edit store links for ${esc(name)}">${icon("pencil")}</button>
    </div>
    ${chips ? `<div class="store-chips">${chips}</div>` : ""}
  </li>`;
}

function paintList() {
  const list = document.querySelector("#stores-list");
  if (!list) return;
  const cols = c();
  const source = filteredItems().slice().sort((a, b) => String(a[cols.comida] ?? "").localeCompare(String(b[cols.comida] ?? "")));
  const linked = source.filter((item) => Object.keys(item.urls || {}).length).length;
  document.querySelector("#stores-list-count").textContent = `${source.length} items · ${linked} with links`;
  list.innerHTML = source.map(rowMarkup).join("") || `<li>${emptyStateEl("search", "No matching items.").outerHTML}</li>`;
}

function repaintRow(id) {
  const row = document.querySelector(`.store-row[data-item-id="${id}"]`);
  const item = itemById(id);
  if (!row || !item) return;
  const hadFocus = row.contains(document.activeElement);
  row.outerHTML = rowMarkup(item);
  // The picker was just used — keep keyboard focus on it across the repaint.
  if (hadFocus) document.querySelector(`.store-row[data-item-id="${id}"] [data-stores-pick]`)?.focus();
}

// --------------------------------------------------------------- actions
async function importLatest(button) {
  button.disabled = true;
  try {
    const body = await fetchJson("/api/stores/import-latest", { method: "POST" });
    const { import: counts, ...payload } = body;
    state.payload = payload;
    const suspectCounts = Object.entries(counts.suspect_urls || {});
    const suspectTotal = suspectCounts.reduce((n, [, count]) => n + count, 0);
    const suspect = suspectTotal
      ? `${suspectCounts.map(([store, n]) => `${n} ${storeName(store)}`).join(", ")} link${suspectTotal === 1 ? " lacks" : "s lack"} a product id and may open the store's home page.`
      : "";
    local.note = {
      kind: "ok",
      text: `Imported ${counts.seeded} link${counts.seeded === 1 ? "" : "s"} · ${counts.skipped_existing} already set`,
      warning: suspect,
    };
    local.meta = null;
    local.metaState = "idle";
    local.sim = null;
    local.simState = "loading";  // a first import may bring the first run
  } catch (error) {
    local.note = { kind: "error", text: error.message };
  }
  render();
}

async function loadRecommended() {
  try {
    const body = await fetchJson("/api/stores/recommended");
    local.picks = {};
    for (const [id, store] of Object.entries(body.picks)) setPick(Number(id), store);
    writeStored(PICKS_KEY, local.picks);
    local.actionNote = { kind: "ok", text: `Loaded the recommended plan: ${body.stores.map(storeName).join(" + ")}` };
  } catch (error) {
    local.actionNote = { kind: "error", text: error.message };
  }
  render();
  scheduleSimulate(0);
}

function resetPicks() {
  local.picks = {};
  writeStored(PICKS_KEY, local.picks);
  local.actionNote = { kind: "ok", text: "Back to today's stores" };
  render();
  scheduleSimulate(0);
}

// ------------------------------------------------------------- dialogs
// Both editors are native <dialog>s on the vendored modal shell, built once
// and kept at <body> level so a pane re-render never tears them down.
function dialogShell(id, title, saveLabel) {
  let dialog = document.getElementById(id);
  if (dialog) return dialog;
  dialog = document.createElement("dialog");
  dialog.id = id;
  dialog.className = "detail-dialog stores-dialog";
  dialog.setAttribute("aria-labelledby", `${id}-title`);
  dialog.innerHTML = `<div class="detail-card">
      <div class="detail-header">
        <h2 id="${id}-title">${esc(title)}</h2>
        <button type="button" class="detail-close" aria-label="Close" data-dialog-close>${icon("x")}</button>
      </div>
      <div class="stores-dialog-body"></div>
      <div class="panel-status error stores-dialog-status" role="status"></div>
      <div class="detail-actions"><button type="button" class="detail-save-btn" disabled>${esc(saveLabel)}</button></div>
    </div>`;
  dialog.querySelector("[data-dialog-close]").addEventListener("click", () => dialog.close());
  document.body.appendChild(dialog);
  return dialog;
}

function openLinksEditor(id) {
  const item = itemById(id);
  if (!item) return;
  local.linksItemId = id;
  const dialog = dialogShell("stores-links-dialog", "Store links", "Save links");
  const save = dialog.querySelector(".detail-save-btn");
  const urls = item.urls || {};
  dialog.querySelector(".stores-dialog-body").innerHTML = `<p class="hint">${esc(item[c().comida])}</p>${
    local.meta.stores.map((s) => `<label class="row"><span>${esc(s.name)}</span>
      <input class="input-native" type="url" inputmode="url" autocomplete="off" spellcheck="false"
             data-store-key="${esc(s.key)}" data-original="${esc(urls[s.key] || "")}" value="${esc(urls[s.key] || "")}"
             placeholder="https://…"></label>`).join("")}`;
  dialog.querySelector(".stores-dialog-status").textContent = "";
  save.disabled = true;
  save.onclick = () => saveLinks(dialog);
  dialog.querySelector(".stores-dialog-body").oninput = () => {
    save.disabled = !changedLinks(dialog).length;
  };
  dialog.showModal();
}

function changedLinks(dialog) {
  return [...dialog.querySelectorAll("input[data-store-key]")]
    .filter((input) => input.value.trim() !== input.dataset.original)
    .map((input) => ({ store: input.dataset.storeKey, url: input.value.trim() }));
}

async function saveLinks(dialog) {
  const save = dialog.querySelector(".detail-save-btn");
  const status = dialog.querySelector(".stores-dialog-status");
  save.disabled = true;
  status.textContent = "";
  try {
    for (const change of changedLinks(dialog)) {
      state.payload = await fetchJson(`/api/items/${local.linksItemId}/store-url`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(change),
      });
    }
    dialog.close();
    render();
    scheduleSimulate(0);
  } catch (error) {
    status.textContent = error.message;
    save.disabled = false;
  }
}

async function openApplyReview() {
  const dialog = dialogShell("stores-apply-dialog", "Review changes", "Apply");
  const bodyEl = dialog.querySelector(".stores-dialog-body");
  const status = dialog.querySelector(".stores-dialog-status");
  const save = dialog.querySelector(".detail-save-btn");
  status.textContent = "";
  save.disabled = true;
  save.textContent = "Apply";
  bodyEl.replaceChildren(emptyStateEl("refresh-cw", "Working out the changes…"));
  dialog.showModal();
  try {
    const body = await fetchJson("/api/stores/apply-preview", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ picks: activePicks() }),
    });
    local.preview = body.changes;
  } catch (error) {
    bodyEl.replaceChildren(emptyStateEl("circle-alert", "Couldn't prepare the changes."));
    status.textContent = error.message;
    return;
  }
  if (!local.preview.length) {
    bodyEl.replaceChildren(emptyStateEl("circle-check", "No store changes to apply."));
    return;
  }
  bodyEl.innerHTML = `<p class="hint">Targets are converted by pack size. Adjust any before applying.</p>
    <ul class="apply-rows">${local.preview.map(applyRowMarkup).join("")}</ul>`;
  const ready = local.preview.filter((ch) => !ch.flags.includes("no_url")).length;
  save.textContent = `Apply ${ready} change${ready === 1 ? "" : "s"}`;
  save.disabled = ready === 0;
  save.onclick = () => submitApply(dialog);
}

function packText(pack) {
  return pack ? `${Number(pack.size).toLocaleString("en-IE", { maximumFractionDigits: 3 })} ${esc(pack.unit)}` : "unknown";
}

function applyRowMarkup(change) {
  const blocked = change.flags.includes("no_url");
  const warnings = change.flags.filter((f) => FLAG_TEXT[f]);
  const flagged = warnings.length > 0;
  const flagLines = blocked
    ? `<p class="apply-flag">${icon("circle-alert")}No ${esc(storeName(change.to))} link for this item yet. Add one with its edit button first; it is skipped.</p>`
    : warnings.map((f) => `<p class="apply-flag">${icon("circle-alert")}${FLAG_TEXT[f]}</p>`).join("");
  return `<li class="apply-row${blocked ? " is-blocked" : ""}${flagged ? " is-flagged" : ""}" data-apply-id="${change.id}">
    <span class="apply-row-title">${esc(change.comida)}</span>
    <span class="apply-row-move">${esc(storeName(change.from))}${icon("arrow-right")}${esc(storeName(change.to))}</span>
    <span class="apply-row-move">Pack ${packText(change.old_pack)}${icon("arrow-right")}${packText(change.new_pack)}</span>
    <label class="row"><span>Target <span class="meta">was ${change.old_cantidad}</span></span>
      <input class="input-native apply-qty" type="number" min="0" step="1" inputmode="numeric"
             value="${change.cantidad}" aria-label="New target for ${esc(change.comida)}"${blocked ? " disabled" : ""}></label>
    ${flagLines}
  </li>`;
}

async function submitApply(dialog) {
  const save = dialog.querySelector(".detail-save-btn");
  const status = dialog.querySelector(".stores-dialog-status");
  const changes = [];
  for (const change of local.preview) {
    if (change.flags.includes("no_url")) continue;
    const input = dialog.querySelector(`[data-apply-id="${change.id}"] .apply-qty`);
    const qty = Number(input.value);
    if (!Number.isInteger(qty) || qty < 0) {
      status.textContent = `Target for ${change.comida} must be a whole number of 0 or more.`;
      input.focus();
      return;
    }
    changes.push({ id: change.id, store: change.to, cantidad: qty });
  }
  save.disabled = true;
  status.textContent = "";
  try {
    const { applied, ...payload } = await fetchJson("/api/stores/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ changes }),
    });
    state.payload = payload;
    for (const change of changes) delete local.picks[change.id];
    writeStored(PICKS_KEY, local.picks);
    local.actionNote = { kind: "ok", text: `Applied ${applied} change${applied === 1 ? "" : "s"}` };
    dialog.close();
    render();
    scheduleSimulate(0);
  } catch (error) {
    // e.g. 423 — the spreadsheet is open in Excel; the server's hint says so.
    status.textContent = error.message;
    save.disabled = false;
  }
}

// ------------------------------------------------------ event delegation
// Wired from app.js on .app; only acts on this view's own data-* hooks.
export async function onStoresClick(event) {
  if (state.mode !== "stores") return;
  const freq = event.target.closest("[data-stores-freq]");
  if (freq) {
    local.frequency = freq.dataset.storesFreq;
    writeStored(FREQ_KEY, local.frequency);
    document.querySelectorAll("[data-stores-freq]").forEach((b) => {
      const on = b === freq;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", String(on));
    });
    scheduleSimulate();
    return;
  }
  const button = event.target.closest("[data-stores-action]");
  if (!button) return;
  const action = button.dataset.storesAction;
  if (action === "import") await importLatest(button);
  if (action === "recommended") await loadRecommended();
  if (action === "reset") resetPicks();
  if (action === "review") await openApplyReview();
  if (action === "retry-sim") scheduleSimulate(0);
  if (action === "edit-links") openLinksEditor(Number(button.closest("[data-item-id]").dataset.itemId));
}

export function onStoresChange(event) {
  if (state.mode !== "stores" || !event.target.matches("[data-stores-pick]")) return;
  const id = Number(event.target.closest("[data-item-id]").dataset.itemId);
  setPick(id, event.target.value);
  repaintRow(id);
  local.actionNote = null;
  paintActions();
  scheduleSimulate();
}
