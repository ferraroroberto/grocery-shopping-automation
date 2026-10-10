// Cart automation — Shop's carts glance (#258; Fill carts since #182, the
// retired Auto tab before that). The glance card leads the tab and answers
// "are the carts filled?": one row per store on the list with the last fill's
// outcome, the one Fill carts button, the run's progress and outcome, and the
// run log folded. Its Options row opens a sheet with the store, cart mode, dry
// run and clean confirm, and a short "what will run" in words.
//
// The last fill comes from the run records a live run writes (GET
// /api/automation/fills), so it outlives the server; the run in flight comes
// from the status endpoint and its event stream. Run state and the picks are
// local to this module; Shop's header, its tab badge and its store groups read
// the outcome through the exports below.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { setSwitch } from "../_vendored/switch/switch.js";
import { fetchJson } from "./api.js";
import { c, items, state, storedToken, updateHeaders } from "./core.js";
import { chip, esc, formatElapsed, html, icon, switchMarkup } from "./dom.js";
import { dialogShell } from "./stores/dialog.js";

const OPTIONS_ID = "fill-carts-options";
const SEEN_KEY = "grocery.fillSeen";

const run = {
  source: null,
  startedAt: null,
  timer: null,
  status: null, // the last status applied — which stores the run in flight covers
};

// What the next Fill carts runs with. Kept here, not read back from the
// Options sheet, so the glance can start a run before the sheet was ever opened.
const picks = { store: "all", cartMode: "keep", dryRun: false, cleanConfirmed: false };

// The store directory (display names, which stores have a cart handler) and
// the last fill day's outcome per store key.
const fills = { stores: [], last: null };

const IDLE_LOG = "Not running. Press Fill carts to fill the store carts; progress appears here.";

function host() {
  return document.querySelector("#fill-carts-host");
}

// ------------------------------------------------------------ store names
// The list holds the store as typed in the sheet; the run records and the
// directory key it lower-cased. Never show the key (#252 decision 5).
function storeKey(store) {
  return String(store ?? "").trim().toLowerCase();
}

function directoryEntry(store) {
  return fills.stores.find((s) => s.key === storeKey(store));
}

export function storeLabel(store) {
  return directoryEntry(store)?.name || String(store ?? "");
}

function joinNames(names) {
  return names.length < 2 ? names.join("") : `${names.slice(0, -1).join(", ")} and ${names.at(-1)}`;
}

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

// Stores with something to buy, and how many items each — what a fill acts on.
function listStores() {
  const cols = c();
  const counts = new Map();
  for (const item of items()) {
    if (Number(item[cols.comprar]) > 0) counts.set(item[cols.super], (counts.get(item[cols.super]) || 0) + 1);
  }
  return [...counts].map(([store, count]) => ({ store, count, label: storeLabel(store) }))
    .sort((a, b) => a.label.localeCompare(b.label));
}

// ---------------------------------------------------------------- the outcome
// Items the last fill left out of the carts: Shop's header exception.
export function fillMissingCount() {
  return Object.values(fills.last?.stores || {}).reduce((n, s) => n + s.missing, 0);
}

function seenRun() {
  try {
    return localStorage.getItem(SEEN_KEY);
  } catch (_) {
    return null;
  }
}

// The Shop tab badge (#252 decision 11): the last fill's misses until Shop has
// been seen since that fill. Seeing Shop is what clears it, so this marks the
// fill seen whenever Shop is the open tab.
export function unseenFillMisses() {
  const missing = fillMissingCount();
  if (!missing) return 0;
  if (state.mode === "shopping") {
    try {
      localStorage.setItem(SEEN_KEY, fills.last.run);
    } catch (_) {
      // private mode — the badge just clears on every Shop visit
    }
    return 0;
  }
  return seenRun() === fills.last.run ? 0 : missing;
}

export async function loadFills() {
  try {
    const body = await fetchJson("/api/automation/fills");
    fills.stores = body.stores || [];
    fills.last = body.last_fill;
  } catch (_) {
    return; // keep what we had; the glance still runs a fill without it
  }
  updateHeaders();
  if (state.mode === "shopping") {
    paintCarts();
    // Store-group titles carry the display names the directory just brought.
    document.querySelectorAll("#pane-shopping [data-store-label]").forEach((node) => {
      node.textContent = storeLabel(node.dataset.storeLabel);
    });
  }
}

