// Stores: the "Review & apply" dialog (#148) and the baseline confirm/reset (#183).
import { emptyStateEl } from "../../_vendored/empty-state/empty-state.js";
import { fetchJson } from "../api.js";
import { render, state } from "../core.js";
import { esc, icon } from "../dom.js";
import { dialogShell } from "./dialog.js";
import { loadChecks, scheduleSimulate } from "./simulator.js";
import { PICKS_KEY, activePicks, jsonInit, local, num, plural, storeName, writeStored } from "./state.js";

const FLAG_TEXT = {
  unit_mismatch: "Pack units differ, so the target and in-stock count were kept. Check them.",
  pack_unknown: "Pack size unknown, so the target and in-stock count were kept. Check them.",
};

export function openSetBaselineConfirm() {
  const dialog = dialogShell("stores-baseline-dialog", "Set as baseline", "Set baseline");
  const bodyEl = dialog.querySelector(".stores-dialog-body");
  const status = dialog.querySelector(".stores-dialog-status");
  const save = dialog.querySelector(".detail-save-btn");
  status.textContent = "";
  save.disabled = false;
  save.textContent = "Set baseline";
  const needs = local.checks?.counts?.needs_checking ?? 0;
  bodyEl.innerHTML = `<p>This freezes your current stores as the new "Today" for the Monthly cost what-if — the saving shown from here on is measured from this list, not the benchmarked run.</p>
    ${needs ? `<p class="hint">${plural(needs, "item")} still ${needs === 1 ? "needs" : "need"} checking in the review worklist.</p>` : ""}`;
  save.onclick = () => submitSetBaseline(dialog);
  dialog.showModal();
}

async function submitSetBaseline(dialog) {
  const save = dialog.querySelector(".detail-save-btn");
  const status = dialog.querySelector(".stores-dialog-status");
  save.disabled = true;
  status.textContent = "";
  try {
    await fetchJson("/api/stores/baseline", { method: "POST" });
    dialog.close();
    local.note = { kind: "ok", text: "Baseline set." };
    render();
    scheduleSimulate(0);
  } catch (error) {
    status.textContent = error.message;
    save.disabled = false;
  }
}

export async function resetBaseline() {
  try {
    await fetchJson("/api/stores/baseline", { method: "DELETE" });
    local.note = { kind: "ok", text: "Baseline reset to the benchmark." };
  } catch (error) {
    local.note = { kind: "error", text: error.message };
  }
  render();
  scheduleSimulate(0);
}

export async function openApplyReview() {
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
  bodyEl.innerHTML = `<p class="hint">Target and in-stock count are converted by pack size. Adjust any before applying.</p>
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
    <label class="row"><span>In stock <span class="meta">was ${change.tenemos_from}</span></span>
      <input class="input-native apply-stock" type="number" min="0" step="1" inputmode="numeric"
             value="${change.tenemos}" aria-label="New in-stock count for ${esc(change.comida)}"${blocked ? " disabled" : ""}></label>
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
    for (const [field, cls, label] of [["cantidad", ".apply-qty", "Target"], ["tenemos", ".apply-stock", "In stock"]]) {
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
