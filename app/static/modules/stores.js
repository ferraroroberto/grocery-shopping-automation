// Items → Stores (issue #148): every item's per-store product links, a what-if
// store picker, and the monthly cost simulator priced from the latest
// supermarket-benchmark run. Picks are a local what-if — nothing is written to
// the spreadsheet until the Review & apply dialog posts them.
//
// The per-item review (#165): the row's pencil opens the item-detail dialog —
// the quantity check in real units, every store's product / pack / price with
// your overrides beside the benchmark's values, and the server-side Checked
// mark — and the list filters to the items that still need checking.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { setSwitch } from "../_vendored/switch/switch.js";
import { fetchJson } from "./api.js";
import { activePaneBody, c, filteredItems, render, state } from "./core.js";
import { switchMarkup } from "./dom.js";

const PICKS_KEY = "grocery.storePicks";
const FREQ_KEY = "grocery.storeFrequency";
const SHOW_ALL_KEY = "grocery.storesShowAll";
const FILTER_KEY = "grocery.storesFilter";
const STORE_FILTER_KEY = "grocery.storesStoreFilter";
const SIM_DEBOUNCE_MS = 350;
const DETAIL_ID = "stores-detail-dialog";
const FREQ_LABELS = { weekly: "Weekly", "2-weekly": "2-weekly", monthly: "Monthly" };
const FILTERS = [["all", "All"], ["needs", "Needs checking"], ["checked", "Checked"]];
const FLAG_TEXT = {
  unit_mismatch: "Pack units differ, so the target and stock were kept. Check them.",
  pack_unknown: "Pack size unknown, so the target and stock were kept. Check them.",
};
// Review flags (src/store_links.CHECK_FLAGS): badge wording + the longer reason.
const FLAG_LABELS = {
  moved: ["Moved store", "Your list buys it at a different store than the benchmarked one"],
  pack_x2: ["Pack ×2+", "The new pack is at least twice, or at most half, the old one"],
  unit_mismatch: ["Unit mismatch", "The old and new packs are in different units"],
  search_link: ["Search link", "The list's link opens a search page, not the product"],
  suspect_link: ["Suspect link", "The list's link may not open the product page"],
  override: ["Your override", "You saved your own values for a store"],
  benchmark_changed: ["Benchmark changed", "A newer run changed the values your override was made against"],
  stock_unconverted: ["Stock not converted", "Your stock may still be counted in the old packs"],
};
const STATUS_TEXT = {
  baseline: "Current product",
  equivalent: "Equivalent",
  upgrade: "Upgrade",
  unverified: "Unverified",
  not_found: "Not found",
  override: "Your values",
};
const OVERRIDE_UNITS = ["kg", "l", "ud", "m"];
const REFRESH_STEPS = [
  "In Claude Code, in the grocery-shopping-automation repo, run /supermarket-benchmark (about an hour; it asks you to confirm the quality specs).",
  "Come back to Items → Stores and tap \"Import latest run\".",
  "Review the \"Needs checking\" items.",
];
const money = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" });
const decimal = new Intl.NumberFormat("en-IE", { maximumFractionDigits: 3 });