// --------------------------------------------------------------- the glance
// The card is painted once and then left alone: the timer, the summary and an
// open log must survive Shop's re-render on every Got-it tap. paintCarts()
// refreshes only what the list and the last fill decide.
export function renderFillCarts({ force = false } = {}) {
  const node = host();
  if (!node) return;
  if (!node.firstElementChild || force) {
    node.innerHTML = `<section id="fill-carts" class="card carts-glance" aria-labelledby="fill-carts-title">
      <div class="card-head">
        <h2 id="fill-carts-title" class="card-title">${icon("shopping-cart")}Carts</h2>
        <span class="card-head-meta" id="carts-last-fill"></span>
      </div>
      <ul id="carts-stores" class="action-rows carts-stores"></ul>
      <div id="automation-elapsed" class="panel-status" role="status"></div>
      <pre id="automation-summary" class="log" hidden></pre>
      <div class="carts-actions">
        <button id="automation-start" class="button-primary btn-block" type="button"></button>
        <button id="automation-stop" class="button-tint danger btn-block" type="button" hidden>${icon("square")}Stop</button>
        <button id="automation-dismiss" class="button-surface btn-block" type="button" hidden>Dismiss</button>
      </div>
      <p id="automation-blocked" class="panel-status warn" hidden>Clean cart needs your OK in Options.</p>
      <ul class="action-rows carts-glance-foot"><li class="action-row">
        <button id="automation-options" type="button" class="action-row-main sheet-link" aria-haspopup="dialog">
          ${icon("sliders-horizontal", "sheet-link-icon")}
          <span class="action-row-title">Options</span>
          <span class="sheet-link-value" id="automation-options-value"></span>
          ${icon("chevron-right", "sheet-link-chevron")}
        </button>
      </li></ul>
      <details class="inline-disclosure carts-log">
        <summary class="inline-disclosure-summary"><span>Run log</span><span class="inline-chevron" aria-hidden="true">›</span></summary>
        <pre id="automation-log" class="log">${IDLE_LOG}</pre>
      </details>
    </section>`;
    refreshAutomation();
  }
  paintCarts();
}

// The run in flight covers every store, or the one it was started for.
function inRun(store) {
  const s = run.status;
  return !!s?.running && (s.store === "all" || storeKey(s.store) === storeKey(store));
}

// One exception chip per store row, in the tone map's order of urgency.
function storeChip(store) {
  if (inRun(store)) return { label: "Filling", tone: "accent" };
  const entry = directoryEntry(store);
  if (entry && !entry.has_handler) return { label: "Manual order", tone: "neutral" };
  const outcome = fills.last?.stores[storeKey(store)];
  if (outcome?.missing) return { label: `${outcome.missing} not in cart`, tone: "danger" };
  if (outcome?.unconfirmed) return { label: `${outcome.unconfirmed} not confirmed`, tone: "attention" };
  return null;
}

function storeRow({ store, count, label }) {
  const outcome = fills.last?.stores[storeKey(store)];
  const filled = outcome ? ` · ${outcome.in_cart + outcome.unconfirmed} in cart` : "";
  const status = storeChip(store);
  const chipMarkup = status ? ` ${chip(status.label, status.tone)}` : "";
  return `<li class="action-row">
      <div class="action-row-main">
        <span class="action-row-title">${html(label)}</span>
        <span class="action-row-meta">${esc(`${count} to buy${filled}`)}${chipMarkup}</span>
      </div>
    </li>`;
}

function fillDateText() {
  if (!fills.last) return "Not filled yet";
  const day = new Date(`${fills.last.date}T00:00:00`);
  return `Last fill ${day.toLocaleDateString("en-GB", { weekday: "short", day: "numeric", month: "short" })}`;
}

function cleanBlocked() {
  return picks.cartMode === "clean" && !picks.dryRun && !picks.cleanConfirmed;
}

function optionsValue() {
  const store = picks.store === "all" ? "All stores" : storeLabel(picks.store);
  return `${store} · ${picks.cartMode === "clean" ? "Clean cart" : "Keep cart"}${picks.dryRun ? " · Dry run" : ""}`;
}

export function paintCarts() {
  const list = document.querySelector("#carts-stores");
  if (!list) return;
  const stores = listStores();
  list.innerHTML = stores.length
    ? stores.map(storeRow).join("")
    : `<li class="carts-empty">${emptyStateEl("circle-check", "Nothing on the list to fill.").outerHTML}</li>`;
  document.querySelector("#carts-last-fill").textContent = fillDateText();
  const start = document.querySelector("#automation-start");
  start.innerHTML = `${icon("play")}${picks.dryRun ? "Start dry run" : "Fill carts"}`;
  start.disabled = cleanBlocked();
  document.querySelector("#automation-blocked").hidden = !cleanBlocked() || start.hidden;
  document.querySelector("#automation-options-value").textContent = optionsValue();
}

