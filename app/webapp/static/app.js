// Orchestrator: owns render() (mode → feature renderer), the delegated event
// listeners on .app, the top-bar controls, and boot. Every view lives in its
// own module under ./modules/ — add features there, not here.
import { initNavTabs } from "./_vendored/nav/nav-tabs.js";
import { setSwitch } from "./_vendored/switch/switch.js";
import { bindTextSize } from "./_vendored/text-size/text-size.js";
import { initUsageLog } from "./_vendored/usage-log/usage-log.js";
import { fetchVersion, itemUrl, loadInventory, mutate, onLoginSubmit } from "./modules/api.js";
import {
  applyAudio,
  audioUnmatchedCount,
  cancelAudioRequest,
  matchTranscript,
  openTranscriptMenu,
  redoTranscribe,
  rememberAudioPick,
  renderAudio,
  toggleRecording,
} from "./modules/audio.js";
import {
  dismissAutomation,
  fillMissingCount,
  loadFills,
  markFillDone,
  openFillHistory,
  openFillOptions,
  renderFillCarts,
  startAutomation,
  stopAutomation,
  syncFillCarts,
  unseenFillMisses,
} from "./modules/automation.js";
import {
  c,
  captureTokenFromURL,
  el,
  migrateRetiredTab,
  MODE_TO_TAB,
  restoreSubMode,
  saveShoppingState,
  saveSubMode,
  SEARCHABLE_MODES,
  setContextLine,
  setHeaderUpdater,
  setRenderer,
  state,
  storedToken,
  TAB_KEY,
  THEME_KEY,
} from "./modules/core.js";
import { openAddSheet } from "./modules/add-sheet.js";
import { openEmailWatch } from "./modules/email.js";
import { homeContext, loadThisWeek, paintThisWeek, renderHome } from "./modules/home.js";
import { renderAudit, renderItems } from "./modules/inventory.js";
import { openItemSheet } from "./modules/item-sheet.js";
import { copyLink, exportCsv, openSpreadsheet, openStoreLogins, renderSettings } from "./modules/settings.js";
import { renderShopping, shoppingMissingLinkCount } from "./modules/shopping.js";
import { openStoreSheet } from "./modules/store-sheet.js";
import {
  loadStoresChecks,
  onStoresChange,
  onStoresClick,
  renderStores,
  repaintStoresList,
  storesNeedsChecking,
} from "./modules/stores/index.js";
import { loadMeta } from "./modules/stores/simulator.js";
import { itemById } from "./modules/stores/state.js";

// Page-header context lines (design.md "page header"): the exception
// that needs you, in its tone, else the plain fact — never the mode name the
// segmented control already shows. Home's names the last fill's misses, else
// when you last counted. The Shop tab also carries a badge while the
// last fill's misses are unseen. A late read that
// changes one of these also refills Home's This week rows.
function updatePageHeaderContexts() {
  if (!state.payload) return;
  const home = homeContext();
  setContextLine("#status", home.exceptions, home.fallback);
  paintThisWeek();
  const total = `${state.payload.summary.total_items} items`;
  setContextLine("#shop-context", [
    [fillMissingCount(), "not in cart", "danger"],
    [shoppingMissingLinkCount(), "no link", "attention"],
  ], `${state.payload.summary.shopping_items} to buy`);
  nav?.setBadge("shopping", unseenFillMisses(), "not in cart");
  setContextLine("#audit-context", [[audioUnmatchedCount(), "unmatched", "attention"]], total);
  setContextLine("#items-context", [[storesNeedsChecking(), ["needs checking", "need checking"], "attention"]], total);
}

setHeaderUpdater(updatePageHeaderContexts);

