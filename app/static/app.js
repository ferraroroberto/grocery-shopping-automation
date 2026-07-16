// Orchestrator: owns render() (mode → feature renderer), the delegated event
// listeners on .app, the top-bar controls, and boot. Every view lives in its
// own module under ./modules/ — add features there, not here.
import { initNavTabs } from "./_vendored/nav/nav-tabs.js";
import { setSwitch } from "./_vendored/switch/switch.js";
import { fetchJson, fetchVersion, loadInventory, mutate, onLoginSubmit } from "./modules/api.js";
import {
  applyAudio,
  cancelAudioRequest,
  clearAudio,
  matchTranscript,
  redoTranscribe,
  renderAudio,
  setAudioModel,
  toggleRecording,
} from "./modules/audio.js";
import {
  dismissAutomation,
  renderAutomation,
  startAutomation,
  stopAutomation,
  updateAutomationCommand,
} from "./modules/automation.js";
import {
  captureTokenFromURL,
  el,
  idleStatus,
  MODE_TO_TAB,
  restoreSubMode,
  saveShoppingState,
  saveSubMode,
  SEARCHABLE_MODES,
  setRenderer,
  setStatus,
  state,
  TAB_KEY,
  THEME_KEY,
} from "./modules/core.js";
import { isEmailControl, pushEmailMonitorConfig, runEmailCheck } from "./modules/email.js";
import { renderAdd, renderAudit, renderDashboard, renderEdit } from "./modules/inventory.js";
import {
  cancelProductSearch,
  handleSearchInput,
  renderSearch,
  startProductSearch,
  toggleCandidateConfirm,
  toggleSearchRecording,
  useCandidate,
} from "./modules/search.js";
import { renderShopping } from "./modules/shopping.js";

function render() {
  if (!state.payload) return;
  setStatus(idleStatus());
  // Re-home the (single) search node into the active pane, above its body but
  // BELOW the sub-mode pills — the pills stay pinned to the top and never
  // shift when the search appears/disappears across sub-modes.
  const pane = document.querySelector(`#pane-${MODE_TO_TAB[state.mode] || "inventory"}`);
  const paneBody = pane?.querySelector(".pane-body");
  if (paneBody && el.toolbar.parentElement !== pane) pane.insertBefore(el.toolbar, paneBody);
  el.toolbar.hidden = !SEARCHABLE_MODES.has(state.mode);
  el.app.querySelectorAll(".subnav [data-mode]").forEach((button) => button.classList.toggle("active", button.dataset.mode === state.mode));
  if (state.mode === "dashboard") renderDashboard();
  if (state.mode === "audit") renderAudit(true);
  if (state.mode === "targets") renderAudit(false);
  if (state.mode === "edit") renderEdit();
  if (state.mode === "add") renderAdd();
  if (state.mode === "shopping") renderShopping();
  if (state.mode === "automation") renderAutomation();
  if (state.mode === "audio") renderAudio();
  if (state.mode === "search") renderSearch();
}

setRenderer(render);

function onTabChange(tab) {
  if (MODE_TO_TAB[state.mode] !== tab) state.mode = restoreSubMode(tab);
  render();
}

// ------------------------------------------------------------------ theme
function currentTheme() {
  return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light";
}

function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  localStorage.setItem(THEME_KEY, theme);
  // The button holds both sprite glyphs; CSS shows the one for the *action*
  // keyed on html[data-theme] — no JS glyph swap.
  if (el.themeToggle) {
    el.themeToggle.title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  }
}

function toggleTheme() {
  applyTheme(currentTheme() === "dark" ? "light" : "dark");
}

// ------------------------------------------------------- event delegation
// Sub-mode pills (static markup in the audit/items panes).
el.app.addEventListener("click", (event) => {
  const button = event.target.closest(".subnav [data-mode]");
  if (!button) return;
  state.mode = button.dataset.mode;
  saveSubMode(state.mode);
  render();
});