// Module-local view state. `picks` (item id → store) and the frequency are the
// what-if; localStorage only remembers them per viewer, best-effort.
const local = {
  meta: null,         // GET /api/stores
  metaState: "idle",  // idle | loading | ready | error
  checks: null,       // GET /api/stores/checks — review flags + checked dates
  sim: null,          // last POST /api/stores/simulate response
  simState: "loading", // loading | ready | empty | stale | error
  simUpdated: null,
  picks: readStored(PICKS_KEY, {}),
  frequency: readStored(FREQ_KEY, ""),
  showAll: readStored(SHOW_ALL_KEY, false), // chips/picker for every benchmarked store
  filter: storedFilter(),                   // all | needs | checked
  stores: storedStores(),                   // list stores the rows are narrowed to; none = every store
  note: null,         // { kind: "ok" | "error", text, warning? } — header feedback
  actionNote: null,   // { kind, text } — actions card feedback
  timer: 0,
  seq: 0,
  preview: [],        // apply-preview changes shown in the apply dialog
  detailId: null,     // item the detail dialog is showing
  detail: null,       // GET /api/items/{id}/store-detail
  detailError: "",
  editing: null,      // store whose edit form is open in the detail dialog
  qtyDraft: {},       // unsaved target / stock typed in the detail dialog
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

function storedFilter() {
  const value = readStored(FILTER_KEY, "all");
  return FILTERS.some(([key]) => key === value) ? value : "all";
}

function storedStores() {
  const value = readStored(STORE_FILTER_KEY, []);
  return Array.isArray(value) ? value.filter((key) => typeof key === "string") : [];
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

function num(value) {
  return decimal.format(value);
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
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

function jsonInit(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}

// -------------------------------------------------------------- loading
async function loadMeta() {
  local.metaState = "loading";
  try {
    local.meta = await fetchJson("/api/stores");
    local.metaState = "ready";
    loadChecks();
  } catch (_) {
    local.metaState = "error";
  }
  if (state.mode === "stores") render();
}

// Review flags and checked dates for every row. A failed read keeps the last
// good one; the filter pills then show no counts and the list stays unfiltered.
async function loadChecks() {
  try {
    local.checks = await fetchJson("/api/stores/checks");
  } catch (_) {
    // keep what we had — the list still works without the review state
  }
  if (state.mode !== "stores") return;
  paintFilter();
  paintList();
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
    const body = await fetchJson("/api/stores/simulate", jsonInit("POST", { picks: activePicks(), frequency: frequency() }));
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
  const howtoOpen = !!body.querySelector(".stores-howto[open]");
  body.innerHTML = `
    <section class="card stores-head">${headMarkup(howtoOpen)}</section>
    <section id="stores-sim" class="card stores-sim"></section>
    <div id="stores-unpriced"></div>
    <section class="card stores-actions">${actionsMarkup()}</section>
    <section class="card stores-list-card">
      <div class="card-head">
        <h2 class="card-title">${icon("package")}Items</h2>
        <span class="card-head-meta" id="stores-list-count"></span>
      </div>
      <div id="stores-filter"></div>
      <div class="flag-row stores-showall">${switchMarkup(local.showAll, "Show all stores", { "data-stores-showall": "" })}<span>Show all stores, not only the ones in your list</span></div>
      <ul id="stores-list" class="store-rows"></ul>
    </section>`;
  paintSim();
  paintUnpriced();
  paintFilter();
  paintList();
  if (!local.sim && local.simState !== "empty") scheduleSimulate(0);
}

function ageText(days) {
  if (days === null || days === undefined) return "";
  return days <= 0 ? "from today" : `${plural(days, "day")} old`;
}

// Benchmark status (#165): how old the prices are, when the next review is
// due, and the steps to refresh them — the benchmark itself runs in Claude
// Code (it needs your quality-spec confirmation), never from here.
function headMarkup(howtoOpen = false) {
  const m = local.meta;
  const note = local.note;
  const today = new Date().toISOString().slice(0, 10);
  const due = m.next_due && m.next_due <= today;
  const covered = m.stores_covered || [];
  const status = m.run_date
    ? `<p class="stores-status">Run ${esc(m.run_date)}, ${ageText(m.age_days)} ·
        <span class="${due ? "stores-due" : ""}">${due ? `${icon("circle-alert")}Review due since` : "Next review"} ${esc(m.next_due)}</span> ·
        <span title="${esc(covered.map(storeName).join(", "))}">${plural(covered.length, "store")} covered</span> ·
        ${plural(m.overrides || 0, "override")} of yours</p>`
    : `<p class="hint">No benchmark run yet. Run the supermarket benchmark, then import it here.</p>`;
  return `<div class="card-head"><h2 class="card-title">${icon("shopping-basket")}Store prices</h2></div>
    ${status}
    <details class="stores-howto"${howtoOpen ? " open" : ""}>
      <summary class="stores-howto-summary">${icon("refresh-cw")}<span>How to refresh the prices</span><span class="inline-chevron" aria-hidden="true">›</span></summary>
      <ol class="stores-howto-steps">
        <li>In Claude Code, in this repo, run <code>/supermarket-benchmark</code>. It takes about an hour and asks you to confirm the quality specs.</li>
        <li>Come back here and tap <strong>Import latest run</strong>.</li>
        <li>Review the <strong>Needs checking</strong> items below.</li>
      </ol>
      <button type="button" class="secondary btn-block" data-stores-action="copy-steps">${icon("copy")}Copy steps</button>
      <p id="stores-copy-status" class="panel-status" role="status"></p>
    </details>
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
      <button type="button" class="secondary" data-stores-action="reset"${count ? "" : " disabled"}>Reset to my list</button>
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
    <p class="hint">Today = your stores when prices were benchmarked (${esc(local.sim.run_date || "")}).</p>
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

// Stores the list buys from today; the rest are only shown on request so a
// row carries its three real options, not nine benchmark ones.
function storesInUse() {
  return new Set((state.payload?.items || []).map(currentStore).filter(Boolean));
}

// The Store filter's selection, minus any store the list no longer buys from
// (an Apply moved its last item away) — a stale pick must not hide every row.
function selectedStores(inUse = storesInUse()) {
  if (!state.payload) return local.stores;
  const kept = local.stores.filter((key) => inUse.has(key));
  if (kept.length !== local.stores.length) {
    local.stores = kept;
    writeStored(STORE_FILTER_KEY, kept);
  }
  return kept;
}

const LINK_NOTE = {
  search: "opens a search page, not the product",
  suspect: "link may not open the product page",
};

// "checked" | "needs" (flagged, not checked) | "" — from the last checks read.
function reviewState(item) {
  const id = String(item.id);
  if (local.checks?.checked?.[id]) return "checked";
  return local.checks?.checks?.[id] ? "needs" : "";
}

function flagLabel(flag) {
  return FLAG_LABELS[flag]?.[0] || flag;
}

function reviewBadge(item) {
  const review = reviewState(item);
  if (review === "checked") return `<span class="review-badge is-checked">${icon("check")}Checked</span>`;
  const flags = review ? local.checks.checks[String(item.id)] : [];
  if (!flags.length) return "";
  return `<span class="review-badge" title="${esc(flags.map(flagLabel).join(", "))}">${esc(flagLabel(flags[0]))}${flags.length > 1 ? ` +${flags.length - 1}` : ""}</span>`;
}

function rowMarkup(item, inUse = storesInUse()) {
  const cols = c();
  const today = currentStore(item);
  const pick = pickFor(item);
  const shown = (key) => local.showAll || inUse.has(key) || key === pick || key === today;
  const urls = Object.fromEntries(Object.entries(item.urls || {}).filter(([key]) => shown(key)));
  const kinds = item.url_kinds || {};
  const prices = local.sim?.item_prices?.[String(item.id)] || {};
  const name = item[cols.comida] ?? "";
  const options = [...new Set([today, ...Object.keys(urls)].filter(Boolean))];
  const meta = !options.length || !Object.keys(item.urls || {}).length
    ? `${today ? `${esc(storeName(today))} · ` : ""}No store links yet`
    : pick !== today ? `What-if · in list ${esc(storeName(today))}` : `Bought at ${esc(storeName(today))}`;
  const select = options.length > 1
    ? `<select class="field store-pick" data-stores-pick aria-label="Buy ${esc(name)} at">${options.map((key) =>
        `<option value="${esc(key)}"${key === pick ? " selected" : ""}>${esc(storeName(key))}${key === today ? " (in list)" : ""}</option>`).join("")}</select>`
    : "";
  // The picked store leads, then the cheapest: with up to nine links on one
  // swipeable line, what matters must not start off-screen.
  const rank = ([key]) => (key === pick ? -1 : prices[key] ?? Number.MAX_VALUE);
  const chips = Object.entries(urls).sort((a, b) => rank(a) - rank(b)).map(([key, url]) => {
    const price = prices[key];
    const picked = key === pick;
    const kind = kinds[key];
    const label = `Open ${name} at ${storeName(key)}${price !== undefined ? `, ${eur(price)} a month` : ""}${kind ? ` (${LINK_NOTE[kind]})` : ""}`;
    const lead = picked ? icon("check") : kind === "search" ? icon("search") : kind === "suspect" ? icon("circle-alert") : "";
    return `<a class="store-chip${picked ? " is-picked" : ""}${kind ? ` is-${kind}` : ""}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(label)}"${kind ? ` title="${esc(LINK_NOTE[kind])}"` : ""}>${lead}<span>${esc(storeName(key))}</span>${price !== undefined ? `<span class="store-chip-price">${eur(price)}/mo</span>` : ""}</a>`;
  }).join("");
  return `<li class="store-row" data-item-id="${item.id}">
    <div class="store-row-head">
      <div class="store-row-text">
        <span class="store-row-title">${esc(name)}</span>
        <span class="store-row-line">${reviewBadge(item)}<span class="store-row-meta">${meta}</span></span>
      </div>
      ${select}
      <button type="button" class="icon-btn" data-stores-action="detail" aria-label="Review ${esc(name)}: stores, packs and prices">${icon("pencil")}</button>
    </div>
    ${chips ? `<div class="store-chips">${chips}</div>` : ""}
  </li>`;
}

// All · Needs checking (n) · Checked (n) — composes with the search box and
// the show-all switch; remembered per viewer.
function filterMarkup() {
  const counts = local.checks?.counts;
  const count = { needs: counts?.needs_checking, checked: counts?.checked };
  return `<div class="pills stores-filter" role="radiogroup" aria-label="Show items">${FILTERS.map(([key, label]) => {
    const on = key === local.filter;
    const n = count[key];
    return `<button type="button" class="pill${on ? " active" : ""}" role="radio" aria-checked="${on}" data-stores-filter="${key}">${label}${n !== undefined ? ` <span class="pill-count">${n}</span>` : ""}</button>`;
  }).join("")}</div>`;
}

// Store (#170): one toggle per store the list buys from, with its item count
// over the whole list (like the status counts). Several can be on at once;
// none on = every store. Composes with the status pills and the search box.
function storeFilterMarkup(inUse = storesInUse()) {
  const counts = {};
  for (const item of state.payload?.items || []) {
    const key = currentStore(item);
    if (key) counts[key] = (counts[key] || 0) + 1;
  }
  const selected = selectedStores(inUse);
  const keys = [...inUse].sort((a, b) => storeName(a).localeCompare(storeName(b)));
  if (!keys.length) return "";
  return `<div class="pills stores-filter" role="group" aria-labelledby="stores-store-label"><span class="stores-filter-label" id="stores-store-label">Store</span>${keys.map((key) => {
    const on = selected.includes(key);
    return `<button type="button" class="pill${on ? " active" : ""}" aria-pressed="${on}" data-stores-store="${esc(key)}">${esc(storeName(key))} <span class="pill-count">${counts[key]}</span></button>`;
  }).join("")}</div>`;
}

function paintFilter() {
  const host = document.querySelector("#stores-filter");
  if (!host) return;
  const focused = document.activeElement?.closest?.("[data-stores-filter], [data-stores-store]");
  const refocus = focused?.dataset.storesFilter !== undefined
    ? `[data-stores-filter="${focused.dataset.storesFilter}"]`
    : focused ? `[data-stores-store="${CSS.escape(focused.dataset.storesStore)}"]` : "";
  host.innerHTML = filterMarkup() + storeFilterMarkup();
  if (refocus) host.querySelector(refocus)?.focus();
}

// Why the list is empty; a store selection is named after it ("… at Ametller.").
const FILTER_EMPTY = {
  all: ["search", "No matching items"],
  needs: ["circle-check", "Nothing needs checking"],
  checked: ["list-checks", "No items checked yet"],
};
const storeList = new Intl.ListFormat("en", { type: "disjunction" });

function paintList() {
  const list = document.querySelector("#stores-list");
  if (!list) return;
  const cols = c();
  const filter = local.checks ? local.filter : "all";
  const inUse = storesInUse();
  const stores = selectedStores(inUse);
  const source = filteredItems()
    .filter((item) => filter === "all" || reviewState(item) === filter)
    .filter((item) => !stores.length || stores.includes(currentStore(item)))
    .sort((a, b) => String(a[cols.comida] ?? "").localeCompare(String(b[cols.comida] ?? "")));
  const linked = source.filter((item) => Object.keys(item.urls || {}).length).length;
  document.querySelector("#stores-list-count").textContent = `${plural(source.length, "item")} · ${linked} with links`;
  const [glyph, message] = FILTER_EMPTY[state.query ? "all" : filter] || FILTER_EMPTY.all;
  const at = stores.length ? ` at ${storeList.format(stores.map(storeName))}` : "";
  list.innerHTML = source.map((item) => rowMarkup(item, inUse)).join("")
    || `<li>${emptyStateEl(glyph, `${message}${at}.`).outerHTML}</li>`;
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
    local.metaState = "idle";  // reloads the run status and the review checks
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
  local.actionNote = { kind: "ok", text: "Back to the stores in your list" };
  render();
  scheduleSimulate(0);
}

// The Clipboard API needs a secure context (plain-HTTP LAN access has none),
// so fall back to a selected textarea + execCommand before giving up.
async function copySteps() {
  const text = `How to refresh the supermarket prices:\n${REFRESH_STEPS.map((step, i) => `${i + 1}. ${step}`).join("\n")}`;
  let copied = false;
  try {
    await navigator.clipboard.writeText(text);
    copied = true;
  } catch (_) {
    const area = document.createElement("textarea");
    area.value = text;
    area.setAttribute("readonly", "");
    area.className = "visually-hidden";
    document.body.appendChild(area);
    area.select();
    try {
      copied = document.execCommand("copy");
    } catch (_) {
      copied = false;
    }
    area.remove();
  }
  const status = document.querySelector("#stores-copy-status");
  if (!status) return;
  status.className = `panel-status ${copied ? "ok" : "error"}`;
  status.textContent = copied ? "Steps copied." : "Couldn't copy. Select the steps above and copy them by hand.";
}

// ------------------------------------------------------------- dialogs
// The editors are native <dialog>s on the vendored modal shell, built once
// and kept at <body> level so a pane re-render never tears them down.
function dialogShell(id, title, saveLabel, init) {
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
  init?.(dialog);
  document.body.appendChild(dialog);
  return dialog;
}

function setDialogStatus(dialog, text, kind = "error") {
  const status = dialog.querySelector(".stores-dialog-status");
  status.className = `panel-status ${kind} stores-dialog-status`;
  status.textContent = text;
}

// After anything that can move prices or review flags: the checks (pills,
// badges) and the simulator (totals, chip prices — it repaints the list).
function afterReviewChange() {
  loadChecks();
  scheduleSimulate(0);
}

// ---------------------------------------------------- item detail (#165)
function detailDialog() {
  return dialogShell(DETAIL_ID, "", "Save target & stock", (dialog) => {
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

async function openItemDetail(id) {
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
      ${qtyFieldMarkup("tenemos", "Stock", d.list.tenemos, suggested.tenemos, `${packCtx} you have`)}
    </div>`;
}

function qtyFieldMarkup(field, label, saved, suggested, packCtx) {
  const value = local.qtyDraft[field] ?? saved;
  const suggest = suggested !== undefined && suggested !== null && suggested !== saved
    ? `<button type="button" class="secondary review-suggest" data-review-suggest="${field}" data-value="${suggested}">Use suggested (${suggested})</button>`
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
      ${link}${kind}${benchLink}${changed}
    </td>
    <td class="review-cell-num" data-label="Pack">${esc(packOf(s) || "–")}${yoursMarkup(s, ["pack_size", "unit"], packOf(bench))}</td>
    <td class="review-cell-num" data-label="Pack price">${eur(s.pack_price)}${yoursMarkup(s, ["pack_price"], isSet(bench.pack_price) ? eur(bench.pack_price) : "")}</td>
    <td class="review-cell-num" data-label="Per unit">${s.price_per_unit === null ? "–" : `${eur(s.price_per_unit)}/${esc(unit)}`}</td>
    <td class="review-cell-num" data-label="Per month">${eur(s.monthly_cost)}</td>
    <td class="review-cell-status" data-label="Status">${esc(statusText(s))}</td>
    <td class="review-cell-edit"><button type="button" class="icon-btn" data-review-edit="${esc(s.store)}"
        aria-expanded="${editing}" aria-label="Edit ${esc(s.store_name)}">${icon(editing ? "x" : "pencil")}</button></td>
  </tr>`;
  return editing ? row + `<tr class="review-edit-row"><td colspan="8">${editFormMarkup(d, s)}</td></tr>` : row;
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
          <select id="${id("unit")}" class="field" name="unit">
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
        <button type="submit" class="big-btn">Save</button>
        ${s.override ? `<button type="button" class="secondary" data-review-reset="${esc(s.store)}">Reset to benchmark</button>` : ""}
        <button type="button" class="secondary" data-review-cancel>Cancel</button>
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
        <select class="field" data-review-add-store aria-label="Store">${missing.map((s) =>
          `<option value="${esc(s.key)}">${esc(s.name)}</option>`).join("")}</select>
        <input id="review-add-url" class="field" type="url" inputmode="url" autocomplete="off" spellcheck="false"
               placeholder="https://…" data-review-add-url>
        <button type="button" class="secondary" data-review-add>Add link</button>
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

// ---------------------------------------------- item detail: mutations
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
  for (const [field, label] of [["cantidad", "Target"], ["tenemos", "Stock"]]) {
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
    setDialogStatus(dialog, "Target and stock saved.", "ok");
    loadChecks();
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

// ------------------------------------------------------ review & apply
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
    const body = await fetchJson("/api/stores/apply-preview", jsonInit("POST", { picks: activePicks() }));
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
  bodyEl.innerHTML = `<p class="hint">Target and stock are converted by pack size. Adjust any before applying.</p>
    <ul class="apply-rows">${local.preview.map(applyRowMarkup).join("")}</ul>`;
  const ready = local.preview.filter((ch) => !ch.flags.includes("no_url")).length;
  save.textContent = `Apply ${ready} change${ready === 1 ? "" : "s"}`;
  save.disabled = ready === 0;
  save.onclick = () => submitApply(dialog);
}

function packText(pack) {
  return pack ? `${num(pack.size)} ${esc(pack.unit)}` : "unknown";
}

function applyRowMarkup(change) {
  const blocked = change.flags.includes("no_url");
  const warnings = change.flags.filter((f) => FLAG_TEXT[f]);
  const flagged = warnings.length > 0;
  const flagLines = blocked
    ? `<p class="apply-flag">${icon("circle-alert")}No ${esc(storeName(change.to))} link for this item yet. Add one with its edit button first; it is skipped.</p>`
    : warnings.map((f) => `<p class="apply-flag">${icon("circle-alert")}${FLAG_TEXT[f]}</p>`).join("");
  const names = change.old_name || change.new_name
    ? `<span class="apply-row-move apply-row-names">${esc(change.old_name || "unknown product")}${icon("arrow-right")}${esc(change.new_name || "unknown product")}</span>` : "";
  return `<li class="apply-row${blocked ? " is-blocked" : ""}${flagged ? " is-flagged" : ""}" data-apply-id="${change.id}">
    <span class="apply-row-title">${esc(change.comida)}</span>
    <span class="apply-row-move">${esc(storeName(change.from))}${icon("arrow-right")}${esc(storeName(change.to))}</span>
    ${names}
    <span class="apply-row-move">Pack ${packText(change.old_pack)}${icon("arrow-right")}${packText(change.new_pack)}</span>
    <label class="row"><span>Target <span class="meta">was ${change.old_cantidad}</span></span>
      <input class="input-native apply-qty" type="number" min="0" step="1" inputmode="numeric"
             value="${change.cantidad}" aria-label="New target for ${esc(change.comida)}"${blocked ? " disabled" : ""}></label>
    <label class="row"><span>Stock <span class="meta">was ${change.tenemos_from}</span></span>
      <input class="input-native apply-stock" type="number" min="0" step="1" inputmode="numeric"
             value="${change.tenemos}" aria-label="New stock for ${esc(change.comida)}"${blocked ? " disabled" : ""}></label>
    ${flagLines}
  </li>`;
}

async function submitApply(dialog) {
  const save = dialog.querySelector(".detail-save-btn");
  const status = dialog.querySelector(".stores-dialog-status");
  const changes = [];
  for (const change of local.preview) {
    if (change.flags.includes("no_url")) continue;
    const row = dialog.querySelector(`[data-apply-id="${change.id}"]`);
    const values = {};
    for (const [field, cls, label] of [["cantidad", ".apply-qty", "Target"], ["tenemos", ".apply-stock", "Stock"]]) {
      const input = row.querySelector(cls);
      const n = Number(input.value);
      if (input.value.trim() === "" || !Number.isInteger(n) || n < 0) {
        status.textContent = `${label} for ${change.comida} must be a whole number of 0 or more.`;
        input.focus();
        return;
      }
      values[field] = n;
    }
    changes.push({ id: change.id, store: change.to, ...values });
  }
  save.disabled = true;
  status.textContent = "";
  try {
    const { applied, ...payload } = await fetchJson("/api/stores/apply", jsonInit("POST", { changes }));
    state.payload = payload;
    for (const change of changes) delete local.picks[change.id];
    writeStored(PICKS_KEY, local.picks);
    local.actionNote = { kind: "ok", text: `Applied ${applied} change${applied === 1 ? "" : "s"}` };
    dialog.close();
    render();
    scheduleSimulate(0);
    loadChecks();  // moved items now need checking
  } catch (error) {
    // e.g. 423 — the spreadsheet is open in Excel; the server's hint says so.
    status.textContent = error.message;
    save.disabled = false;
  }
}

// ------------------------------------------------------ event delegation
// Wired from app.js on .app; only acts on this view's own data-* hooks. The
// dialogs live at <body> level and carry their own listeners.
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
  const filter = event.target.closest("[data-stores-filter]");
  if (filter) {
    local.filter = filter.dataset.storesFilter;
    writeStored(FILTER_KEY, local.filter);
    document.querySelectorAll("[data-stores-filter]").forEach((b) => {
      const on = b === filter;
      b.classList.toggle("active", on);
      b.setAttribute("aria-checked", String(on));
    });
    paintList();
    return;
  }
  const store = event.target.closest("[data-stores-store]");
  if (store) {
    const key = store.dataset.storesStore;
    const on = !local.stores.includes(key);
    local.stores = on ? [...local.stores, key] : local.stores.filter((k) => k !== key);
    writeStored(STORE_FILTER_KEY, local.stores);
    store.classList.toggle("active", on);
    store.setAttribute("aria-pressed", String(on));
    paintList();
    return;
  }
  const showAll = event.target.closest("[data-stores-showall]");
  if (showAll) {
    // app.js's delegated switch handler has already flipped it.
    local.showAll = showAll.getAttribute("aria-checked") === "true";
    writeStored(SHOW_ALL_KEY, local.showAll);
    paintList();
    return;
  }
  const button = event.target.closest("[data-stores-action]");
  if (!button) return;
  const action = button.dataset.storesAction;
  if (action === "import") await importLatest(button);
  if (action === "copy-steps") await copySteps();
  if (action === "recommended") await loadRecommended();
  if (action === "reset") resetPicks();
  if (action === "review") await openApplyReview();
  if (action === "retry-sim") scheduleSimulate(0);
  if (action === "detail") await openItemDetail(Number(button.closest("[data-item-id]").dataset.itemId));
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
