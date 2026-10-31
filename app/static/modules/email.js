// Email watch — a Settings row and its sheet.
//
// Server-side poller over the confirmation-email check: the sheet selects
// which monitored senders (each mapped to a store) are active, sets the poll
// cadence, and shows the last-check log. "Test last email" re-processes the
// newest confirmation even if already seen — the end-to-end dry run. Every
// change saves as it happens (design.md "settings group"); Done closes. The
// sheet lives at <body> level, outside .app, so it carries its own listeners.
import { setSwitch } from "../_vendored/switch/switch.js";
import { fetchJson, jsonInit } from "./api.js";
import { fmtBuildTime, html, icon, switchMarkup, switchOn, text } from "./dom.js";
import { dialogShell } from "./stores/dialog.js";

const SHEET_ID = "email-watch-sheet";
const EMAIL_INTERVALS = [
  [15, "Every 15 min"], [30, "Every 30 min"], [60, "Every hour"],
  [180, "Every 3 h"], [360, "Every 6 h"], [720, "Every 12 h"], [1440, "Daily"],
];

let status = null; // the last /api/email-monitor/status read
let readError = ""; // why the last read failed, if it did

// Read the status, then paint the Settings row's value and an open sheet.
export async function refreshEmailWatch() {
  try {
    status = await fetchJson("/api/email-monitor/status");
    readError = "";
  } catch (error) {
    readError = error.message;
  }
  paintValue();
  paintSheet();
}

function intervalLabel(minutes) {
  return EMAIL_INTERVALS.find(([mins]) => mins === minutes)?.[1] || `Every ${minutes} min`;
}

function paintValue() {
  const node = document.querySelector("#email-watch-value");
  if (!node) return;
  if (!status) {
    node.textContent = readError ? "Unavailable" : "";
    if (readError) node.dataset.tone = "attention";
    else delete node.dataset.tone;
    return;
  }
  delete node.dataset.tone;
  node.textContent = status.poller.enabled ? intervalLabel(status.poller.interval_minutes) : "Off";
}

function sheet() {
  return dialogShell(SHEET_ID, "Email watch", "Done", (shell) => {
    shell.classList.add("email-sheet");
    const done = shell.querySelector(".detail-save-btn");
    done.disabled = false;
    done.addEventListener("click", () => shell.close());
    shell.addEventListener("click", onClick);
    shell.addEventListener("change", (event) => {
      if (event.target.id === "email-poller-interval") pushEmailMonitorConfig();
    });
    shell.addEventListener("close", () => document.querySelector("#open-email-watch")?.focus());
  });
}

export function openEmailWatch() {
  const d = sheet();
  paintSheet();
  if (!d.open) d.showModal();
  refreshEmailWatch();
}