// Vendored switches are rendered as markup strings, so their flips are
// delegated here — setSwitch is the one write path for class + aria-checked.
el.app.addEventListener("click", (event) => {
  const sw = event.target.closest('.toggle[role="switch"]');
  if (!sw) return;
  setSwitch(sw, sw.getAttribute("aria-checked") !== "true");
  if (sw.id === "automation-dry-run" || sw.id === "automation-clean-confirm") updateAutomationCommand();
  if (isEmailControl(sw)) pushEmailMonitorConfig();
});

el.app.addEventListener("click", async (event) => {
  const zoneButton = event.target.closest("[data-zone]");
  if (zoneButton) {
    state.zone = zoneButton.dataset.zone;
    render();
    return;
  }
  const action = event.target.closest("[data-action]")?.dataset.action;
  if (!action) return;
  const card = event.target.closest("[data-id]");
  const id = card ? Number(card.dataset.id) : null;
  if (action === "current-minus") await mutate(`/api/items/${id}/current-delta`, { delta: -1 });
  if (action === "current-plus") await mutate(`/api/items/${id}/current-delta`, { delta: 1 });
  if (action === "target-minus") await mutate(`/api/items/${id}/target-delta`, { delta: -1 });
  if (action === "target-plus") await mutate(`/api/items/${id}/target-delta`, { delta: 1 });
  if (action === "delete" && confirm("Delete this item?")) await mutate(`/api/items/${id}`, {}, "DELETE");
  if (action === "open-buy") window.open(event.target.dataset.url, "_blank", "noopener");
  if (action === "mark-buy") { state.shopping.bought.add(id); saveShoppingState(); render(); }
  if (action === "undo-buy") { state.shopping.bought.delete(id); saveShoppingState(); render(); }
  const extraCard = event.target.closest("[data-extra-id]");
  if (extraCard) {
    const store = extraCard.dataset.store;
    const extraId = Number(extraCard.dataset.extraId);
    state.shopping.extraBought[store] ||= [];
    const set = new Set(state.shopping.extraBought[store]);
    if (action === "remove-extra") state.shopping.extras[store] = (state.shopping.extras[store] || []).filter((x) => x.id !== extraId);
    if (action === "mark-extra") set.add(extraId);
    if (action === "undo-extra") set.delete(extraId);
    state.shopping.extraBought[store] = [...set];
    saveShoppingState();
    render();
  }
});

el.app.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (event.target.matches(".edit-form")) {
    const card = event.target.closest("[data-id]");
    const data = Object.fromEntries(new FormData(event.target).entries());
    data.cantidad = Number(data.cantidad);
    data.tenemos = Number(data.tenemos);
    await mutate(`/api/items/${Number(card.dataset.id)}`, data, "PUT");
  }
  if (event.target.matches("#add-form")) {
    const data = Object.fromEntries(new FormData(event.target).entries());
    data.cantidad = Number(data.cantidad);
    data.tenemos = Number(data.tenemos);
    await mutate("/api/items", data);
  }
  if (event.target.matches(".quick-add")) {
    const panel = event.target.closest("[data-store]");
    const store = panel.dataset.store;
    const data = Object.fromEntries(new FormData(event.target).entries());
    state.shopping.extras[store] ||= [];
    state.shopping.extras[store].push({ id: state.shopping.counter++, name: data.name, qty: Number(data.qty || 1) });
    saveShoppingState();
    render();
  }
});

el.app.addEventListener("change", (event) => {
  if (event.target.id === "audio-model") {
    setAudioModel(event.target.value);
    return;
  }
  // The dry-run / clean-confirm switches fire through the click delegation
  // above; only the two selects arrive here.
  if (["automation-store", "automation-cart-mode"].includes(event.target.id)) {
    updateAutomationCommand();
    return;
  }
  if (isEmailControl(event.target)) {
    pushEmailMonitorConfig();
    return;
  }
  const action = event.target.dataset.action;
  const panel = event.target.closest("[data-store]");
  if (!panel || !action) return;
  const store = panel.dataset.store;
  state.shopping.offsets[store] ||= { items: 0, units: 0 };
  if (action === "offset-items") state.shopping.offsets[store].items = Number(event.target.value || 0);
  if (action === "offset-units") state.shopping.offsets[store].units = Number(event.target.value || 0);
  saveShoppingState();
  render();
});