function render() {
  if (!state.payload) return;
  updatePageHeaderContexts();
  // Re-home the (single) search node into the active pane, above its body but
  // BELOW the segmented control — the control stays pinned to the top and
  // never shifts when the search appears/disappears across modes.
  const pane = document.querySelector(`#pane-${MODE_TO_TAB[state.mode] || "inventory"}`);
  const paneBody = pane?.querySelector(".pane-body");
  if (paneBody && el.toolbar.parentElement !== pane) pane.insertBefore(el.toolbar, paneBody);
  el.toolbar.hidden = !SEARCHABLE_MODES.has(state.mode);
  el.itemsAdd.hidden = state.mode !== "list";
  // Audit's and Items' modes are segmented controls: aria-pressed is the state.
  el.app.querySelectorAll(".subnav [data-mode]").forEach((button) => {
    button.setAttribute("aria-pressed", String(button.dataset.mode === state.mode));
  });
  if (state.mode === "dashboard") renderHome();
  if (state.mode === "audit") renderAudit();
  if (state.mode === "list") renderItems();
  if (state.mode === "shopping") { renderShopping(); renderFillCarts(); }
  if (state.mode === "audio") renderAudio();
  if (state.mode === "settings") renderSettings();
  if (state.mode === "stores") {
    // A full render() (as opposed to the search-only repaintStoresList() used
    // for keystrokes) rebuilds the pane body from scratch, detaching
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
// Settings is never a tab (design.md "Navigation"): every pane's header
// carries a gear that opens #pane-settings over whichever tab is showing. The
// nav only knows its own tab panes, so opening hides them here and leaves no
// tab selected; a tap on any tab (or on the gear again) comes back.
const settingsPane = document.querySelector("#pane-settings");
let nav = null;
let settingsReturnTab = "inventory";

// Local usage log (vendored usage-log): which views and `data-track`
// controls get used, on which device class — posted to this app's own origin
// only. The API sits behind the bearer token off loopback, so the token rides
// as a header (never a wrapped fetch). Nothing but constant ids is recorded.
const usage = initUsageLog({
  headers: () => {
    const token = storedToken();
    return token ? { Authorization: `Bearer ${token}` } : {};
  },
});

// A segment is its own view id (the tab id alone would say "Audit" for both
// Voice and Manual): <tab>:<segment>, constants — never derived from data.
const SEGMENT_VIEW = { audio: "audit:voice", audit: "audit:manual", list: "items:list", stores: "items:prices" };

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
  usage.trackView("settings");
  render();
}

function closeSettings() {
  nav.setTab(settingsReturnTab);
}

// Voice's "Needs you" → Manual: a mention no item matched opens Manual
// searching every zone for what was heard, where one stepper tap fixes it.
// The match results wait on Voice, as they were left.
function findInManual(phrase) {
  state.mode = "audit";
  saveSubMode(state.mode);
  el.search.value = phrase;
  state.query = phrase.trim().toLowerCase();
  render();
  el.app.scrollTop = 0;
  window.scrollTo(0, 0);
}

function onTabChange(tab) {
  usage.trackView(tab);
  // Leaving Settings: its pane is not a nav pane, so the nav never hides it.
  settingsPane.hidden = true;
  if (MODE_TO_TAB[state.mode] !== tab) state.mode = restoreSubMode(tab);
  // The carts glance paints once (render → renderFillCarts, which fetches the
  // run status itself) and then persists; entering Shop re-reads the last
  // fill and resyncs an already painted glance. Before render(), so a first
  // paint isn't fetched twice.
  if (tab === "shopping") syncFillCarts();
  // Items' header names the items that need checking, whichever mode opens.
  if (tab === "items") loadStoresChecks();
  // Home's This week rows: the last audit and the next price review.
  if (tab === "inventory") loadThisWeek();
  render();
}

// A This week row opens the tab that owns its fact, on the segment
// that shows it: the Audit row on Voice, the Prices row on Prices.
function openWeekRow(mode) {
  saveSubMode(mode);
  state.mode = mode;
  nav.setTab(MODE_TO_TAB[mode]);
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
  // carries its own toggle, so update all of them, not just one.
  const title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  document.querySelectorAll(".theme-toggle").forEach((button) => { button.title = title; });
}

function toggleTheme() {
  applyTheme(currentTheme() === "dark" ? "light" : "dark");
}

// ------------------------------------------------------- event delegation
// Mode segments (static markup in the audit/items panes).
el.app.addEventListener("click", (event) => {
  const button = event.target.closest(".subnav [data-mode]");
  if (!button) return;
  state.mode = button.dataset.mode;
  saveSubMode(state.mode);
  usage.trackView(SEGMENT_VIEW[state.mode]);
  render();
});

// Settings gear: one per pane's page header, always beside the theme toggle.
el.app.addEventListener("click", (event) => {
  if (!event.target.closest(".home-settings")) return;
  if (state.mode === "settings") closeSettings();
  else openSettings();
});

// Theme toggle: one per pane's page header, so the id lives only
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
  rememberAudioPick(sw);
});

