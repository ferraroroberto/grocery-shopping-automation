// Email watch (issue #73) — the Auto tab's second card.
//
// Server-side poller over the #72 confirmation-email check: the card selects
// which monitored senders (each mapped to a store) are active, sets the poll
// cadence, and shows the last-check log. "Test last email" re-processes the
// newest confirmation even if already seen — the end-to-end dry run.
import { fetchJson } from "./api.js";
import { activePaneBody } from "./core.js";
import { fmtBuildTime, html, switchMarkup, switchOn, text } from "./dom.js";

const EMAIL_INTERVALS = [
  [15, "Every 15 min"], [30, "Every 30 min"], [60, "Every hour"],
  [180, "Every 3 h"], [360, "Every 6 h"], [720, "Every 12 h"], [1440, "Daily"],
];

// The card shell the automation view interpolates. Folds by default; harvest
// the live open state so a re-render (e.g. automation dismiss) doesn't slam
// it shut. `refreshEmailMonitor` fills the body.
export function emailMonitorCard() {
  const open = !!activePaneBody().querySelector("#email-monitor[open]");
  return `<details id="email-monitor" class="card card--collapsible"${open ? " open" : ""}>
    <summary class="collapse-summary">
      <span class="collapse-main">
        <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-mail"></use></svg>
        <h3 class="collapse-title">Email Watch</h3>
        <span class="collapse-count" id="email-monitor-count"></span>
      </span>
      <span class="collapse-chevron" aria-hidden="true">›</span>
    </summary>
    <div class="collapse-body" id="email-monitor-body">
      <div class="panel-status">Loading email monitor…</div>
    </div>
  </details>`;
}

export async function refreshEmailMonitor() {
  const body = document.querySelector("#email-monitor-body");
  if (!body) return;
  let s;
  try {
    s = await fetchJson("/api/email-monitor/status");
  } catch (error) {
    body.innerHTML = `<div class="panel-status error">Email monitor unavailable: ${html(error.message)}</div>`;
    return;
  }
  const count = document.querySelector("#email-monitor-count");
  const activeSenders = s.senders.filter((x) => x.enabled).length;
  if (count) count.textContent = `${s.poller.enabled ? "on" : "off"} · ${activeSenders}/${s.senders.length} senders`;
  const senderRows = s.senders.length
    ? s.senders.map((sender) => `<div class="zone-item">
        <span>${html(sender.name || sender.address)}<span class="meta"> · ${html(sender.store || "no store")}</span></span>
        ${switchMarkup(sender.enabled, `Monitor ${text(sender.name || sender.address)}`, { "data-monitor-sender": sender.address })}
      </div>`).join("")
    : `<div class="panel-status">No senders configured — see config/gmail_config.sample.json.</div>`;
  const intervalOptions = EMAIL_INTERVALS.map(([mins, label]) =>
    `<option value="${mins}"${mins === s.poller.interval_minutes ? " selected" : ""}>${label}</option>`).join("");
  const checkRows = s.checks.length
    ? s.checks.map((entry) => `<div class="zone-item">
        <span>${html(entry.outcome)}<div class="meta">${fmtBuildTime(entry.ts)} · ${html(entry.store || "-")} · ${html(entry.trigger)}${entry.notified ? " · notified" : ""}</div></span>
      </div>`).join("")
    : "";
  // One flat switch-row list (dense-collection contract): the sender rows and
  // the poll toggle share the container so every switch pins right and the
  // rows divide on one hairline.
  body.innerHTML = `
    <div class="hint">Watches Gmail for store "order prepared" emails and alerts on Telegram when the confirmation drops an ordered item.</div>
    <div class="zone-items">${senderRows}
      <div class="zone-item"><span>Poll automatically</span>${switchMarkup(s.poller.enabled, "Poll automatically", { id: "email-poller-enabled" })}</div>
    </div>
    <label class="field-label">Frequency
      <select id="email-poller-interval">${intervalOptions}</select>
    </label>
    <div class="email-actions">
      <button id="email-check-now" class="secondary btn-block" type="button"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-refresh-cw"></use></svg>Check now</button>
      <button id="email-check-test" class="secondary btn-block" type="button"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-play"></use></svg>Test last email</button>
    </div>
    <div id="email-monitor-status" class="panel-status">${emailNextCheckText(s)}</div>
    ${checkRows ? `<div class="field-label">Last checks</div><div class="zone-items">${checkRows}</div>` : ""}`;
}

function emailNextCheckText(s) {
  if (!s.poller.enabled) return "Automatic polling is off.";
  if (!s.next_check_at) return "Next check: soon.";
  return `Next check: ${fmtBuildTime(s.next_check_at)}.`;
}

export async function pushEmailMonitorConfig() {
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
    await fetchJson("/api/email-monitor/config", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    await refreshEmailMonitor();
  } catch (error) {
    const status = document.querySelector("#email-monitor-status");
    if (status) {
      status.className = "panel-status error";
      status.textContent = `Could not save settings: ${error.message}`;
    }
  }
}

export async function runEmailCheck(force) {
  const status = document.querySelector("#email-monitor-status");
  const buttons = ["#email-check-now", "#email-check-test"]
    .map((sel) => document.querySelector(sel)).filter(Boolean);
  buttons.forEach((b) => { b.disabled = true; });
  if (status) {
    status.className = "panel-status";
    status.textContent = force
      ? "Re-checking the latest email end to end…"
      : "Checking mailbox…";
  }
  try {
    await fetchJson("/api/email-monitor/check", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ force }),
    });
    await refreshEmailMonitor();
  } catch (error) {
    if (status) {
      status.className = "panel-status error";
      status.textContent = `Check failed: ${error.message}`;
    }
    buttons.forEach((b) => { b.disabled = false; });
  }
}

// The switches/selects inside this card flip through app.js's delegation;
// this keeps the "which control belongs to email watch" test in one place.
export function isEmailControl(node) {
  return node.id === "email-poller-enabled"
    || node.id === "email-poller-interval"
    || node.dataset?.monitorSender !== undefined;
}
