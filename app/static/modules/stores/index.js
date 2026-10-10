// Items → Stores (issue #148): every item's per-store product links, a what-if
// store picker, and the monthly cost simulator priced from the latest
// supermarket-benchmark run. Picks are a local what-if — nothing is written to
// the spreadsheet until the Review & apply dialog posts them.
//
// The per-item review (#165): the row's pencil opens the item sheet (#255) on
// its Stores & prices page — every store's product / pack / price with your
// overrides beside the benchmark's values, and the server-side Checked mark;
// its Quantity check page has the quantity maths in real units — and the list
// filters to the items that still need checking.
//
// The view is split across this folder: state (shared what-if state + helpers), simulator
// (loading + the Monthly cost card), list (rows + filters), actions (header/actions buttons),
// dialog + dialogs (apply / baseline), review (the item sheet's two store pages). This file is the
// facade app.js imports and owns the pane render + event delegation.
import { emptyStateEl } from "../../_vendored/empty-state/empty-state.js";
import { activePaneBody, render, state } from "../core.js";
import { icon, switchMarkup } from "../dom.js";
import { openItemSheet } from "../item-sheet.js";
import { copySteps, importLatest, loadRecommended, resetPicks } from "./actions.js";
import { openApplyReview, openSetBaselineConfirm, resetBaseline } from "./dialogs.js";
import { paintFilter, paintList, repaintRow } from "./list.js";
import { actionsMarkup, headMarkup, loadChecks, loadMeta, paintActions, paintSim, paintUnpriced, scheduleSimulate } from "./simulator.js";
import { FILTER_KEY, FREQ_KEY, SHOW_ALL_KEY, STORE_FILTER_KEY, TARGET_ONLY_KEY, local, setPick, writeStored } from "./state.js";

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
      <div id="stores-search-slot"></div>
      <div id="stores-filter"></div>
      <div class="stores-switches">
        <div class="flag-row">${switchMarkup(local.targetOnly, "Only items I buy", { "data-stores-targetonly": "" })}<span>Only items I buy (target above 0)</span></div>
        <div class="flag-row">${switchMarkup(local.showAll, "Show all stores", { "data-stores-showall": "" })}<span>Show all stores, not only the ones in your list</span></div>
      </div>
      <ul id="stores-list" class="store-rows"></ul>
    </section>`;
  paintSim();
  paintUnpriced();
  paintFilter();
  paintList();
  if (!local.sim && local.simState !== "empty") scheduleSimulate(0);
}

// app.js's delegated switch handler has already flipped a tapped switch, and
// setSwitch rebuilds its knob: a tap on the knob (it sits on top once the
// switch is on) leaves event.target detached, so closest() finds nothing and
// the switch could never be turned off. The dispatch path still holds it.
function switchHit(event, selector) {
  return event.composedPath().find((node) => node instanceof Element && node.matches(selector));
}

// Wired from app.js on .app; only acts on this view's own data-* hooks. The
// dialogs live at <body> level and carry their own listeners.
export async function onStoresClick(event) {
  if (state.mode !== "stores") return;
  const freq = event.target.closest("[data-stores-freq]");
  if (freq) {
    local.frequency = freq.dataset.storesFreq;
    writeStored(FREQ_KEY, local.frequency);
    document.querySelectorAll("[data-stores-freq]").forEach((b) => b.setAttribute("aria-pressed", String(b === freq)));
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
  const targetOnly = switchHit(event, "[data-stores-targetonly]");
  if (targetOnly) {
    local.targetOnly = targetOnly.getAttribute("aria-checked") === "true";
    writeStored(TARGET_ONLY_KEY, local.targetOnly);
    paintFilter();
    paintList();
    return;
  }
  const showAll = switchHit(event, "[data-stores-showall]");
  if (showAll) {
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
  if (action === "set-baseline") openSetBaselineConfirm();
  if (action === "reset-baseline") await resetBaseline();
  if (action === "detail") await openItemSheet(Number(button.closest("[data-item-id]").dataset.itemId), "prices");
}

// Search-only repaint (#193): a keystroke in the shared search box must not
// rebuild the whole Stores pane — the Monthly cost card, the simulator call
// and the toolbar re-home all stay untouched. Just refilter the rows and the
// "N items · M with links" count.
export function repaintStoresList() {
  paintList();
}

// For Items' page header (#254): what still needs checking, once the review
// state has been read; loadStoresChecks() reads it the first time Items opens.
export function storesNeedsChecking() {
  return local.checks?.counts?.needs_checking || 0;
}

export function loadStoresChecks() {
  if (!local.checks) loadChecks();
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