// Voice's typed counts and transcript outlive a hop to Manual (audio.js picks).
el.app.addEventListener("input", (event) => rememberAudioPick(event.target));

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
  const url = (path) => itemUrl(id, itemById(id)?.[c().comida], path);
  if (action === "current-minus") await mutate(url("/current-delta"), { delta: -1 });
  if (action === "current-plus") await mutate(url("/current-delta"), { delta: 1 });
  // A row tap opens the item sheet, wherever the row is; a Prices row
  // opens it on Stores & prices.
  if (action === "open-item") await openItemSheet(id, event.target.closest("[data-sheet-page]")?.dataset.sheetPage);
  if (action === "audio-find") findInManual(event.target.closest("[data-phrase]").dataset.phrase);
  if (action === "mark-buy") { state.shopping.bought.add(id); saveShoppingState(); render(); }
  if (action === "undo-buy") { state.shopping.bought.delete(id); saveShoppingState(); render(); }
  // A quick-add's Complete; its Remove lives in the store sheet.
  const extraCard = event.target.closest("[data-extra-id]");
  if (extraCard) {
    const store = extraCard.dataset.store;
    const extraId = Number(extraCard.dataset.extraId);
    state.shopping.extraBought[store] ||= [];
    const set = new Set(state.shopping.extraBought[store]);
    if (action === "mark-extra") set.add(extraId);
    if (action === "undo-extra") set.delete(extraId);
    state.shopping.extraBought[store] = [...set];
    saveShoppingState();
    render();
  }
});

el.app.addEventListener("submit", async (event) => {
  event.preventDefault();
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

el.app.addEventListener("click", async (event) => {
  // Buttons carry inline sprite icons, so the click target can be the <svg>
  // — resolve the owning button before dispatching on id.
  const button = event.target.closest("button");
  const id = button?.id || "";
  if (id === "automation-start") await startAutomation();
  if (id === "automation-stop") await stopAutomation();
  if (id === "automation-dismiss") await dismissAutomation();
  if (id === "automation-options") openFillOptions();
  if (id === "automation-history") openFillHistory();
  if (id === "automation-mark-done") await markFillDone();
  if (button?.matches("[data-store-sheet]")) openStoreSheet(button.closest("[data-store]").dataset.store);
  if (id === "open-store-logins") openStoreLogins();
  if (id === "open-email-watch") openEmailWatch();
  if (id === "open-sheet") openSpreadsheet();
  if (id === "copy-link") await copyLink();
  if (id === "export-csv") exportCsv();
  if (id === "record-toggle") await toggleRecording(button);
  if (id === "audio-redo") await redoTranscribe();
  if (id === "match-transcript") await matchTranscript();
  if (id === "apply-audio") await applyAudio();
  if (id === "audio-menu") openTranscriptMenu();
  if (id === "audio-cancel") cancelAudioRequest();
  if (id === "items-add") openAddSheet();
  if (button?.matches("[data-goto]")) openWeekRow(button.dataset.goto);
});

// Items → Prices: the view owns its data-stores-* hooks (store_links).
el.app.addEventListener("click", onStoresClick);
el.app.addEventListener("change", onStoresChange);

// ------------------------------------------------------- top-bar controls
el.search.addEventListener("input", () => {
  state.query = el.search.value.trim().toLowerCase();
  // Stores mode repaints just the list — a full render() rebuilds the
  // pane and re-homes the toolbar, which drops focus mid-keystroke. Every
  // other mode keeps the old full-render behaviour.
  if (state.mode === "stores") repaintStoresList();
  else render();
});
el.loginForm.addEventListener("submit", onLoginSubmit);
// Auth is mandatory — Esc must not dismiss the login dialog.
el.loginDialog.addEventListener("cancel", (event) => event.preventDefault());

// ------------------------------------------------------------------ boot
captureTokenFromURL();
applyTheme(currentTheme());
// A saved Search/Auto tab (retired) is rewritten to its new home
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
// The last fill feeds Shop's header and its tab badge from whichever tab
// opens; a Shop that opens has already asked (onTabChange → syncFillCarts).
if (nav.getTab() !== "shopping") loadFills();
fetchVersion();
// The Stores registry (display names, cart-handler flags) is read fleet-wide
// for store labels (automation.js, fallback.js, item-sheet.js, search.js) —
// loaded here so Home/Shop show real names before Items → Prices is ever
// opened; renderStores()/loadPricesReview() skip the reload once it is ready.
loadMeta();

// No manual refresh button: refetch when the PWA returns to the foreground.
// Only on the read-only list modes — a re-render on Voice would wipe a pasted
// transcript. The item and Add item sheets sit outside the panes, so a
// refetch behind them never touches what they staged.
document.addEventListener("visibilitychange", () => {
  if (document.visibilityState !== "visible") return;
  fetchVersion();
  if (["dashboard", "shopping", "audit", "list"].includes(state.mode)) loadInventory();
  if (state.mode === "dashboard") loadThisWeek();
  if (state.mode === "shopping") syncFillCarts();
  else loadFills(); // a fill finished elsewhere: Shop's badge
});
