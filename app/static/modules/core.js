// Shared app state, cached shell nodes, and the selectors/persistence every
// feature module leans on.
//
// Feature-local state does NOT live here — the audio, search and automation
// modules each own theirs. What is left is genuinely app-wide: the loaded
// inventory payload, the active mode/zone/query, and the shopping progress
// (read by both the shopping view and the dashboard's store cards).
import { text } from "./dom.js";

export const TOKEN_KEY = "grocery.authToken";
export const SHOP_STATE_KEY = "grocery.shoppingState";
export const TAB_KEY = "grocery.tab";
export const SUB_KEY_PREFIX = "grocery.sub.";
export const THEME_KEY = "grocery.theme";

// Modes whose content the search box filters — it hides everywhere else.
export const SEARCHABLE_MODES = new Set(["dashboard", "audit", "targets", "edit"]);

// The 8 modes group into the fleet nav's 5 tabs; audit/items tabs re-home
// their modes as sub-pills (static markup in index.html).
export const MODE_TO_TAB = {
  dashboard: "inventory",
  shopping: "shopping",
  audit: "audit",
  audio: "audit",
  targets: "items",
  edit: "items",
  add: "items",
  search: "search",
  automation: "automation",
  settings: "settings",
};
export const TAB_DEFAULT_MODE = {
  inventory: "dashboard",
  shopping: "shopping",
  audit: "audit",
  items: "targets",
  search: "search",
  automation: "automation",
  settings: "settings",
};

export const state = {
  payload: null,
  access: null,
  mode: "dashboard",
  query: "",
  zone: "",
  shopping: loadShoppingState(),
};

export const el = {
  themeToggle: document.querySelector("#theme-toggle"),
  status: document.querySelector("#status"),
  search: document.querySelector("#search"),
  toolbar: document.querySelector("#toolbar"),
  buildReadout: document.querySelector("#build-readout"),
  app: document.querySelector("main.app"),
  openSheet: document.querySelector("#open-sheet"),
  copyLink: document.querySelector("#copy-link"),
  exportCsv: document.querySelector("#export-csv"),
  closeApp: document.querySelector("#close-app"),
  loginDialog: document.querySelector("#login-dialog"),
  loginForm: document.querySelector("#login-form"),
  loginPassword: document.querySelector("#login-password"),
  loginError: document.querySelector("#login-error"),
};

// The orchestrating app.js owns the real render(); feature modules call it
// through this indirection instead of importing app.js, so the module graph
// stays a DAG (features → core, app → features) with no import cycle.
let renderImpl = () => {};

export function setRenderer(fn) {
  renderImpl = fn;
}

export function render() {
  renderImpl();
}

// The pane body a mode renders into (each tab's <section class="pane"> holds
// one .pane-body; audit/items also carry a static sub-pill row above it).
export function activePaneBody() {
  const tab = MODE_TO_TAB[state.mode] || "inventory";
  return document.querySelector(`#pane-${tab} .pane-body`);
}

export function setStatus(message) {
  el.status.textContent = message;
}

// Resting top-bar text: the item count on Home, silence elsewhere (the same
// "Loaded 160 items" on every tab read as clutter). Transient action feedback
// (Saving… / Saved / errors) still writes over it via setStatus.
export function idleStatus() {
  return state.mode === "dashboard" && state.payload
    ? `Loaded ${state.payload.summary.total_items} items`
    : "";
}

export function c() {
  return state.payload?.columns ?? {};
}

export function items() {
  return state.payload?.items ?? [];
}

export function filteredItems(source = items()) {
  if (!state.query) return source;
  const cols = c();
  return source.filter((item) =>
    [item[cols.comida], item[cols.lugar], item[cols.super]]
      .map(text)
      .join(" ")
      .toLowerCase()
      .includes(state.query),
  );
}

export function defaultZone() {
  const cols = c();
  const withTargets = items().find((item) => Number(item[cols.cantidad]) > 0);
  return withTargets?.[cols.lugar] || state.payload.summary.zones[0] || "";
}

export function loadShoppingState() {
  try {
    const raw = JSON.parse(localStorage.getItem(SHOP_STATE_KEY) || "{}");
    return {
      bought: new Set(raw.bought || []),
      extras: raw.extras || {},
      extraBought: raw.extraBought || {},
      offsets: raw.offsets || {},
      counter: raw.counter || 1,
    };
  } catch (_) {
    return { bought: new Set(), extras: {}, extraBought: {}, offsets: {}, counter: 1 };
  }
}

export function saveShoppingState() {
  localStorage.setItem(
    SHOP_STATE_KEY,
    JSON.stringify({
      bought: [...state.shopping.bought],
      extras: state.shopping.extras,
      extraBought: state.shopping.extraBought,
      offsets: state.shopping.offsets,
      counter: state.shopping.counter,
    }),
  );
}

export function captureTokenFromURL() {
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");
  if (!token) return;
  localStorage.setItem(TOKEN_KEY, token);
  url.searchParams.delete("token");
  window.history.replaceState({}, "", url.toString());
}

export function storedToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

// Sub-mode persistence: each grouped tab remembers which of its modes was last
// active, so the PWA reopens exactly where you left it (tab via the nav
// component's own storage, sub-mode via grocery.sub.<tab>).
export function restoreSubMode(tab) {
  const fallback = TAB_DEFAULT_MODE[tab] || "dashboard";
  let stored = null;
  try {
    stored = localStorage.getItem(SUB_KEY_PREFIX + tab);
  } catch (_) {
    return fallback;
  }
  return stored && MODE_TO_TAB[stored] === tab ? stored : fallback;
}

export function saveSubMode(mode) {
  const tab = MODE_TO_TAB[mode];
  if (!tab || TAB_DEFAULT_MODE[tab] === undefined) return;
  try {
    localStorage.setItem(SUB_KEY_PREFIX + tab, mode);
  } catch (_) {
    // private mode — persistence is best-effort
  }
}