// ------------------------------------------------------------ Options sheet
function optionsSheet() {
  return dialogShell(OPTIONS_ID, "Fill carts options", "Done", (shell) => {
    shell.classList.add("fill-options");
    const done = shell.querySelector(".detail-save-btn");
    done.disabled = false;
    done.addEventListener("click", () => shell.close());
    shell.addEventListener("click", onOptionsClick);
    shell.addEventListener("change", onOptionsChange);
    shell.addEventListener("close", () => document.querySelector("#automation-options")?.focus());
  });
}

export function openFillOptions() {
  const d = optionsSheet();
  paintOptions();
  if (!d.open) d.showModal();
}

function paintOptions() {
  const d = document.getElementById(OPTIONS_ID);
  if (!d) return;
  const supermarkets = [...(state.payload?.summary.supermarkets || [])].sort((a, b) => storeLabel(a).localeCompare(storeLabel(b)));
  const option = (value, label) => `<option value="${esc(value)}"${value === picks.store ? " selected" : ""}>${html(label)}</option>`;
  d.querySelector(".stores-dialog-body").innerHTML = `
    <label class="field-label">Store
      <select id="automation-store">${option("all", "All stores")}${supermarkets.map((s) => option(s, storeLabel(s))).join("")}</select>
    </label>
    <label class="field-label">Cart mode
      <select id="automation-cart-mode">
        <option value="keep"${picks.cartMode === "keep" ? " selected" : ""}>Keep cart</option>
        <option value="clean"${picks.cartMode === "clean" ? " selected" : ""}>Clean cart</option>
      </select>
    </label>
    <div class="flag-row"><span>Dry run</span>${switchMarkup(picks.dryRun, "Dry run", { id: "automation-dry-run" })}</div>
    <div id="automation-clean-warn" class="panel-status error"${picks.cartMode === "clean" ? "" : " hidden"}>Clean mode empties the store cart first — anything added by hand will be removed.</div>
    <div id="automation-clean-confirm-wrap" class="flag-row"${picks.cartMode === "clean" && !picks.dryRun ? "" : " hidden"}>
      <span>Yes, empty the cart first</span>${switchMarkup(picks.cleanConfirmed, "Yes, empty the cart first", { id: "automation-clean-confirm" })}
    </div>
    <section class="fill-plan" aria-labelledby="fill-plan-title">
      <h3 class="overline" id="fill-plan-title">What will run</h3>
      <p id="automation-plan" class="hint">${esc(planText())}</p>
    </section>`;
}

// "What will run", in words — never the interpreter command line (#252 decision 5).
function planText() {
  const stores = listStores().filter(({ store }) => picks.store === "all" || storeKey(store) === storeKey(picks.store));
  if (!stores.length) return "Nothing on the list to fill.";
  const manual = stores.filter(({ store }) => directoryEntry(store) && !directoryEntry(store).has_handler);
  const fillable = stores.filter((s) => !manual.includes(s));
  const count = fillable.reduce((n, s) => n + s.count, 0);
  const names = joinNames(fillable.map((s) => s.label));
  const cartWord = fillable.length === 1 ? "cart" : "carts";
  let text;
  if (!fillable.length) text = "No store here can be filled automatically.";
  else if (picks.dryRun) text = `Lists the ${plural(count, "item")} it would add to ${names}. No browser opens and no cart changes.`;
  else if (picks.cartMode === "clean") text = `Empties the ${names} ${cartWord}, then adds ${plural(count, "item")}. You confirm and pay in the browser.`;
  else text = `Adds ${plural(count, "item")} to the ${names} ${cartWord}, on top of what is already there. You confirm and pay in the browser.`;
  if (manual.length) {
    const many = manual.length > 1;
    text += ` ${joinNames(manual.map((s) => s.label))} ${many ? "have" : "has"} no automation, so ${many ? "their" : "its"} items show as not in cart.`;
  }
  return text;
}

function onOptionsClick(event) {
  const sw = event.target.closest('.toggle[role="switch"]');
  if (!sw) return;
  const on = sw.getAttribute("aria-checked") !== "true";
  setSwitch(sw, on);
  if (sw.id === "automation-dry-run") picks.dryRun = on;
  if (sw.id === "automation-clean-confirm") picks.cleanConfirmed = on;
  paintOptions();
  paintCarts();
}

function onOptionsChange(event) {
  if (event.target.id === "automation-store") picks.store = event.target.value;
  if (event.target.id === "automation-cart-mode") {
    picks.cartMode = event.target.value;
    picks.cleanConfirmed = false; // a fresh OK for every switch to Clean
  }
  paintOptions();
  paintCarts();
}

