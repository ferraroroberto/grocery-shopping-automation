// Orchestrator: owns render() (mode → feature renderer), the delegated event
// listeners on .app, the top-bar controls, and boot. Every view lives in its
// own module under ./modules/ — add features there, not here.
import { initNavTabs } from "./_vendored/nav/nav-tabs.js";
import { setSwitch } from "./_vendored/switch/switch.js";
import { bindTextSize } from "./_vendored/text-size/text-size.js";
import { showToast } from "./_vendored/toast/toast.js";
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
  renderFillCarts,
  startAutomation,
  stopAutomation,
  syncFillCarts,
  updateAutomationCommand,
} from "./modules/automation.js";
import {
  captureTokenFromURL,
  el,
  idleStatus,
  migrateRetiredTab,
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
import { isEmailControl, pushEmailMonitorConfig, renderEmailWatch, runEmailCheck } from "./modules/email.js";
import { renderAdd, renderAudit, renderDashboard, renderEdit } from "./modules/inventory.js";
import {
  cancelProductSearch,
  handleSearchInput,
  markPrefillAdded,
  renderSearch,
  startProductSearch,
  toggleSearchRecording,
  useCandidate,
  useCandidateClick,
} from "./modules/search.js";
import { renderShopping, shoppingStoreCount } from "./modules/shopping.js";
import { onStoresChange, onStoresClick, renderStores, repaintStoresList } from "./modules/stores.js";

// Page-header context lines for the four non-Home panes (#153 J-04) — Home's
// own #status is driven by idleStatus()/setStatus() below, unchanged. Each
// falls back to its tab's default submode label so the line is never stale
// even before that tab has been opened once.
const AUDIT_CONTEXT = { audit: "Manual audit", audio: "Audio audit" };
const ITEMS_CONTEXT = { targets: "Targets", edit: "Edit item", add: "Add item", stores: "Stores" };

function updatePageHeaderContexts() {
  const shopContext = document.querySelector("#shop-context");
  if (shopContext) shopContext.textContent = `${state.payload.summary.shopping_items} to buy · ${shoppingStoreCount()} stores`;
  const auditContext = document.querySelector("#audit-context");
  if (auditContext) auditContext.textContent = AUDIT_CONTEXT[state.mode] || AUDIT_CONTEXT.audit;
  const itemsContext = document.querySelector("#items-context");
  if (itemsContext) itemsContext.textContent = ITEMS_CONTEXT[state.mode] || ITEMS_CONTEXT.targets;
}

function render() {
  if (!state.payload) return;
  setStatus(idleStatus());
  updatePageHeaderContexts();
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
  if (state.mode === "add") { renderAdd(); renderSearch(); }
  if (state.mode === "shopping") { renderShopping(); renderFillCarts(); }
  if (state.mode === "audio") renderAudio();
  if (state.mode === "settings") renderEmailWatch();
  if (state.mode === "stores") {
    // A full render() (as opposed to the search-only repaintStoresList() used
    // for keystrokes, #193) rebuilds the pane body from scratch, detaching
    // the toolbar from its slot — capture focus/caret first so it can be
    // restored below if the search was mid-edit.
    const hadFocus = document.activeElement === el.search;
    const selStart = el.search.selectionStart;
    const selEnd = el.search.selectionEnd;
    renderStores();
    // LAYOUT-02: the Stores list needs its own search/filter input inside the
    // list's card (the measurement looks for input[type=search] within the
    // list's nearest `section` ancestor) — re-home the one global toolbar
    // search into the freshly-rendered card instead of duplicating a second
    // search box. renderStores() rebuilds the pane-body from scratch on every
    // call, so the slot is a fresh node each time and never already holds the
    // toolbar — the guard still protects a future renderStores() that stops
    // wiping the slot from silently duplicating the search box.
    const slot = document.querySelector("#stores-search-slot");
    if (slot && !slot.contains(el.toolbar)) {
      slot.appendChild(el.toolbar);
      if (hadFocus) {
        el.search.focus();
        try {
          el.search.setSelectionRange(selStart, selEnd);
        } catch (_) {
          // Some input types/browsers reject setSelectionRange — focus alone
          // still saves the keystroke.
        }
      }
    }
  }
}

setRenderer(render);

// ---------------------------------------------------------------- settings
// Settings is never a tab (design.md "Navigation", #200): every pane's header
// carries a gear that opens #pane-settings over whichever tab is showing. The
// nav only knows its own tab panes, so opening hides them here and leaves no
// tab selected; a tap on any tab (or on the gear again) comes back.
const settingsPane = document.querySelector("#pane-settings");
let nav = null;
let settingsReturnTab = "inventory";

function openSettings() {
  settingsReturnTab = nav.getTab();
  state.mode = "settings";
  el.app.querySelectorAll(":scope > .pane").forEach((pane) => { pane.hidden = pane !== settingsPane; });
  document.querySelectorAll(".tabs .tab").forEach((tab) => {
    tab.classList.remove("active");
    tab.setAttribute("aria-selected", "false");
  });
  el.app.scrollTop = 0;
  window.scrollTo(0, 0);
  render();
}

function closeSettings() {
  nav.setTab(settingsReturnTab);
}

function onTabChange(tab) {
  // Leaving Settings: its pane is not a nav pane, so the nav never hides it.
  settingsPane.hidden = true;
  if (MODE_TO_TAB[state.mode] !== tab) state.mode = restoreSubMode(tab);
  // Fill carts paints once (render → renderFillCarts, which fetches the run
  // status itself) and then persists; re-entering Shop resyncs an already
  // painted section. Before render(), so a first paint isn't fetched twice.
  if (tab === "shopping") syncFillCarts();
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
  // keyed on html[data-theme] — no JS glyph swap. Every pane's page header
  // carries its own toggle (#153 J-04), so update all of them, not just one.
  const title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  document.querySelectorAll(".theme-toggle").forEach((button) => { button.title = title; });
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

// Settings gear: one per pane's page header, always beside the theme toggle.
el.app.addEventListener("click", (event) => {
  if (!event.target.closest(".home-settings")) return;
  if (state.mode === "settings") closeSettings();
  else openSettings();
});

// Theme toggle: one per pane's page header (#153 J-04), so the id lives only
// on Home's for back-compat — delegate on the shared class instead.
el.app.addEventListener("click", (event) => {
  if (event.target.closest(".theme-toggle")) toggleTheme();
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
    markPrefillAdded(data.buscador); // a product-search pick that filled this form → Added
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
  if (button?.dataset.action === "search-use") useCandidateClick(button.closest(".candidate"));
  if (button?.dataset.action === "search-confirm") await useCandidate(button.closest(".candidate"));
});

// Items → Stores: the view owns its data-stores-* hooks (store_links, #148).
el.app.addEventListener("click", onStoresClick);
el.app.addEventListener("change", onStoresChange);

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
el.search.addEventListener("input", () => {
  state.query = el.search.value.trim().toLowerCase();
  // Stores mode repaints just the list (#193) — a full render() rebuilds the
  // pane and re-homes the toolbar, which drops focus mid-keystroke. Every
  // other mode keeps the old full-render behaviour.
  if (state.mode === "stores") repaintStoresList();
  else render();
});
el.openSheet.addEventListener("click", () => fetchJson("/api/actions/open-spreadsheet", { method: "POST" }).then(() => showToast("Spreadsheet opened")));
el.copyLink.addEventListener("click", async () => {
  const url = state.access?.cloudflare || state.access?.lan || window.location.href;
  await navigator.clipboard.writeText(url);
  showToast("Link copied");
});
el.exportCsv.addEventListener("click", () => { window.location.href = "/api/export.csv"; });
el.bootstrapSession.addEventListener("click", () => {
  fetchJson("/api/actions/bootstrap-session", { method: "POST" })
    .then(() => showToast("Chrome window opened — log into each store, then close it completely."))
    .catch((error) => showToast(error.message, "error"));
});

el.loginForm.addEventListener("submit", onLoginSubmit);
// Auth is mandatory — Esc must not dismiss the login dialog.
el.loginDialog.addEventListener("cancel", (event) => event.preventDefault());

// ------------------------------------------------------------------ boot
captureTokenFromURL();
applyTheme(currentTheme());
// A saved Search/Auto tab (retired in #182) is rewritten to its new home
// before the nav reads it — otherwise the nav would fall back to Home.
const retiredReveal = migrateRetiredTab();
// The nav restores the persisted tab and fires onChange once at init
// (payload is still null there, so that first render() is a no-op — the
// restored tab paints when loadInventory() completes).
nav = initNavTabs({
  storageKey: TAB_KEY,
  onChange: onTabChange,
  scrollResetSelector: ".app",
});
bindTextSize(document.querySelector("#textSizeControl"), "grocery");
loadInventory().then(() => {
  if (retiredReveal) document.querySelector(retiredReveal)?.scrollIntoView({ block: "start" });
});
fetchVersion();

// No manual refresh button: refetch when the PWA returns to the foreground.
// Only on the read-only list modes — a re-render on edit/add/audio would wipe
// in-progress form input or a pasted transcript.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  fetchVersion();
  if (["dashboard", "shopping", "audit", "targets"].includes(state.mode)) loadInventory();
  if (state.mode === "shopping") syncFillCarts();
});
