// Stores: loading the benchmark meta + review checks, the debounced what-if simulation, and the
// header / actions / Monthly cost cards it paints.
import { emptyStateEl } from "../../_vendored/empty-state/empty-state.js";
import { fetchJson } from "../api.js";
import { render, state } from "../core.js";
import { esc, icon } from "../dom.js";
import { paintFilter, paintList } from "./list.js";
import { activePicks, eur, frequency, hasHandler, jsonInit, local, plural, storeName } from "./state.js";

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
  if (state.mode === "stores") render();
}

// Review flags and checked dates for every row. A failed read keeps the last
// good one; the filter pills then show no counts and the list stays unfiltered.
export async function loadChecks() {
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

function ageText(days) {
  if (days === null || days === undefined) return "";
  return days <= 0 ? "from today" : `${plural(days, "day")} old`;
}

// Benchmark status (#165): how old the prices are, when the next review is
// due, and the steps to refresh them — the benchmark itself runs in Claude
// Code (it needs your quality-spec confirmation), never from here.
export function headMarkup(howtoOpen = false) {
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

export function actionsMarkup() {
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

export function paintActions() {
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

export function paintSim() {
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
    <p class="hint">${todayHint()}${excludedNote()}</p>
    ${baselineButtonMarkup()}
    ${notComparable}${stale}`;
}

// The baseline (#183) is "active" once set and still tied to the latest run;
// a stale one (an older run superseded it) reads as not set — the household
// sets a fresh one instead of resurrecting a dropped run's stores.
function activeBaseline() {
  const b = local.sim?.baseline;
  return b && !b.stale ? b : null;
}

function todayHint() {
  const active = activeBaseline();
  if (active) return `Today = your stores as of ${esc(active.set_at.slice(0, 10))} (baseline).`;
  const stale = local.sim?.baseline?.stale
    ? ` A saved baseline from ${esc(local.sim.baseline.run_date)} is out of date (a newer run superseded it) and was not used.`
    : "";
  return `Today = your stores when prices were benchmarked (${esc(local.sim?.run_date || "")}).${stale}`;
}

function baselineButtonMarkup() {
  return activeBaseline()
    ? `<button type="button" class="secondary btn-block" data-stores-action="reset-baseline">${icon("refresh-cw")}Reset to benchmark</button>`
    : `<button type="button" class="secondary btn-block" data-stores-action="set-baseline">${icon("check")}Set as baseline</button>`;
}

// Target-0 items aren't bought, so the simulator leaves them out of both sides (#178).
function excludedNote() {
  const n = (local.sim?.excluded || []).length;
  return n ? ` ${n} item${n === 1 ? "" : "s"} with target 0 ${n === 1 ? "is" : "are"} left out of both.` : "";
}

export function paintUnpriced() {
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
