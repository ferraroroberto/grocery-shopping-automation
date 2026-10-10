// Prices: the Benchmark sheet (#257) the glance card's footer row opens — the
// latest run's status and how to refresh it, the plan actions, and the cost
// breakdown behind the glance figure (goods, delivery, per store, the
// baseline, the items without a price). Its one primary imports the latest run.
//
// The sheet lives at <body> level like the item sheet, outside .app, so it
// carries its own click listener and hands its data-stores-action buttons to
// the view's one dispatcher.
import { emptyStateEl } from "../../_vendored/empty-state/empty-state.js";
import { chip, esc, icon } from "../dom.js";
import { dialogShell, setDialogStatus } from "./dialog.js";
import { runStoresAction } from "./index.js";
import { activePicks, eur, hasHandler, local, plural, storeName } from "./state.js";

const SHEET_ID = "stores-benchmark-sheet";

function dialog() {
  return dialogShell(SHEET_ID, "Benchmark", "Import latest run", (shell) => {
    shell.classList.add("benchmark-sheet");
    const primary = shell.querySelector(".detail-save-btn");
    primary.dataset.storesAction = "import";
    shell.addEventListener("click", async (event) => {
      const button = event.target.closest("[data-stores-action]");
      if (button) await runStoresAction(button);
    });
    shell.addEventListener("close", () => document.querySelector('[data-stores-action="benchmark"]')?.focus());
  });
}

export function openBenchmarkSheet() {
  const d = dialog();
  setDialogStatus(d, "");
  paintBenchmarkSheet();
  if (!d.open) d.showModal();
}

export function closeBenchmarkSheet() {
  document.getElementById(SHEET_ID)?.close();
}

// Repainted whenever the run or the simulation changes; a no-op until the
// sheet has been opened once. An open "How to refresh" stays open.
export function paintBenchmarkSheet() {
  const d = document.getElementById(SHEET_ID);
  if (!d || !local.meta) return;
  const body = d.querySelector(".stores-dialog-body");
  const howtoOpen = !!body.querySelector(".stores-howto[open]");
  const unpricedOpen = !!body.querySelector(".stores-unpriced[open]");
  body.innerHTML = `
    <section class="benchmark-section" aria-labelledby="benchmark-run-title">
      <h3 class="overline" id="benchmark-run-title">Latest run</h3>
      ${runMarkup()}
      ${howtoMarkup(howtoOpen)}
    </section>
    <section class="benchmark-section" aria-labelledby="benchmark-plan-title">
      <h3 class="overline" id="benchmark-plan-title">Plan</h3>
      ${planMarkup()}
    </section>
    <section class="benchmark-section" aria-labelledby="benchmark-cost-title">
      <h3 class="overline" id="benchmark-cost-title">Cost breakdown</h3>
      ${breakdownMarkup(unpricedOpen)}
    </section>`;
  d.querySelector(".detail-save-btn").disabled = false;
}

function ageText(days) {
  if (days === null || days === undefined) return "";
  return days <= 0 ? "from today" : `${plural(days, "day")} old`;
}

export function reviewDue() {
  const m = local.meta;
  return !!(m?.next_due && m.next_due <= new Date().toISOString().slice(0, 10));
}

// The run as label/value rows (design.md "editor modal" body rows), then the
// last import's result. The benchmark itself runs in Claude Code (it needs
// your quality-spec confirmation), never from here (#165).
function runMarkup() {
  const m = local.meta;
  const note = local.note;
  const result = `${note ? `<div class="panel-status ${note.kind}" role="status">${esc(note.text)}</div>` : ""}
    ${note?.warning ? `<div class="panel-status warn">${icon("circle-alert")} ${esc(note.warning)}</div>` : ""}`;
  if (!m.run_date) return `<p class="hint">No benchmark run yet. Run the supermarket benchmark, then import it here.</p>${result}`;
  const covered = m.stores_covered || [];
  const due = reviewDue();
  const row = (label, value) => `<div class="benchmark-row"><dt>${label}</dt><dd>${value}</dd></div>`;
  return `<dl class="benchmark-rows stores-status">
      ${row("Run", `${esc(m.run_date)}<span class="meta"> · ${ageText(m.age_days)}</span>`)}
      ${row(due ? "Review due since" : "Next review", `<span class="${due ? "stores-due" : ""}">${due ? icon("circle-alert") : ""}${esc(m.next_due)}</span>`)}
      ${row("Stores covered", `<span title="${esc(covered.map(storeName).join(", "))}">${covered.length}</span>`)}
      ${row("Your overrides", esc(m.overrides || 0))}
    </dl>${result}`;
}