// ------------------------------------------------------------- the run
// Tab (re-)entry / foreground: pick up a run that started, finished or lost
// its event stream while Shop was out of view, and the last fill it wrote.
export function syncFillCarts() {
  loadFills();
  if (host()?.firstElementChild && !run.source) refreshAutomation();
}

async function refreshAutomation() {
  const status = await fetchJson("/api/automation/status").catch(() => null);
  if (!status) return;
  applyAutomationStatus(status);
  if (status.running) {
    if (!run.startedAt) run.startedAt = Date.now();
    startAutomationTimer();
    connectAutomationEvents();
  }
}

function applyAutomationStatus(status) {
  run.status = status;
  const log = document.querySelector("#automation-log");
  if (log) log.textContent = status.lines?.length ? status.lines.join("\n") : (status.running ? "(waiting for output…)" : IDLE_LOG);
  const finished = !status.running && status.returncode !== null && status.returncode !== undefined;
  const start = document.querySelector("#automation-start");
  const stop = document.querySelector("#automation-stop");
  const dismiss = document.querySelector("#automation-dismiss");
  if (start) start.hidden = status.running || finished;
  if (stop) stop.hidden = !status.running;
  if (dismiss) dismiss.hidden = !finished;
  const elapsed = document.querySelector("#automation-elapsed");
  const summary = document.querySelector("#automation-summary");
  if (summary) summary.hidden = true;
  if (elapsed && !status.running && !finished) {
    elapsed.className = "panel-status";
    elapsed.textContent = "";
  }
  if (elapsed && finished) {
    stopAutomationTimer();
    const [tone, text] = RUN_OUTCOME[status.returncode]
      || ["error", `Fill carts stopped with code ${status.returncode}. See the run log.`];
    elapsed.className = `panel-status ${tone}`;
    elapsed.textContent = text;
    // The run's own end-of-run summary (#247), lifted out of the long log so a
    // miss is in front of the user, not scrolled away.
    const lines = status.lines || [];
    const at = lines.lastIndexOf(SUMMARY_HEADER);
    if (summary && at >= 0) {
      summary.textContent = lines.slice(at).join("\n");
      summary.hidden = false;
    }
  }
  paintCarts();
}

// Exit codes from automation/run_automation.py (#247): 0 every item verified in
// the cart, 1 some item is NOT in the cart, 3 added but the cart couldn't be read.
const RUN_OUTCOME = {
  0: ["ok", "Every item is verified in the cart. Review and pay in the browser."],
  1: ["error", "Some items are NOT in the cart — see the summary. Sort them out before paying."],
  3: ["warn", "Items added but NOT confirmed — the store cart couldn't be read. Check it before paying."],
};
const SUMMARY_HEADER = "── Cart automation summary ──";

function startAutomationTimer() {
  if (run.timer) return;
  const tick = () => {
    const elapsed = document.querySelector("#automation-elapsed");
    if (!elapsed || !run.startedAt) return;
    elapsed.className = "panel-status";
    elapsed.textContent = `Filling carts… ${formatElapsed(Math.floor((Date.now() - run.startedAt) / 1000))}`;
  };
  tick();
  run.timer = window.setInterval(tick, 1000);
}

function stopAutomationTimer() {
  if (run.timer) {
    window.clearInterval(run.timer);
    run.timer = null;
  }
  run.startedAt = null;
}

function connectAutomationEvents() {
  if (run.source) run.source.close();
  let url = "/api/automation/events";
  const token = storedToken();
  if (token) url += `?token=${encodeURIComponent(token)}`;
  run.source = new EventSource(url);
  run.source.onmessage = async (event) => {
    const status = JSON.parse(event.data);
    applyAutomationStatus(status);
    if (!status.running) {
      run.source.close();
      run.source = null;
      const final = await fetchJson("/api/automation/status").catch(() => null);
      if (final) applyAutomationStatus(final);
      // A live run has written its run records: the glance's outcome per store.
      if (!status.dry_run) loadFills();
    }
  };
}

export async function startAutomation() {
  run.startedAt = Date.now();
  const status = await fetchJson("/api/automation/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ store: picks.store, dry_run: picks.dryRun, cart_mode: picks.cartMode }),
  });
  applyAutomationStatus(status);
  startAutomationTimer();
  connectAutomationEvents();
}

export async function stopAutomation() {
  await fetchJson("/api/automation/stop", { method: "POST" });
}

export async function dismissAutomation() {
  stopAutomationTimer();
  await fetchJson("/api/automation/reset", { method: "POST" }).catch(() => null);
  renderFillCarts({ force: true }); // repaints idle, then re-reads the status
}
