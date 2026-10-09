// Cart automation — the "Fill carts" section at the bottom of Shop (#182; it
// was the Auto tab): store/cart-mode/dry-run controls, the command preview
// (folded), the elapsed timer, and the SSE log stream. Run state is local to
// this module — nothing else in the app reads it.
import { fetchJson } from "./api.js";
import { state, storedToken } from "./core.js";
import { formatElapsed, html, icon, switchMarkup, switchOn } from "./dom.js";

const run = {
  source: null,
  startedAt: null,
  timer: null,
};

const IDLE_LOG = "Not running. Press Run automation to fill the store carts; progress appears here.";

function host() {
  return document.querySelector("#fill-carts-host");
}

// Paints the section once and then leaves it alone: Shop re-renders its list
// on every Got-it tap, and that must not reset the picks or the live log.
// Re-entering Shop resyncs the run status via syncFillCarts() instead.
export function renderFillCarts({ force = false } = {}) {
  const node = host();
  if (!node || (node.firstElementChild && !force)) return;
  const stores = state.payload.summary.supermarkets;
  node.innerHTML = `<section id="fill-carts" class="panel" aria-labelledby="fill-carts-title">
    <h2 id="fill-carts-title" class="card-title">${icon("bot")}Fill carts</h2>
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
    <details class="inline-disclosure">
      <summary class="inline-disclosure-summary"><span>What will run</span><span class="inline-chevron" aria-hidden="true">›</span></summary>
      <pre id="automation-command" class="log"></pre>
    </details>
    <div class="actions">
      <button id="automation-start" class="big-btn btn-block" type="button">${icon("play")}Run automation</button>
      <button id="automation-stop" class="danger btn-block" type="button" hidden>${icon("square")}Stop</button>
      <button id="automation-dismiss" class="secondary btn-block" type="button" hidden>Dismiss</button>
    </div>
    <div id="automation-elapsed" class="panel-status"></div>
    <pre id="automation-summary" class="log" hidden></pre>
    <pre id="automation-log" class="log">${IDLE_LOG}</pre>
  </section>`;
  updateAutomationCommand();
  refreshAutomation();
}

// Tab (re-)entry / foreground: pick up a run that started, finished or lost
// its event stream while Shop was out of view. No-op until first painted.
export function syncFillCarts() {
  if (host()?.firstElementChild && !run.source) refreshAutomation();
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
  if (elapsed && finished) {
    stopAutomationTimer();
    const [tone, text] = RUN_OUTCOME[status.returncode]
      || ["error", `Automation exited with code ${status.returncode}. See the log below.`];
    elapsed.className = `panel-status ${tone}`;
    elapsed.textContent = text;
    // The run's own end-of-run summary (#247), lifted out of the long log so a
    // miss is in front of the user, not scrolled away.
    const lines = status.lines || [];
    const start = lines.lastIndexOf(SUMMARY_HEADER);
    if (summary && start >= 0) {
      summary.textContent = lines.slice(start).join("\n");
      summary.hidden = false;
    }
  }
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
  renderFillCarts({ force: true });
}