function howtoMarkup(open) {
  return `<details class="stores-howto"${open ? " open" : ""}>
      <summary class="stores-howto-summary">${icon("refresh-cw")}<span>How to refresh the prices</span><span class="inline-chevron" aria-hidden="true">›</span></summary>
      <ol class="stores-howto-steps">
        <li>In Claude Code, in this repo, run <code>/supermarket-benchmark</code>. It takes about an hour and asks you to confirm the quality specs.</li>
        <li>Come back here and tap <strong>Import latest run</strong>.</li>
        <li>Review the <strong>Needs checking</strong> items in Prices.</li>
      </ol>
      <button type="button" class="button-surface btn-block" data-stores-action="copy-steps">${icon("copy")}Copy steps</button>
      <p id="stores-copy-status" class="panel-status" role="status"></p>
    </details>`;
}

function planMarkup() {
  const count = Object.keys(activePicks()).length;
  return `<div class="benchmark-actions">
      <button type="button" class="button-tint btn-block" data-stores-action="recommended"${local.meta.run_date ? "" : " disabled"}>Load recommended plan</button>
      <button type="button" class="button-surface btn-block" data-stores-action="reset"${count ? "" : " disabled"}>Reset to my list${count ? ` (${count})` : ""}</button>
    </div>`;
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

// Target-0 items aren't bought, so the simulator leaves them out of both sides (#178).
function excludedNote() {
  const n = (local.sim?.excluded || []).length;
  return n ? ` ${n} item${n === 1 ? "" : "s"} with target 0 ${n === 1 ? "is" : "are"} left out of both.` : "";
}

function baselineButtonMarkup() {
  return activeBaseline()
    ? `<button type="button" class="button-surface btn-block" data-stores-action="reset-baseline">${icon("refresh-cw")}Reset to benchmark</button>`
    : `<button type="button" class="button-surface btn-block" data-stores-action="set-baseline">${icon("check")}Set as baseline</button>`;
}

function unpricedMarkup(open) {
  const unpriced = local.sim?.picks.unpriced || [];
  if (!unpriced.length) return "";
  return `<details class="stores-unpriced"${open ? " open" : ""}>
      <summary class="stores-howto-summary">${icon("circle-alert")}<span>No price: ${plural(unpriced.length, "item")} left out of the totals</span><span class="inline-chevron" aria-hidden="true">›</span></summary>
      <ul class="stores-unpriced-list">${unpriced.map((u) =>
        `<li><span>${esc(u.comida)}</span><span class="meta">${esc(storeName(u.store))}</span></li>`).join("")}</ul>
    </details>`;
}

function breakdownMarkup(unpricedOpen) {
  if (local.simState === "empty") return emptyStateEl("shopping-basket", "No benchmark prices to break down yet.").outerHTML;
  if (!local.sim) return emptyStateEl("refresh-cw", "Pricing your picks…").outerHTML;
  const { picks: whatIf, today } = local.sim;
  const rows = [
    ["Goods", "goods"], ["Delivery", "delivery"], ["Total", "total"], ["Total, fee-optimised", "total_optimised"],
  ].map(([label, key]) => `<tr><th scope="row">${label}</th><td>${eur(whatIf[key])}</td><td>${eur(today[key])}</td></tr>`).join("");
  const perStore = Object.entries(whatIf.per_store).map(([key, s]) => `<li>
      <span class="stores-store-name">${esc(storeName(key))}</span>
      ${hasHandler(key) ? "" : chip("manual order")}
      <span class="stores-store-figures">${eur(s.goods)} goods · ${s.orders} orders · ${eur(s.monthly_fee)} fees</span>
    </li>`).join("");
  const notComparable = local.sim.comparable ? "" :
    `<p class="hint">Some items have no price on one side, so the two totals leave out different items.</p>`;
  return `<table class="stores-table">
      <thead><tr><th scope="col"><span class="meta">Per month</span></th><th scope="col">What-if</th><th scope="col">Today</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
    <ul class="stores-per-store">${perStore}</ul>
    <p class="hint stores-today">${todayHint()}${excludedNote()}</p>
    ${notComparable}
    ${baselineButtonMarkup()}
    ${unpricedMarkup(unpricedOpen)}`;
}
