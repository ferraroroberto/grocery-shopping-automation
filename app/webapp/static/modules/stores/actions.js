// Prices: the plan and run actions — import the latest run, load the
// recommended plan, reset the picks, copy the refresh steps. The glance card
// and the Benchmark sheet both reach them through index.js.
import { showToast } from "../../_vendored/toast/toast.js";
import { fetchJson } from "../api.js";
import { copyText } from "../clipboard.js";
import { render, state } from "../core.js";
import { closeBenchmarkSheet, paintBenchmarkSheet } from "./benchmark.js";
import { scheduleSimulate } from "./simulator.js";
import { PICKS_KEY, local, setPick, storeName, writeStored } from "./state.js";

const REFRESH_STEPS = [
  "In Claude Code, in the grocery-shopping-automation repo, run /supermarket-benchmark (about an hour; it asks you to confirm the quality specs).",
  "Come back to Items > Prices, open Benchmark and tap \"Import latest run\".",
  "Review the \"Needs checking\" items in Prices.",
];

// The result stays on the Benchmark sheet, where the import was started.
export async function importLatest(button) {
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
    button.disabled = false;
    paintBenchmarkSheet();
  }
  render();
}

// Loading or dropping a plan closes the sheet: the glance card behind it
// shows the new total, and its one action becomes Review & apply.
export async function loadRecommended() {
  try {
    const body = await fetchJson("/api/stores/recommended");
    local.picks = {};
    for (const [id, store] of Object.entries(body.picks)) setPick(Number(id), store);
    writeStored(PICKS_KEY, local.picks);
    closeBenchmarkSheet();
    showToast(`Loaded the recommended plan: ${body.stores.map(storeName).join(" + ")}`);
  } catch (error) {
    showToast(error.message, "error");
    return;
  }
  render();
  scheduleSimulate(0);
}

export function resetPicks() {
  local.picks = {};
  writeStored(PICKS_KEY, local.picks);
  closeBenchmarkSheet();
  showToast("Back to the stores in your list");
  render();
  scheduleSimulate(0);
}

export async function copySteps() {
  const text = `How to refresh the supermarket prices:\n${REFRESH_STEPS.map((step, i) => `${i + 1}. ${step}`).join("\n")}`;
  const copied = await copyText(text);
  const status = document.querySelector("#stores-copy-status");
  if (!status) return;
  status.className = `panel-status ${copied ? "ok" : "error"}`;
  status.textContent = copied ? "Steps copied." : "Couldn't copy. Select the steps above and copy them by hand.";
}
