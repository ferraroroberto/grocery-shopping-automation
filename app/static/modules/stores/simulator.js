// Prices: loading the benchmark meta + review checks, the debounced what-if
// simulation, and the glance card it paints (#257) — am I on the cheapest
// plan? The monthly cost and its delta, the ordering frequency, the one
// action, and a footer row to the Benchmark sheet (benchmark.js), which holds
// the breakdown behind the figure.
import { emptyStateEl } from "../../_vendored/empty-state/empty-state.js";
import { fetchJson } from "../api.js";
import { render, state, updateHeaders } from "../core.js";
import { esc, icon } from "../dom.js";
import { paintBenchmarkSheet, reviewValue } from "./benchmark.js";
import { paintFilter, paintList } from "./list.js";
import { activePicks, eur, frequency, jsonInit, local } from "./state.js";

const SIM_DEBOUNCE_MS = 350;

const FREQ_LABELS = { weekly: "Weekly", "2-weekly": "2-weekly", monthly: "Monthly" };

export async function loadMeta() {
  local.metaState = "loading";
  try {
    local.meta = await fetchJson("/api/stores");
    local.metaState = "ready";
    loadChecks();
  } catch (_) {
    local.metaState = "error";
  }
  updateHeaders(); // Home's Prices row names the next review
  if (state.mode === "stores") render();
  paintBenchmarkSheet();
}

// Review flags and checked dates for every row. A failed read keeps the last
// good one; the filter pills then show no counts and the list stays unfiltered.
export async function loadChecks() {
  try {
    local.checks = await fetchJson("/api/stores/checks");
  } catch (_) {
    // keep what we had — the list still works without the review state
  }
  updateHeaders(); // Items' header counts what needs checking
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
  paintBenchmarkSheet();
  if (state.mode !== "stores") return;
  paintSim();
  paintList();
}

function freqMarkup() {
  const current = frequency();
  return `<div class="segmented" role="group" aria-label="Ordering frequency">${
    Object.keys(local.meta.frequencies).map((key) =>
      `<button type="button" class="segmented-item" aria-pressed="${key === current}" data-stores-freq="${esc(key)}">${esc(FREQ_LABELS[key] || key)}</button>`,
    ).join("")}</div>`;
}

// The glance card's one action: Review & apply once a pick differs from the
// list, else the recommended plan — the way to see the cheapest one.
function actionMarkup() {
  const count = Object.keys(activePicks()).length;
  if (count) return `<button type="button" class="button-primary btn-block" data-track="prices.review" data-stores-action="review">Review &amp; apply (${count})</button>`;
  return `<button type="button" class="button-primary btn-block" data-track="prices.recommended" data-stores-action="recommended"${local.meta.run_date ? "" : " disabled"}>Load recommended plan</button>`;
}

export function paintActions() {
  const slot = document.querySelector("#stores-glance-action");
  if (slot) slot.innerHTML = actionMarkup();
  paintBenchmarkSheet();
}

// The footer row: what the latest run says about itself, one tap from the sheet.
function footMarkup() {
  const { text, tone } = reviewValue();
  return `<ul class="action-rows stores-glance-foot"><li class="action-row">
      <button type="button" class="action-row-main sheet-link" data-track="prices.benchmark" data-stores-action="benchmark" aria-haspopup="dialog">
        ${icon("shopping-basket", "sheet-link-icon")}
        <span class="action-row-title">Benchmark</span>
        <span class="sheet-link-value"${tone ? ` data-tone="${tone}"` : ""}>${esc(text)}</span>
        ${icon("chevron-right", "sheet-link-chevron")}
      </button>
    </li></ul>`;
}

export function paintSim() {
  const card = document.querySelector("#stores-sim");
  if (!card) return;
  card.removeAttribute("aria-busy");
  card.dataset.state = local.simState;
  const head = `<div class="card-head"><h2 class="card-title">${icon("shopping-cart")}Monthly cost</h2></div>`;
  const action = `<div id="stores-glance-action">${actionMarkup()}</div>`;
  if (local.simState === "empty") {
    card.innerHTML = head + emptyStateEl("shopping-basket", "No benchmark prices yet. Import a run from the Benchmark sheet.").outerHTML + footMarkup();
    return;
  }
  if (!local.sim) {
    const failed = local.simState === "error";
    const block = emptyStateEl(failed ? "circle-alert" : "refresh-cw", failed ? "Simulation unavailable." : "Pricing your picks…",
      failed ? { actionLabel: "Retry" } : undefined);
    card.innerHTML = head + block.outerHTML + freqMarkup() + footMarkup();
    card.querySelector(".empty-state-action")?.setAttribute("data-stores-action", "retry-sim");
    return;
  }
  const { picks: whatIf, delta } = local.sim;
  const saves = delta.total < 0;
  const deltaText = delta.total === 0 ? "Same as today" : `${saves ? "Saves" : "Costs"} ${eur(Math.abs(delta.total))} a month vs today`;
  const stale = local.simState === "stale"
    ? `<p class="panel-status warn">Last updated ${local.simUpdated?.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }) || ""} · simulation unavailable</p>` : "";
  card.innerHTML = `${head}
    <div class="stores-total">
      <div><span class="meta">What-if total</span><strong>${eur(whatIf.total)}</strong></div>
      <span class="stores-delta${saves ? " ok" : ""}" role="status">${deltaText}</span>
    </div>
    ${stale}
    ${freqMarkup()}
    ${action}
    ${footMarkup()}`;
}
