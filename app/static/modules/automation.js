// Cart automation: the run panel (store/cart-mode/dry-run + live command
// preview), the elapsed timer, and the SSE log stream. Run state is local to
// this module — nothing else in the app reads it.
import { fetchJson } from "./api.js";
import { activePaneBody, state } from "./core.js";
import { formatElapsed, html, switchMarkup, switchOn } from "./dom.js";
import { emailMonitorCard, refreshEmailMonitor } from "./email.js";

const run = {
  source: null,
  startedAt: null,
  timer: null,
};

export function renderAutomation() {
  const stores = state.payload.summary.supermarkets;
  activePaneBody().innerHTML = `<section class="panel">
    <h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-bot"></use></svg>Run Automation</h2>
    <div class="hint">Fills the store carts from this list via Chrome automation. You still confirm and pay in the browser.</div>
    <div class="two">
      <label class="field-label">Store
        <select id="automation-store"><option value="all">All stores</option>${stores.map((s) => `<option value="${html(s)}">${html(s)}</option>`).join("")}</select>
      </label>
      <label class="field-label">Cart mode
        <select id="automation-cart-mode"><option value="keep">Keep cart</option><option value="clean">Clean cart</option></select>
      </label>
    </div>
    <div class="flag-row"><span>Dry run</span>${switchMarkup(false, "Dry run", { id: "automation-dry-run" })}</div>
    <div id="automation-clean-warn" class="panel-status error" hidden>Clean mode empties the store cart first — anything added by hand will be removed.</div>
    <div id="automation-clean-confirm-wrap" class="flag-row" hidden><span>Yes, empty the cart first</span>${switchMarkup(false, "Yes, empty the cart first", { id: "automation-clean-confirm" })}</div>
    <pre id="automation-command" class="log"></pre>
    <div class="actions">
      <button id="automation-start" class="big-btn btn-block" type="button"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-play"></use></svg>Run Automation</button>
      <button id="automation-stop" class="danger btn-block" type="button" hidden><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-square"></use></svg>Stop</button>
      <button id="automation-dismiss" class="secondary btn-block" type="button" hidden>Dismiss</button>
    </div>
    <div id="automation-elapsed" class="panel-status"></div>
    <pre id="automation-log" class="log">(not running)</pre>
  </section>
  ${emailMonitorCard()}`;
  updateAutomationCommand();
  refreshAutomation();
  refreshEmailMonitor();
}

// Mirror the Streamlit controls: clean-mode warning + destructive confirm, and a
// live command preview pulled from the backend so the argv never diverges.
export async function updateAutomationCommand() {
  const store = document.querySelector("#automation-store")?.value || "all";
  const cartMode = document.querySelector("#automation-cart-mode")?.value || "keep";
  const dryEl = document.querySelector("#automation-dry-run");
  const dryRun = dryEl ? dryEl.getAttribute("aria-checked") === "true" : true;
  const clean = cartMode === "clean";
  const warn = document.querySelector("#automation-clean-warn");
  const confirmWrap = document.querySelector("#automation-clean-confirm-wrap");
  if (warn) warn.hidden = !clean;
  if (confirmWrap) confirmWrap.hidden = !(clean && !dryRun);
  const start = document.querySelector("#automation-start");
  if (start) start.disabled = clean && !dryRun && !switchOn("#automation-clean-confirm");
  try {
    const r = await fetchJson(`/api/automation/command?store=${encodeURIComponent(store)}&dry_run=${dryRun}&cart_mode=${cartMode}`);
    const cmd = document.querySelector("#automation-command");
    if (cmd) cmd.textContent = r.command;
  } catch (_) {
    // preview is best-effort
  }
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
  const log = document.querySelector("#automation-log");
  if (log) log.textContent = status.lines?.length ? status.lines.join("\n") : (status.running ? "(waiting for output…)" : "(not running)");
  const finished = !status.running && status.returncode !== null && status.returncode !== undefined;
  const start = document.querySelector("#automation-start");
  const stop = document.querySelector("#automation-stop");
  const dismiss = document.querySelector("#automation-dismiss");
  if (start) start.hidden = status.running || finished;
  if (stop) stop.hidden = !status.running;
  if (dismiss) dismiss.hidden = !finished;
  const elapsed = document.querySelector("#automation-elapsed");
  if (elapsed && finished) {
    stopAutomationTimer();
    elapsed.className = `panel-status ${status.returncode === 0 ? "ok" : "error"}`;
    elapsed.textContent = status.returncode === 0
      ? "Automation finished — exit 0. Review and pay in the browser."
      : `Automation exited with code ${status.returncode}. See the log above.`;
  }
}

function startAutomationTimer() {
  if (run.timer) return;
  const tick = () => {
    const elapsed = document.querySelector("#automation-elapsed");
    if (!elapsed || !run.startedAt) return;
    elapsed.className = "panel-status";
    elapsed.textContent = `Automation running… (${formatElapsed(Math.floor((Date.now() - run.startedAt) / 1000))} elapsed)`;
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
  run.source = new EventSource("/api/automation/events");
  run.source.onmessage = async (event) => {
    const status = JSON.parse(event.data);
    applyAutomationStatus(status);
    if (!status.running) {
      run.source.close();
      run.source = null;
      const final = await fetchJson("/api/automation/status").catch(() => null);
      if (final) applyAutomationStatus(final);
    }
  };
}

export async function startAutomation() {
  run.startedAt = Date.now();
  const status = await fetchJson("/api/automation/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      store: document.querySelector("#automation-store").value,
      dry_run: switchOn("#automation-dry-run"),
      cart_mode: document.querySelector("#automation-cart-mode").value,
    }),
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
  renderAutomation();
}