function paintSheet() {
  const body = document.getElementById(SHEET_ID)?.querySelector(".stores-dialog-body");
  if (!body) return;
  if (!status) {
    body.innerHTML = readError
      ? `<div class="panel-status error">Email watch unavailable: ${html(readError)}</div>`
      : `<div class="panel-status">Loading email watch…</div>`;
    return;
  }
  const s = status;
  const senderRows = s.senders.length
    ? s.senders.map((sender) => `<div class="zone-item">
        <span>${html(sender.name || sender.address)}<span class="meta"> · ${html(sender.store || "no store")}</span></span>
        ${switchMarkup(sender.enabled, `Monitor ${text(sender.name || sender.address)}`, { "data-monitor-sender": sender.address })}
      </div>`).join("")
    : `<div class="panel-status">No senders configured — see config/gmail_config.sample.json.</div>`;
  const intervalOptions = EMAIL_INTERVALS.map(([mins, label]) =>
    `<option value="${mins}"${mins === s.poller.interval_minutes ? " selected" : ""}>${label}</option>`).join("");
  const checkRows = s.checks.map((entry) => `<div class="zone-item">
      <span>${html(entry.outcome)}<div class="meta">${fmtBuildTime(entry.ts)} · ${html(entry.store || "-")} · ${html(entry.trigger)}${entry.notified ? " · notified" : ""}</div></span>
    </div>`).join("");
  // Flat switch-row lists (dense-collection contract): every switch pins
  // right and the rows divide on one hairline.
  body.innerHTML = `
    <p class="hint">Watches Gmail for store "order prepared" emails and alerts on Telegram when one drops an ordered item.</p>
    <section class="sheet-section" aria-labelledby="email-senders">
      <h3 class="overline" id="email-senders">Senders</h3>
      <div class="zone-items">${senderRows}</div>
    </section>
    <section class="sheet-section" aria-labelledby="email-polling">
      <h3 class="overline" id="email-polling">Polling</h3>
      <div class="zone-items">
        <div class="zone-item"><span>Poll automatically</span>${switchMarkup(s.poller.enabled, "Poll automatically", { id: "email-poller-enabled" })}</div>
      </div>
      <label class="field-label">Frequency
        <select id="email-poller-interval">${intervalOptions}</select>
      </label>
      <div id="email-monitor-status" class="panel-status">${emailNextCheckText(s)}</div>
    </section>
    <div class="email-actions">
      <button id="email-check-now" class="button-surface btn-block" type="button">${icon("refresh-cw")}Check now</button>
      <button id="email-check-test" class="button-surface btn-block" type="button">${icon("play")}Test last email</button>
    </div>
    ${checkRows ? `<section class="sheet-section" aria-labelledby="email-checks">
      <h3 class="overline" id="email-checks">Last checks</h3>
      <div class="zone-items">${checkRows}</div>
    </section>` : ""}`;
}

function emailNextCheckText(s) {
  if (!s.poller.enabled) return "Automatic polling is off.";
  if (!s.next_check_at) return "Next check: soon.";
  return `Next check: ${fmtBuildTime(s.next_check_at)}.`;
}

function onClick(event) {
  const sw = event.target.closest('.toggle[role="switch"]');
  if (sw) {
    setSwitch(sw, sw.getAttribute("aria-checked") !== "true");
    pushEmailMonitorConfig();
    return;
  }
  const id = event.target.closest("button")?.id;
  if (id === "email-check-now") runEmailCheck(false);
  if (id === "email-check-test") runEmailCheck(true);
}

async function pushEmailMonitorConfig() {
  const senders = [...document.querySelectorAll("[data-monitor-sender]")].map((sw) => ({
    address: sw.dataset.monitorSender,
    enabled: sw.getAttribute("aria-checked") === "true",
  }));
  const payload = {
    enabled: switchOn("#email-poller-enabled"),
    interval_minutes: Number(document.querySelector("#email-poller-interval")?.value || 60),
    senders,
  };
  try {
    await fetchJson("/api/email-monitor/config", jsonInit("PUT", payload));
    await refreshEmailWatch();
  } catch (error) {
    const line = document.querySelector("#email-monitor-status");
    if (line) {
      line.className = "panel-status error";
      line.textContent = `Could not save settings: ${error.message}`;
    }
  }
}

async function runEmailCheck(force) {
  const line = document.querySelector("#email-monitor-status");
  const buttons = ["#email-check-now", "#email-check-test"]
    .map((sel) => document.querySelector(sel)).filter(Boolean);
  buttons.forEach((b) => { b.disabled = true; });
  if (line) {
    line.className = "panel-status";
    line.textContent = force
      ? "Re-checking the latest email end to end…"
      : "Checking mailbox…";
  }
  try {
    await fetchJson("/api/email-monitor/check", jsonInit("POST", { force }));
    await refreshEmailWatch();
  } catch (error) {
    if (line) {
      line.className = "panel-status error";
      line.textContent = `Check failed: ${error.message}`;
    }
    buttons.forEach((b) => { b.disabled = false; });
  }
}