el.app.addEventListener("click", async (event) => {
  // Buttons carry inline sprite icons, so the click target can be the <svg>
  // — resolve the owning button before dispatching on id.
  const button = event.target.closest("button");
  const id = button?.id || "";
  if (id === "automation-start") await startAutomation();
  if (id === "automation-stop") await stopAutomation();
  if (id === "automation-dismiss") await dismissAutomation();
  if (id === "email-check-now") await runEmailCheck(false);
  if (id === "email-check-test") await runEmailCheck(true);
  if (id === "shopping-unmark-all") {
    state.shopping.bought.clear();
    state.shopping.extraBought = {};
    saveShoppingState();
    render();
  }
  if (id === "record-toggle") await toggleRecording(button);
  if (id === "audio-redo") await redoTranscribe();
  if (id === "match-transcript") await matchTranscript();
  if (id === "apply-audio") await applyAudio();
  if (id === "audio-clear") clearAudio();
  if (id === "audio-cancel") cancelAudioRequest();
  if (id === "search-record") await toggleSearchRecording(button);
  if (id === "search-run") await startProductSearch();
  if (id === "search-cancel") await cancelProductSearch();
  if (button?.dataset.action === "search-use") toggleCandidateConfirm(button.closest(".candidate"));
  if (button?.dataset.action === "search-confirm") await useCandidate(button.closest(".candidate"));
});

// Product-search term box: keep state in sync while typing; Enter runs the search.
// The confirm-row fields persist into the draft so a poll re-render keeps them.
el.app.addEventListener("input", (event) => handleSearchInput(event.target));
el.app.addEventListener("keydown", (event) => {
  if (event.target.id === "search-term" && event.key === "Enter") {
    event.preventDefault();
    startProductSearch();
  }
});

// ------------------------------------------------------- top-bar controls
el.search.addEventListener("input", () => { state.query = el.search.value.trim().toLowerCase(); render(); });
el.openSheet.addEventListener("click", () => fetchJson("/api/actions/open-spreadsheet", { method: "POST" }).then(() => setStatus("Spreadsheet opened")));
el.copyLink.addEventListener("click", async () => {
  const url = state.access?.cloudflare || state.access?.lan || window.location.href;
  await navigator.clipboard.writeText(url);
  setStatus("Link copied");
});
el.exportCsv.addEventListener("click", () => { window.location.href = "/api/export.csv"; });
el.closeApp.addEventListener("click", () => { if (confirm("Close the FastAPI app?")) fetchJson("/api/actions/close", { method: "POST" }); });

el.themeToggle.addEventListener("click", toggleTheme);
el.loginForm.addEventListener("submit", onLoginSubmit);
// Auth is mandatory — Esc must not dismiss the login dialog.
el.loginDialog.addEventListener("cancel", (event) => event.preventDefault());

// ------------------------------------------------------------------ boot
captureTokenFromURL();
applyTheme(currentTheme());
// The nav restores the persisted tab and fires onChange once at init
// (payload is still null there, so that first render() is a no-op — the
// restored tab paints when loadInventory() completes).
initNavTabs({
  storageKey: TAB_KEY,
  onChange: onTabChange,
  scrollResetSelector: ".app",
});
loadInventory();
fetchVersion();

// No manual refresh button: refetch when the PWA returns to the foreground.
// Only on the read-only list modes — a re-render on edit/add/audio would wipe
// in-progress form input or a pasted transcript.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  fetchVersion();
  if (["dashboard", "shopping", "audit", "targets"].includes(state.mode)) loadInventory();
});
