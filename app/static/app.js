import { initNavTabs } from "./_vendored/nav/nav-tabs.js";
import { setSwitch, switchEl } from "./_vendored/switch/switch.js";
import { emptyStateEl } from "./_vendored/empty-state/empty-state.js";

const TOKEN_KEY = "grocery.authToken";
const SHOP_STATE_KEY = "grocery.shoppingState";
const TAB_KEY = "grocery.tab";
const SUB_KEY_PREFIX = "grocery.sub.";

// Modes whose content the search box filters — it hides everywhere else.
const SEARCHABLE_MODES = new Set(["dashboard", "audit", "targets", "edit"]);

// The 8 modes group into the fleet nav's 5 tabs; audit/items tabs re-home
// their modes as sub-pills (static markup in index.html).
const MODE_TO_TAB = {
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
const TAB_DEFAULT_MODE = {
  inventory: "dashboard",
  shopping: "shopping",
  audit: "audit",
  items: "targets",
  search: "search",
  automation: "automation",
  settings: "settings",
};

const THEME_KEY = "grocery.theme";

const state = {
  payload: null,
  access: null,
  mode: "dashboard",
  query: "",
  zone: "",
  automationSource: null,
  automationStarted: null,
  automationTimer: null,
  mediaRecorder: null,
  audioMime: "",
  transcript: "",
  audioModel: "",
  audioHealth: null,
  audioSha: "",
  audioBytes: 0,
  // Hardened recording via the voice-transcriber session API (issue #30).
  audioStream: null,
  sessionId: "",
  uploadChain: Promise.resolve(),
  pendingUploads: 0,
  bytesSent: 0,
  recordStartedAt: 0,
  recordTimer: null,
  audioEventSource: null,
  matches: null,
  shopping: loadShoppingState(),
  // On-demand product search (issue #87). `items` holds the merged status
  // entries (one per searched term, each with its candidate cards).
  search: {
    term: "", running: false, items: [], error: "", startedAt: 0,
    pollTimer: null, recorder: null, chunks: [], recording: false, notice: "",
    resolved: {}, progress: "",
  },
};

const el = {
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

// The pane body a mode renders into (each tab's <section class="pane"> holds
// one .pane-body; audit/items also carry a static sub-pill row above it).
function activePaneBody() {
  const tab = MODE_TO_TAB[state.mode] || "inventory";
  return document.querySelector(`#pane-${tab} .pane-body`);
}

// String-building renderers can't attach listeners, so switches render as
// canonical vendored markup (switchEl → outerHTML) and one delegated click
// handler on .app flips them via setSwitch — the single write path.
function switchMarkup(on, label, attrs = {}) {
  const btn = switchEl(on, { label });
  for (const [key, value] of Object.entries(attrs)) btn.setAttribute(key, value);
  return btn.outerHTML;
}

function switchOn(selector) {
  return document.querySelector(selector)?.getAttribute("aria-checked") === "true";
}

function c() {
  return state.payload?.columns ?? {};
}

function text(value) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

function html(value) {
  return text(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

function setStatus(message) {
  el.status.textContent = message;
}

// Resting top-bar text: the item count on Home, silence elsewhere (the same
// "Loaded 160 items" on every tab read as clutter). Transient action feedback
// (Saving… / Saved / errors) still writes over it via setStatus.
function idleStatus() {
  return state.mode === "dashboard" && state.payload
    ? `Loaded ${state.payload.summary.total_items} items`
    : "";
}

// Colour-coded current/target, mirroring app/ui_helpers.qty_html:
// green when stocked (current ≥ target), amber when low, red when empty.
function qtyMarkup(current, target) {
  const cur = Number(current) || 0;
  const tgt = Number(target) || 0;
  const cls = cur >= tgt ? "qty-ok" : (cur > 0 ? "qty-low" : "qty-zero");
  return `<span class="${cls}">${html(current)}</span><span class="meta">/${html(target)}</span>`;
}

function setAudioStatus(message, kind = "") {
  const target = document.querySelector("#audio-status");
  if (target) {
    target.textContent = message;
    target.className = `panel-status${kind ? ` ${kind}` : ""}`;
  }
}

let audioAbort = null;

function formatElapsed(seconds) {
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

// Staged progress text, mirroring app/audio_audit.py _extract_progress — budget
// up to ~10 minutes; never imply a call is fast.
function audioMatchStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `Sending request to LLM hub… (${t})`;
  if (elapsed < 20) return `Hub routing to model, analysing transcript… (${t})`;
  if (elapsed < 60) return `Matching mentions to candidates… (${t}) — typical 30s–2min`;
  if (elapsed < 180) return `Still working… (${t}) — long noisy transcripts take 2–4 min`;
  return `Still working… (${t}) — patience, can take up to 10 min on the longest walks`;
}

function audioTranscribeStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `Uploading audio to whisper-server… (${t})`;
  if (elapsed < 30) return `Whisper transcribing… (${t})`;
  if (elapsed < 120) return `Whisper still working… (${t}) — long clips can take 1–3 min`;
  return `Whisper still working… (${t}) — long audio can take up to 10 min`;
}

function setAudioInFlight(busy) {
  const cancel = document.querySelector("#audio-cancel");
  if (cancel) cancel.hidden = !busy;
  ["#match-transcript", "#apply-audio", "#audio-model"].forEach((sel) => {
    const node = document.querySelector(sel);
    if (node) node.disabled = busy;
  });
}

// Run an audio request with a live elapsed timer and a working Cancel button.
// `doFetch(signal)` performs the request; the AbortController lets Cancel free
// the UI even if the hub call is hung.
async function runWithTimer(stageFn, doFetch) {
  audioAbort = new AbortController();
  const start = Date.now();
  const tick = () => setAudioStatus(stageFn(Math.floor((Date.now() - start) / 1000)));
  tick();
  const interval = window.setInterval(tick, 1000);
  setAudioInFlight(true);
  try {
    return await doFetch(audioAbort.signal);
  } finally {
    window.clearInterval(interval);
    setAudioInFlight(false);
    audioAbort = null;
  }
}

async function refreshAudioHealth() {
  try {
    state.audioHealth = await fetchJson("/api/audio/health");
  } catch (_) {
    state.audioHealth = null;
  }
  renderAudioHealth();
}

function renderAudioHealth() {
  const banner = document.querySelector("#audio-health-banner");
  if (!banner) return;
  const h = state.audioHealth;
  if (!h) {
    banner.innerHTML = "";
    return;
  }
  // Sprite icons, not emoji — the status colour + glyph carry the state.
  const okIcon = '<svg class="icon" aria-hidden="true" focusable="false"><use href="#i-circle-check"></use></svg> ';
  const badIcon = '<svg class="icon" aria-hidden="true" focusable="false"><use href="#i-circle-alert"></use></svg> ';
  const problems = [];
  if (!h.voice_ok) problems.push(`${badIcon}Voice recorder unreachable at <code>${html(h.voice_url)}</code> — start the voice-transcriber tray`);
  if (!h.hub_ok) problems.push(`${badIcon}LLM hub unreachable at <code>${html(h.hub_url)}</code>`);
  if (!h.whisper_ok) problems.push(`${badIcon}Whisper server unreachable at <code>${html(h.whisper_url)}</code>`);
  if (!problems.length) {
    banner.className = "panel-status ok";
    banner.innerHTML = `${okIcon}Voice recorder, hub and whisper-server reachable`;
  } else {
    banner.className = "panel-status error";
    banner.innerHTML = `${problems.join("<br>")}<br>Voice recorder is the voice-transcriber app; hub :8000 + whisper :8090 are claude-local-calls.`;
  }
  const matchBtn = document.querySelector("#match-transcript");
  if (matchBtn && !audioAbort) matchBtn.disabled = !h.hub_ok;
  // Record needs the voice-transcriber webapp up — disable it (and show why)
  // rather than letting a take silently hang. Paste + Match still work.
  const recordBtn = document.querySelector("#record-toggle");
  const recording = state.mediaRecorder && state.mediaRecorder.state === "recording";
  if (recordBtn && !recording) {
    recordBtn.disabled = !h.voice_ok;
    recordBtn.title = h.voice_ok ? "" : "Voice recorder unreachable — start the voice-transcriber tray";
  }
}

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

// --------------------------------------------------- build identity
// Ported from home-automation main.js — visible proof of which build the PWA
// is running (footer "Build: <sha> · <time>" line), plus a one-shot reload
// when the served asset hash changed since the last visit (iOS standalone
// PWAs can cling to an old shell even with stamped asset URLs).
const ASSET_HASH_KEY = "grocery.assetHash";
const ASSET_RELOAD_KEY = "grocery.assetReloadedFor";

function fmtBuildTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).replace("T", " ").slice(0, 16);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

async function fetchVersion() {
  try {
    const body = await fetchJson("/api/version");
    const sha = body.git_sha || "unknown";
    const assetHash = body.asset_hash || "";
    const previousHash = localStorage.getItem(ASSET_HASH_KEY) || "";
    if (
      assetHash && previousHash && previousHash !== assetHash &&
      sessionStorage.getItem(ASSET_RELOAD_KEY) !== assetHash
    ) {
      localStorage.setItem(ASSET_HASH_KEY, assetHash);
      sessionStorage.setItem(ASSET_RELOAD_KEY, assetHash);
      window.location.reload();
      return;
    }
    if (assetHash) localStorage.setItem(ASSET_HASH_KEY, assetHash);
    const ts = fmtBuildTime(body.built_at || "");
    el.buildReadout.textContent = ts ? `Build: ${sha} · ${ts}` : `Build: ${sha}`;
  } catch (_) {
    el.buildReadout.textContent = "";
  }
}

function loadShoppingState() {
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

function saveShoppingState() {
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

function captureTokenFromURL() {
  const url = new URL(window.location.href);
  const token = url.searchParams.get("token");
  if (!token) return;
  localStorage.setItem(TOKEN_KEY, token);
  url.searchParams.delete("token");
  window.history.replaceState({}, "", url.toString());
}

function storedToken() {
  return localStorage.getItem(TOKEN_KEY) || "";
}

function defaultZone() {
  const cols = c();
  const withTargets = items().find((item) => Number(item[cols.cantidad]) > 0);
  return withTargets?.[cols.lugar] || state.payload.summary.zones[0] || "";
}

function authFetch(input, init = {}) {
  const token = storedToken();
  const options = { ...init };
  const headers = new Headers(options.headers || {});
  if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
  options.headers = headers;
  return fetch(input, options);
}

async function fetchJson(url, init = {}) {
  const response = await authFetch(url, init);
  if (response.status === 401) {
    await promptForPassword();
    return fetchJson(url, init);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

// Concurrent 401s (e.g. the parallel inventory + access fetches) must share
// one login prompt: every caller gets the same pending promise, resolved once
// on a successful unlock. The dialog is mandatory — Esc/cancel is blocked and
// there is no × close; the fleet nav hides itself while it is open.
let loginPending = null;
let loginResolve = null;

function promptForPassword() {
  if (loginPending) return loginPending;
  loginPending = new Promise((resolve) => {
    loginResolve = resolve;
  });
  el.loginPassword.value = "";
  el.loginError.textContent = "";
  el.loginDialog.showModal();
  window.setTimeout(() => el.loginPassword.focus(), 60);
  return loginPending;
}

async function onLoginSubmit(event) {
  event.preventDefault();
  const password = el.loginPassword.value;
  if (!password) return;
  const button = el.loginForm.querySelector("button[type='submit']");
  button.disabled = true;
  el.loginError.textContent = "";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (response.status === 401) {
      el.loginError.textContent = "Wrong password";
      el.loginPassword.select();
      return;
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      el.loginError.textContent = body.detail || `Login failed: ${response.status}`;
      return;
    }
    localStorage.setItem(TOKEN_KEY, body.token);
    el.loginDialog.close();
    const resolve = loginResolve;
    loginPending = null;
    loginResolve = null;
    resolve?.(true);
  } finally {
    button.disabled = false;
  }
}

// Sub-mode persistence: each grouped tab remembers which of its modes was last
// active, so the PWA reopens exactly where you left it (tab via the nav
// component's own storage, sub-mode via grocery.sub.<tab>).
function restoreSubMode(tab) {
  const fallback = TAB_DEFAULT_MODE[tab] || "dashboard";
  let stored = null;
  try {
    stored = localStorage.getItem(SUB_KEY_PREFIX + tab);
  } catch (_) {
    return fallback;
  }
  return stored && MODE_TO_TAB[stored] === tab ? stored : fallback;
}

function saveSubMode(mode) {
  const tab = MODE_TO_TAB[mode];
  if (!tab || TAB_DEFAULT_MODE[tab] === undefined) return;
  try {
    localStorage.setItem(SUB_KEY_PREFIX + tab, mode);
  } catch (_) {
    // private mode — persistence is best-effort
  }
}

function onTabChange(tab) {
  if (MODE_TO_TAB[state.mode] !== tab) state.mode = restoreSubMode(tab);
  render();
}

async function loadInventory() {
  setStatus("Loading inventory...");
  try {
    state.payload = await fetchJson("/api/inventory", { headers: { Accept: "application/json" } });
    state.access = await fetchJson("/api/access").catch(() => null);
    if (!state.zone) state.zone = defaultZone();
    render();
  } catch (error) {
    setStatus(error.message);
    activePaneBody()?.replaceChildren(emptyStateEl("circle-alert", "Inventory unavailable."));
  }
}

async function mutate(url, payload, method = "POST") {
  setStatus("Saving...");
  state.payload = await fetchJson(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  render();
  setStatus("Saved");
}

function items() {
  return state.payload?.items ?? [];
}

function filteredItems(source = items()) {
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

function metric(label, value) {
  return `<article class="metric"><span>${label}</span><strong>${value}</strong></article>`;
}

function renderSummary() {
  const s = state.payload.summary;
  return `<section class="summary">
    ${metric("Tracked items", s.total_items)}
    ${metric("Stocked", s.total_items - s.shopping_items)}
    ${metric("Need buying", s.shopping_items)}
    ${metric("Units to buy", s.shopping_units)}
    ${metric("Zones", s.zones.length)}
  </section>`;
}

function renderDashboard() {
  const cols = c();
  const source = filteredItems();
  const cards = source.map((item) => itemCard(item, cols)).join("");
  const body = activePaneBody();
  // The full item list folds by default (home-automation pattern: heavy cards
  // are disclosures). A re-render must not slam it shut, so harvest the live
  // open state first; an active search force-opens it — a filter whose
  // results you can't see is a dead control.
  const itemsOpen = !!body.querySelector("#dash-items[open]") || !!state.query;
  body.innerHTML = `${renderSummary()}${renderStoreCards()}
    <details id="dash-items" class="card card--collapsible"${itemsOpen ? " open" : ""}>
      <summary class="collapse-summary">
        <span class="collapse-main">
          <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-package"></use></svg>
          <h3 class="collapse-title">All items</h3>
          <span class="collapse-count">${source.length}</span>
        </span>
        <span class="collapse-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="collapse-body"><section class="grid">${cards || emptyStateEl("search", "No matching items.").outerHTML}</section></div>
    </details>`;
}

// Store progress — ONE shared card, one block per store, with clear air
// between the store name and its progress bar. Carries the cart-offset-aware
// done counts the old sidebar stats showed.
function renderStoreCards() {
  const stats = state.payload.summary.supermarket_stats;
  const stores = Object.keys(stats).sort();
  if (!stores.length) return emptyStateEl("shopping-basket", "No shopping items right now.").outerHTML;
  return `<article class="card">${stores.map((store) => {
    const s = stats[store];
    const offset = state.shopping.offsets[store] || { items: 0, units: 0 };
    const doneItems = s.got_it_unique + Number(offset.items || 0);
    const doneUnits = s.got_it_quantity + Number(offset.units || 0);
    const pct = s.total_unique ? Math.min(100, Math.round((doneItems / s.total_unique) * 100)) : 0;
    return `<div class="store-block">
      <div class="card-head"><h3 class="card-title">${html(store)}</h3><span class="card-head-meta">${doneItems}/${s.total_unique} items · ${doneUnits}/${s.total_quantity} units</span></div>
      <div class="progress"><span style="width:${pct}%"></span></div>
    </div>`;
  }).join("")}</article>`;
}

function itemCard(item, cols) {
  const buy = Number(item[cols.comprar]) || 0;
  return `<article class="item" data-id="${item.id}">
    <div><h3>${html(item[cols.comida])}</h3><div class="meta">${html(item[cols.lugar])} · ${html(item[cols.super])}</div></div>
    <div class="qty"><div>${qtyMarkup(item[cols.tenemos], item[cols.cantidad])}</div><div class="${buy > 0 ? "buy" : "ok"}">${buy > 0 ? `Buy ${buy}` : "Stocked"}</div></div>
  </article>`;
}

function zoneTabs() {
  // .pills, not .tabs — the vendored nav owns the .tabs class app-wide.
  // zone-pills keeps all zones on one swipeable line.
  return `<div class="pills zone-pills">${state.payload.summary.zones.map((zone) =>
    `<button type="button" class="pill ${zone === state.zone ? "active" : ""}" data-zone="${html(zone)}">${html(zone)}</button>`,
  ).join("")}</div>`;
}

function renderAudit(targetsOnly = false) {
  const cols = c();
  const source = filteredItems(items()
    .filter((item) => item[cols.lugar] === state.zone)
    .filter((item) => !targetsOnly || Number(item[cols.cantidad]) > 0))
    .sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])));
  const header = targetsOnly ? "have − + · have/target · target − + · need" : "have/target · target − + · need";
  const controlsClass = targetsOnly ? "audit-controls audit-controls--full" : "audit-controls audit-controls--targets";
  activePaneBody().innerHTML = `<section class="panel"><div class="row"><h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-${targetsOnly ? "list-checks" : "package"}"></use></svg>${targetsOnly ? "Audit Inventory" : "Edit Targets"}</h2><span class="hint">${html(state.zone)} · ${source.length} items</span></div>${zoneTabs()}<div class="hint">${header}</div></section>
    <section class="grid">${source.map((item) => `
      <article class="item audit-item" data-id="${item.id}">
        <div class="audit-name"><h3>${html(item[cols.comida])}</h3><div class="meta">${html(item[cols.super])}</div></div>
        <div class="${controlsClass}">
          ${targetsOnly ? `<button class="icon-btn" data-action="current-minus">-</button><button class="icon-btn" data-action="current-plus">+</button>` : ""}
          <span class="qty">${qtyMarkup(item[cols.tenemos], item[cols.cantidad])}</span>
          <button class="icon-btn" data-action="target-minus">-</button>
          <button class="icon-btn" data-action="target-plus">+</button>
          <span class="audit-verdict ${Number(item[cols.comprar]) > 0 ? "buy" : "ok"}">${Number(item[cols.comprar]) > 0 ? `−${item[cols.comprar]}` : "OK"}</span>
        </div>
      </article>`).join("") || emptyStateEl("package", "No items in this zone.").outerHTML}</section>`;
}

function renderEdit() {
  const cols = c();
  const source = filteredItems().sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])));
  activePaneBody().innerHTML = `<section class="grid">${source.map((item) => `
    <article class="card" data-id="${item.id}">
      <form class="form edit-form">
        <div class="row"><h3>${html(item[cols.comida])}</h3><button class="danger" type="button" data-action="delete">Delete</button></div>
        <div class="three">
          <input class="field" name="comida" value="${html(item[cols.comida])}" placeholder="Item" />
          <input class="field" name="super" value="${html(item[cols.super])}" placeholder="Supermarket" />
          <input class="field" name="lugar" value="${html(item[cols.lugar])}" placeholder="Zone" />
        </div>
        <div class="three-link">
          <input class="field" name="cantidad" type="number" min="0" value="${html(item[cols.cantidad])}" placeholder="Target" />
          <input class="field" name="tenemos" type="number" min="0" value="${html(item[cols.tenemos])}" placeholder="Current" />
          <input class="field" name="buscador" value="${html(item[cols.buscador])}" placeholder="URL" />
        </div>
        <button class="primary" type="submit">Save</button>
      </form>
    </article>`).join("") || emptyStateEl("search", "No matching items.").outerHTML}</section>`;
}

function renderAdd() {
  const zones = state.payload.summary.zones;
  const stores = state.payload.summary.supermarkets;
  activePaneBody().innerHTML = `<section class="panel">
    <h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-plus"></use></svg>Add Item</h2>
    <form id="add-form" class="form">
      <div class="three">
        <input class="field" name="comida" placeholder="Item name" required />
        <input class="field" name="super" list="stores" placeholder="Supermarket" required />
        <input class="field" name="lugar" list="zones" placeholder="Zone" required />
      </div>
      <div class="three-link">
        <input class="field" name="cantidad" type="number" min="0" value="0" placeholder="Target" />
        <input class="field" name="tenemos" type="number" min="0" value="0" placeholder="Current" />
        <input class="field" name="buscador" placeholder="URL" />
      </div>
      <button class="big-btn" type="submit">Add Item</button>
    </form>
    <datalist id="stores">${stores.map((x) => `<option value="${html(x)}"></option>`).join("")}</datalist>
    <datalist id="zones">${zones.map((x) => `<option value="${html(x)}"></option>`).join("")}</datalist>
  </section>`;
}

function shoppingItems() {
  const cols = c();
  return items().filter((item) => Number(item[cols.comprar]) > 0);
}

function renderShopping() {
  const cols = c();
  const base = shoppingItems();
  const stores = [...new Set([...base.map((item) => item[cols.super]), ...Object.keys(state.shopping.extras)])].sort();
  if (!stores.length) {
    activePaneBody().replaceChildren(emptyStateEl("circle-check", "All stocked up."));
    return;
  }
  const missingLink = base.filter((item) => text(item[cols.buscador]) === "-" || !text(item[cols.buscador]).trim());
  const boughtCount = state.shopping.bought.size + Object.values(state.shopping.extraBought || {}).reduce((n, list) => n + (list?.length || 0), 0);
  // Header panel only when it has something to say (unmark-all / warnings) —
  // an empty card under the page title is noise.
  const header = (boughtCount || missingLink.length) ? `<section class="panel">
    <div class="row"><h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-shopping-cart"></use></svg>Shopping</h2>${boughtCount ? `<button class="secondary" id="shopping-unmark-all" type="button">Unmark all</button>` : ""}</div>
    ${missingLink.length ? `<div class="panel-status error">${missingLink.length} item(s) missing a buy link — their Buy button is disabled.</div>` : ""}
  </section>` : "";
  const paneBody = activePaneBody();
  // Store panels fold by default (summary carries the done/total readout);
  // harvest the live open state so a Got-it re-render keeps your store open.
  const openStores = new Set(
    [...paneBody.querySelectorAll("details[data-store][open]")].map((d) => d.dataset.store),
  );
  paneBody.innerHTML = header + stores.map((store) => {
    const storeItems = base.filter((item) => item[cols.super] === store);
    const extras = state.shopping.extras[store] || [];
    const extraBought = new Set(state.shopping.extraBought[store] || []);
    const offset = state.shopping.offsets[store] || { items: 0, units: 0 };
    const totalItems = storeItems.length + extras.length;
    const totalUnits = storeItems.reduce((n, item) => n + Number(item[cols.comprar] || 0), 0) + extras.reduce((n, item) => n + Number(item.qty || 0), 0);
    const doneItems = storeItems.filter((item) => state.shopping.bought.has(item.id)).length + extras.filter((item) => extraBought.has(item.id)).length + Number(offset.items || 0);
    const doneUnits = storeItems.filter((item) => state.shopping.bought.has(item.id)).reduce((n, item) => n + Number(item[cols.comprar] || 0), 0) + extras.filter((item) => extraBought.has(item.id)).reduce((n, item) => n + Number(item.qty || 0), 0) + Number(offset.units || 0);
    return `<details class="card card--collapsible" data-store="${html(store)}"${openStores.has(store) ? " open" : ""}>
      <summary class="collapse-summary">
        <span class="collapse-main">
          <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-shopping-basket"></use></svg>
          <h3 class="collapse-title">${html(store)}</h3>
          <span class="collapse-count">${doneItems}/${totalItems} items · ${doneUnits}/${totalUnits} units</span>
        </span>
        <span class="collapse-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="collapse-body">
        <div class="two">
          <label class="hint">Cart items offset<input class="field" data-action="offset-items" type="number" min="0" value="${Number(offset.items || 0)}"></label>
          <label class="hint">Cart units offset<input class="field" data-action="offset-units" type="number" min="0" value="${Number(offset.units || 0)}"></label>
        </div>
        <div class="grid">
          ${storeItems.map((item) => shoppingRow(item, cols)).join("")}
          ${extras.map((item) => extraRow(item, store, extraBought)).join("")}
        </div>
        <form class="form quick-add">
          <div class="three">
            <input class="field" name="name" placeholder="Quick-add item" required>
            <input class="field" name="qty" type="number" min="1" value="1">
            <button class="secondary" type="submit">Add</button>
          </div>
        </form>
      </div>
    </details>`;
  }).join("");
}

function shoppingRow(item, cols) {
  const bought = state.shopping.bought.has(item.id);
  const url = text(item[cols.buscador]) === "-" ? "" : text(item[cols.buscador]);
  return `<article class="item" data-id="${item.id}">
    <div><h3>${bought ? `<s>${html(item[cols.comida])}</s>` : html(item[cols.comida])}</h3><div class="meta">${html(item[cols.lugar])} · ${item[cols.comprar]}x</div></div>
    <div class="item-actions">
      <button class="secondary" data-action="open-buy" ${url ? `data-url="${html(url)}"` : "disabled"}>${bought ? "Again" : "Buy"}</button>
      <button class="secondary" data-action="${bought ? "undo-buy" : "mark-buy"}">${bought ? "Undo" : "Got it"}</button>
    </div>
  </article>`;
}

function extraRow(item, store, extraBought) {
  const bought = extraBought.has(item.id);
  return `<article class="item" data-extra-id="${item.id}" data-store="${html(store)}">
    <div><h3>${bought ? `<s>${html(item.name)}</s>` : html(item.name)} <span class="meta">+</span></h3><div class="meta">${item.qty}x</div></div>
    <div class="item-actions">
      <button class="danger" data-action="remove-extra">Remove</button>
      <button class="secondary" data-action="${bought ? "undo-extra" : "mark-extra"}">${bought ? "Undo" : "Got it"}</button>
    </div>
  </article>`;
}

function renderAutomation() {
  const stores = state.payload.summary.supermarkets;
  // Email Watch folds by default; harvest live open state so a re-render
  // (e.g. automation dismiss) doesn't slam it shut.
  const emailOpen = !!activePaneBody().querySelector("#email-monitor[open]");
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
  <details id="email-monitor" class="card card--collapsible"${emailOpen ? " open" : ""}>
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
  updateAutomationCommand();
  refreshAutomation();
  refreshEmailMonitor();
}

// ------------------------------------------------ email watch (issue #73)
// Server-side poller over the #72 confirmation-email check: the card selects
// which monitored senders (each mapped to a store) are active, sets the poll
// cadence, and shows the last-check log. "Test last email" re-processes the
// newest confirmation even if already seen — the end-to-end dry run.

const EMAIL_INTERVALS = [
  [15, "Every 15 min"], [30, "Every 30 min"], [60, "Every hour"],
  [180, "Every 3 h"], [360, "Every 6 h"], [720, "Every 12 h"], [1440, "Daily"],
];

async function refreshEmailMonitor() {
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

async function runEmailCheck(force) {
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

// Mirror the Streamlit controls: clean-mode warning + destructive confirm, and a
// live command preview pulled from the backend so the argv never diverges.
async function updateAutomationCommand() {
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
    if (!state.automationStarted) state.automationStarted = Date.now();
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
  if (state.automationTimer) return;
  const tick = () => {
    const elapsed = document.querySelector("#automation-elapsed");
    if (!elapsed || !state.automationStarted) return;
    elapsed.className = "panel-status";
    elapsed.textContent = `Automation running… (${formatElapsed(Math.floor((Date.now() - state.automationStarted) / 1000))} elapsed)`;
  };
  tick();
  state.automationTimer = window.setInterval(tick, 1000);
}

function stopAutomationTimer() {
  if (state.automationTimer) {
    window.clearInterval(state.automationTimer);
    state.automationTimer = null;
  }
  state.automationStarted = null;
}

function connectAutomationEvents() {
  if (state.automationSource) state.automationSource.close();
  state.automationSource = new EventSource("/api/automation/events");
  state.automationSource.onmessage = async (event) => {
    const status = JSON.parse(event.data);
    applyAutomationStatus(status);
    if (!status.running) {
      state.automationSource.close();
      state.automationSource = null;
      const final = await fetchJson("/api/automation/status").catch(() => null);
      if (final) applyAutomationStatus(final);
    }
  };
}

function renderAudio() {
  const cols = c();
  const audioCfg = state.payload.audio || { models: [], default_model: "" };
  if (!state.audioModel) state.audioModel = audioCfg.default_model || audioCfg.models[0] || "";
  const modelOptions = (audioCfg.models || [])
    .map((name) => `<option value="${html(name)}" ${name === state.audioModel ? "selected" : ""}>${html(name)}</option>`)
    .join("");
  const checklist = state.payload.summary.zones.map((zone) => {
    const zoneItems = items().filter((item) => item[cols.lugar] === zone && Number(item[cols.cantidad]) > 0);
    return `<details class="card card--collapsible">
      <summary class="collapse-summary">
        <span class="collapse-main">
          <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-list-checks"></use></svg>
          <h3 class="collapse-title">${html(zone)}</h3>
          <span class="collapse-count">${zoneItems.length}</span>
        </span>
        <span class="collapse-chevron" aria-hidden="true">›</span>
      </summary>
      <div class="collapse-body zone-items">${zoneItems.map((item) =>
        `<div class="zone-item"><span>${html(item[cols.comida])}</span>${switchMarkup(false, `Checked ${text(item[cols.comida])}`)}</div>`,
      ).join("")}</div>
    </details>`;
  }).join("");
  activePaneBody().innerHTML = `<section class="panel">
    <h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-mic"></use></svg>Audio Audit</h2>
    <div id="audio-health-banner" class="panel-status"></div>
    <div class="hint">Keep the checklist visible while recording. Announce the zone, then item counts in Spanish.</div>
    <button id="record-toggle" class="secondary btn-block" type="button">Start Recording</button>
    <button id="audio-redo" class="secondary btn-block" type="button" hidden><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-refresh-cw"></use></svg>Redo</button>
    <div class="hint">Recording streams to the PC as you talk — the take is safe even if the phone dies. Redo re-transcribes the saved audio.</div>
    <div class="zone-list">${checklist}</div>
  </section>
  <section class="panel">
    <h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-list-checks"></use></svg>Transcript</h2>
    <textarea id="transcript" placeholder="Transcript appears here, or paste one manually.">${state.transcript ? html(state.transcript) : ""}</textarea>
    <label class="field-label" for="audio-model">Match model
      <select id="audio-model"${modelOptions ? "" : " disabled"}>${modelOptions || `<option>${html(state.audioModel || "config default")}</option>`}</select>
    </label>
    <div id="audio-context" class="hint"></div>
    <div class="audio-actions">
      <button id="match-transcript" class="big-btn">Match</button>
      <button id="apply-audio" class="big-btn" disabled>Apply</button>
      <button id="audio-clear" class="big-btn" type="button">Clear</button>
      <button id="audio-cancel" class="danger" hidden>Cancel</button>
    </div>
    <div id="audio-status" class="panel-status" role="status"></div>
  </section>
  <section id="match-results" class="grid"></section>`;
  renderAudioContext();
  renderMatches();
  renderAudioHealth();
  refreshAudioHealth();
}

function renderAudioContext() {
  const node = document.querySelector("#audio-context");
  if (!node) return;
  const hub = state.audioHealth?.hub_url || state.payload.audio?.hub_url || "local hub";
  node.textContent = `${hub} · candidates ${items().length} · model ${state.audioModel || "config default"}`;
}

function renderMatches() {
  const target = document.querySelector("#match-results");
  const apply = document.querySelector("#apply-audio");
  if (!target) return;
  if (!state.matches) {
    // The placeholder gets its own card so the results area reads as a
    // surface, not a floating caption.
    target.innerHTML = `<section class="panel">${emptyStateEl("mic", "No match results yet.").outerHTML}</section>`;
    if (apply) apply.disabled = true;
    return;
  }
  const cols = c();
  const clamp = Number(state.payload.audio?.clamp ?? 5);
  const byId = new Map(items().map((item) => [item.id, item]));
  const matched = state.matches.items.filter((match) => byId.has(match.idx));

  // Group detected items by the zone the speaker was in (fall back to lugar).
  const byZone = {};
  matched.forEach((match) => {
    const item = byId.get(match.idx);
    const zone = match.zone || item[cols.lugar] || "—";
    (byZone[zone] ||= []).push(match);
  });

  const detectedRow = (match) => {
    const item = byId.get(match.idx);
    const current = Number(item[cols.tenemos]) || 0;
    const target = Number(item[cols.cantidad]) || 0;
    const proposed = Math.min(Number(match.count) || 0, target + clamp);
    const delta = proposed - current;
    const deltaTxt = `${delta > 0 ? "+" : ""}${delta}`;
    const badge = (match.zone && match.zone !== item[cols.lugar]) ? ` <span class="meta">(list: ${html(item[cols.lugar])})</span>` : "";
    return `<div class="item">
      <span><strong>${html(item[cols.comida])}</strong>${badge}<span class="meta"> ${html(match.evidence || "")}</span></span>
      <span class="match-figures"><span class="meta">${current} →</span> <strong>${proposed}</strong> <span class="${delta > 0 ? "buy" : "meta"}">${deltaTxt}</span>
        ${switchMarkup(true, `Accept ${text(item[cols.comida])}`, { "data-audio-idx": match.idx, "data-count": proposed })}</span>
    </div>`;
  };

  const zoneSections = Object.keys(byZone).sort().map((zone) =>
    `<h3>${html(zone)}</h3><div class="grid">${byZone[zone].map(detectedRow).join("")}</div>`,
  ).join("");

  // "Not mentioned in audited zones" — items in a walked zone with target>0 and
  // current>0 that the speaker didn't name. Tick to zero them.
  const matchedIdx = new Set(matched.map((m) => m.idx));
  const zonesMentioned = new Set((state.matches.zones_mentioned || []).map((z) => String(z).toLowerCase().trim()));
  const unseen = items().filter((item) =>
    !matchedIdx.has(item.id)
    && zonesMentioned.has(String(item[cols.lugar]).toLowerCase().trim())
    && Number(item[cols.cantidad]) > 0
    && Number(item[cols.tenemos]) > 0,
  );
  const unseenSection = unseen.length
    ? `<section class="panel"><h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-circle-alert"></use></svg>Not mentioned (in audited zones)</h2>
      <div class="hint">${unseen.length} item(s) in the zones you walked but didn't name. Tick to set them to 0.</div>
      <div class="grid">${unseen.map((item) =>
        `<div class="item"><span><strong>${html(item[cols.comida])}</strong> <span class="meta">(list: ${html(item[cols.lugar])})</span></span>
          <span class="match-figures"><span class="meta">${Number(item[cols.tenemos])} → <strong>0</strong></span>
            ${switchMarkup(false, `Set ${text(item[cols.comida])} to zero`, { "data-audio-zero": item.id })}</span></div>`,
      ).join("")}</div></section>`
    : "";

  target.innerHTML = `<section class="panel"><h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-circle-check"></use></svg>Detected Items</h2>${
    zoneSections || emptyStateEl("mic", "No recognised items.").outerHTML
  }</section>
  ${unseenSection}
  ${state.matches.unmatched_mentions?.length ? `<section class="panel"><h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-circle-alert"></use></svg>Unmatched Mentions</h2>${state.matches.unmatched_mentions.map((m) => `<div class="meta">${html(m.phrase)} · ${html(m.note)}</div>`).join("")}</section>` : ""}`;
  if (apply) apply.disabled = !matched.length && !unseen.length;
}

// ---------------------------------------------------- product search (issue #87)
// Speak or type a product (Spanish); search both stores; the user validates a
// candidate card (each with a link to see it) to fill that item's `buscador`.
// No automated decision — nothing is written until you tap "Usar".

function searchStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `Abriendo el navegador… (${t})`;
  if (elapsed < 20) return `Buscando en las tiendas… (${t})`;
  if (elapsed < 45) return `Buscando en las tiendas… (${t}) — suele tardar 15–40 s`;
  return `Sigo buscando… (${t}) — a veces tarda 1–2 min`;
}

function renderSearch() {
  if (state.mode !== "search") return; // a background poll must never clobber another pane
  const s = state.search;
  activePaneBody().innerHTML = `<section class="panel">
    <h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-search"></use></svg>Buscar producto</h2>
    <div class="hint">Di o escribe un producto en español. Busco en Mercadona y Ametller — tú eliges el correcto para añadirlo a la lista.</div>
    <div class="search-bar">
      <button id="search-record" class="icon-btn hit-target${s.recording ? " recording" : ""}" type="button" aria-label="Dictar producto" title="Dictar">
        <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-mic"></use></svg>
      </button>
      <input id="search-term" class="search-term" type="search" enterkeyhint="search" autocomplete="off"
             placeholder="p. ej. sandía" value="${html(s.term)}"${s.running ? " disabled" : ""} />
      <button id="search-run" class="big-btn"${s.running ? " disabled" : ""} type="button">
        <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-search"></use></svg>Buscar
      </button>
    </div>
    <div id="search-status" class="panel-status" role="status" aria-live="polite"></div>
    <button id="search-cancel" class="secondary btn-block" type="button"${s.running ? "" : " hidden"}>Cancelar</button>
    <div id="search-results"></div>
  </section>`;
  renderSearchStatus();
  renderSearchResults();
}

function renderSearchStatus() {
  const node = document.querySelector("#search-status");
  if (!node) return;
  const s = state.search;
  node.className = "panel-status";
  if (s.recording) { node.textContent = "Grabando… toca el micro para parar"; return; }
  if (s.error) { node.className = "panel-status error"; node.textContent = s.error; return; }
  if (s.running) {
    const t = formatElapsed(Math.floor((Date.now() - s.startedAt) / 1000));
    // Prefer the real backend phase (Buscando en Mercadona…, N resultados,
    // Preparando…) over the generic time-based stage text.
    node.textContent = s.progress ? `${s.progress} · ${t}` : searchStage(Math.floor((Date.now() - s.startedAt) / 1000));
    return;
  }
  if (s.notice) { node.className = "panel-status ok"; node.textContent = s.notice; return; }
  node.textContent = "";
}

function renderSearchResults() {
  const wrap = document.querySelector("#search-results");
  if (!wrap) return;
  const s = state.search;
  const anyCandidates = s.items.some((i) => (i.candidates || []).length);
  if (s.running && !anyCandidates) { wrap.replaceChildren(); return; }
  wrap.innerHTML = s.items.map(searchItemGroup).join("");
}

function searchItemGroup(item) {
  const cands = item.candidates || [];
  const tag = item.inventory_idx == null
    ? '<span class="chip chip-new">nuevo</span>'
    : '<span class="meta">ya en la lista</span>';
  const header = `<div class="search-group-head"><span class="search-group-term">${html(item.term)}</span>${tag}</div>`;
  // Which stores couldn't be reached (session expired, network) — so a missing
  // store reads as "couldn't check", not "nothing there".
  const failed = Object.keys(item.store_errors || {}).map((s) => s[0].toUpperCase() + s.slice(1));
  const errNote = failed.length
    ? `<div class="panel-status">No pude consultar ${failed.join(" y ")} (sesión o red).</div>` : "";
  if (!cands.length) {
    return `<section class="search-group card">${header}
      <div class="panel-status">No encontré «${html(item.term)}» — prueba otra palabra.</div>${errNote}</section>`;
  }
  return `<section class="search-group card">${header}
    <div class="candidate-list">${cands.map((c) => candidateRow(c, item)).join("")}</div>${errNote}</section>`;
}

function candidateRow(c, item) {
  const done = state.search.resolved[`${item.term}::${c.product_url}`];
  const chip = c.match === "strong" ? '<span class="chip chip-match">coincide</span>' : "";
  const thumb = c.thumbnail
    ? `<img class="candidate-thumb" src="${html(c.thumbnail)}" alt="" loading="lazy" />`
    : `<div class="candidate-thumb candidate-thumb-empty" aria-hidden="true"></div>`;
  return `<article class="candidate" data-term="${html(item.term)}" data-idx="${item.inventory_idx == null ? "" : item.inventory_idx}"
      data-store="${html(c.store)}" data-url="${html(c.product_url)}" data-name="${html(c.name)}">
    ${thumb}
    <div class="candidate-main">
      <div class="candidate-name">${html(c.name)}${chip}</div>
      <div class="meta">${html(c.store)}${c.price_text ? " · " + html(c.price_text) : ""}</div>
    </div>
    <div class="candidate-actions">
      <a class="icon-btn hit-target" href="${html(c.product_url)}" target="_blank" rel="noopener" aria-label="Ver producto" title="Ver">
        <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-external-link"></use></svg>
      </a>
      <button class="secondary candidate-use" type="button" data-action="search-use"${done ? " disabled" : ""}>${done ? "Añadido ✓" : "Usar"}</button>
    </div>
  </article>`;
}

async function toggleSearchRecording(button) {
  const s = state.search;
  if (s.recording && s.recorder) { s.recorder.stop(); return; }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (_) {
    s.error = "Permiso de micrófono denegado"; renderSearchStatus(); return;
  }
  const mime = pickAudioMime();
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  s.chunks = [];
  rec.ondataavailable = (e) => { if (e.data && e.data.size) s.chunks.push(e.data); };
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    s.recording = false;
    button.classList.remove("recording");
    await transcribeSearchClip(mime);
  };
  s.recorder = rec;
  s.recording = true;
  s.error = "";
  s.notice = "";
  button.classList.add("recording");
  renderSearchStatus();
  rec.start();
}

async function transcribeSearchClip(mime) {
  const s = state.search;
  const status = document.querySelector("#search-status");
  if (status) { status.className = "panel-status"; status.textContent = "Transcribiendo…"; }
  try {
    const form = new FormData();
    form.append("file", new Blob(s.chunks, { type: mime || "audio/webm" }),
      mime && mime.includes("mp4") ? "query.mp4" : "query.webm");
    const res = await authFetch("/api/product-search/transcribe", { method: "POST", body: form });
    const body = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(body.detail || `HTTP ${res.status}`);
    s.term = (body.transcript || "").trim();
    renderSearch();
    if (s.term) startProductSearch(); // speak → auto-search, per the on-demand flow
  } catch (err) {
    s.error = `No pude transcribir: ${err.message}`;
    renderSearchStatus();
  }
}

async function startProductSearch() {
  const s = state.search;
  const term = (document.querySelector("#search-term")?.value ?? s.term).trim();
  if (!term) { s.error = "Di o escribe un producto"; renderSearchStatus(); return; }
  Object.assign(s, { term, error: "", notice: "", items: [], resolved: {}, running: true, startedAt: Date.now(), progress: "" });
  renderSearch();
  startSearchPoll();
  try {
    applySearchStatus(await fetchJson("/api/product-search/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: term }),
    }));
  } catch (err) {
    s.running = false; stopSearchPoll(); s.error = err.message; renderSearch();
  }
}

function startSearchPoll() {
  stopSearchPoll();
  const tick = async () => {
    renderSearchStatus();
    const status = await fetchJson("/api/product-search/status").catch(() => null);
    if (status) applySearchStatus(status);
  };
  state.search.pollTimer = window.setInterval(tick, 1000);
}

function stopSearchPoll() {
  if (state.search.pollTimer) { window.clearInterval(state.search.pollTimer); state.search.pollTimer = null; }
}

function applySearchStatus(status) {
  const s = state.search;
  // "idle" = the run isn't registered yet (the /start call is still parsing the
  // utterance). Keep our optimistic running state; don't stop the poll early.
  if (!status || status.state === "idle") return;
  s.progress = status.progress || "";
  s.items = status.items || [];
  if (status.state === "running") { s.running = true; renderSearchResults(); renderSearchStatus(); return; }
  s.running = false;
  stopSearchPoll();
  if (status.state === "error") s.error = status.error || "La búsqueda falló";
  renderSearch();
}

async function cancelProductSearch() {
  state.search.running = false;
  stopSearchPoll();
  await fetchJson("/api/product-search/cancel", { method: "POST" }).catch(() => null);
  state.search.notice = "Búsqueda cancelada";
  renderSearch();
}

async function useCandidate(cardEl) {
  if (!cardEl) return;
  const s = state.search;
  const idxRaw = cardEl.dataset.idx;
  const payload = {
    term: cardEl.dataset.term,
    store: cardEl.dataset.store,
    product_url: cardEl.dataset.url,
    name: cardEl.dataset.name,
    inventory_idx: idxRaw === "" ? null : Number(idxRaw),
  };
  const btn = cardEl.querySelector(".candidate-use");
  if (btn) { btn.disabled = true; btn.textContent = "Guardando…"; }
  try {
    state.payload = await fetchJson("/api/product-search/select", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    s.resolved[`${payload.term}::${payload.product_url}`] = true;
    s.notice = `Añadido: ${payload.name} (${payload.store})`;
    renderSearch();
  } catch (err) {
    s.error = `No se pudo guardar: ${err.message}`;
    if (btn) { btn.disabled = false; btn.textContent = "Usar"; }
    renderSearchStatus();
  }
}

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
  if (sw.id === "email-poller-enabled" || sw.dataset.monitorSender !== undefined) pushEmailMonitorConfig();
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
    state.audioModel = event.target.value;
    renderAudioContext();
    return;
  }
  // The dry-run / clean-confirm switches fire through the click delegation
  // above; only the two selects arrive here.
  if (["automation-store", "automation-cart-mode"].includes(event.target.id)) {
    updateAutomationCommand();
    return;
  }
  if (event.target.id === "email-poller-interval") {
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
  if (id === "automation-start") {
    state.automationStarted = Date.now();
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
  if (id === "automation-stop") await fetchJson("/api/automation/stop", { method: "POST" });
  if (id === "email-check-now") await runEmailCheck(false);
  if (id === "email-check-test") await runEmailCheck(true);
  if (id === "automation-dismiss") {
    stopAutomationTimer();
    await fetchJson("/api/automation/reset", { method: "POST" }).catch(() => null);
    renderAutomation();
  }
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
  if (id === "audio-cancel" && audioAbort) audioAbort.abort();
  if (id === "search-record") await toggleSearchRecording(button);
  if (id === "search-run") await startProductSearch();
  if (id === "search-cancel") await cancelProductSearch();
  if (button?.dataset.action === "search-use") await useCandidate(button.closest(".candidate"));
});

// Product-search term box: keep state in sync while typing; Enter runs the search.
el.app.addEventListener("input", (event) => {
  if (event.target.id === "search-term") state.search.term = event.target.value;
});
el.app.addEventListener("keydown", (event) => {
  if (event.target.id === "search-term" && event.key === "Enter") {
    event.preventDefault();
    startProductSearch();
  }
});

// Wipe the transcript + match results so the next audit starts from scratch.
function clearAudio() {
  if (audioAbort) audioAbort.abort();
  closeAudioPartialStream();
  state.transcript = "";
  state.matches = null;
  state.audioSha = "";
  state.audioBytes = 0;
  state.sessionId = "";
  state.bytesSent = 0;
  render();
  setAudioStatus("Cleared — ready for a new audit", "");
}

// iOS Safari's MediaRecorder only produces audio/mp4 — picking a supported
// type (and labelling the blob/filename to match) is what stops whisper from
// choking on mp4 bytes mislabelled as .webm. Mirrors voice-transcriber.
function pickAudioMime() {
  if (!("MediaRecorder" in window)) return "";
  const candidates = ["audio/webm;codecs=opus", "audio/webm", "audio/mp4;codecs=mp4a.40.2", "audio/mp4"];
  for (const m of candidates) {
    if (MediaRecorder.isTypeSupported && MediaRecorder.isTypeSupported(m)) return m;
  }
  return "";
}

function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

// Hardened recording (issue #30): every 1 s chunk is streamed to the PC and
// archived to disk by the voice-transcriber app the moment it arrives, so the
// take survives a dying phone. Rolling partials flow back over SSE; Stop yields
// the canonical transcript. grocery only proxies — VT owns the audio.
async function toggleRecording(button) {
  if (state.mediaRecorder && state.mediaRecorder.state === "recording") {
    button.disabled = true;
    button.textContent = "Finishing…";
    state.mediaRecorder.stop();
    return;
  }
  if (state.audioHealth && !state.audioHealth.voice_ok) {
    setAudioStatus("Voice recorder unreachable — start the voice-transcriber tray", "error");
    return;
  }

  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (_) {
    setAudioStatus("Mic permission denied", "error");
    return;
  }

  let session;
  try {
    session = await fetchJson("/api/audio/session", { method: "POST" });
  } catch (error) {
    stream.getTracks().forEach((track) => track.stop());
    setAudioStatus(`Could not start recording session: ${error.message}`, "error");
    return;
  }

  state.audioStream = stream;
  state.sessionId = session.session_id;
  state.audioMime = pickAudioMime();
  state.uploadChain = Promise.resolve();
  state.pendingUploads = 0;
  state.bytesSent = 0;
  state.audioSha = "";
  state.audioBytes = 0;
  state.recordStartedAt = Date.now();

  state.mediaRecorder = new MediaRecorder(stream, state.audioMime ? { mimeType: state.audioMime } : undefined);
  state.mediaRecorder.ondataavailable = (event) => {
    if (event.data && event.data.size) enqueueChunkUpload(event.data);
  };
  state.mediaRecorder.onstop = () => {
    stream.getTracks().forEach((track) => track.stop());
    state.audioStream = null;
    finishRecording();
  };
  state.mediaRecorder.start(1000); // 1 s cadence — survives a connection drop

  startRecordTimer();
  openAudioPartialStream(state.sessionId);
  button.textContent = "Stop Recording";
  const redo = document.querySelector("#audio-redo");
  if (redo) redo.hidden = true;
}

// Serialised upload chain — each chunk POSTs after the previous resolves so
// they land on disk in order without overwhelming the connection.
function enqueueChunkUpload(chunk) {
  state.pendingUploads += 1;
  const sessionId = state.sessionId;
  state.uploadChain = state.uploadChain.then(async () => {
    try {
      const response = await authFetch(`/api/audio/session/${sessionId}/chunk`, {
        method: "POST",
        headers: { "Content-Type": chunk.type || state.audioMime || "audio/webm" },
        body: chunk,
      });
      if (response.ok) state.bytesSent += chunk.size;
      else console.warn("chunk upload failed", response.status);
    } catch (error) {
      console.warn("chunk upload errored", error);
    } finally {
      state.pendingUploads -= 1;
    }
  });
}

function startRecordTimer() {
  stopRecordTimer();
  const tick = () => {
    const elapsed = Math.floor((Date.now() - state.recordStartedAt) / 1000);
    setAudioStatus(`Recording · ${formatElapsed(elapsed)} · ${formatBytes(state.bytesSent)} streamed to PC`);
  };
  tick();
  state.recordTimer = window.setInterval(tick, 1000);
}

function stopRecordTimer() {
  if (state.recordTimer) {
    window.clearInterval(state.recordTimer);
    state.recordTimer = null;
  }
}

// VT's rolling-transcription SSE, proxied through grocery's own origin. Partials
// fill the transcript box live; if VT has rolling transcription off, no events
// arrive and the box simply fills on finish.
function openAudioPartialStream(sessionId) {
  closeAudioPartialStream();
  if (!("EventSource" in window)) return;
  let url = `/api/audio/session/${sessionId}/events`;
  const token = storedToken();
  if (token) url += `?token=${encodeURIComponent(token)}`;
  let es;
  try {
    es = new EventSource(url);
  } catch (_) {
    return;
  }
  state.audioEventSource = es;
  es.addEventListener("partial", (event) => {
    try {
      const data = JSON.parse(event.data);
      if (typeof data.transcript === "string") {
        state.transcript = data.transcript;
        const field = document.querySelector("#transcript");
        if (field) field.value = data.transcript;
      }
    } catch (_) {}
  });
  es.addEventListener("final", () => closeAudioPartialStream());
  es.onerror = () => {}; // browser auto-retries; leave the handle in place
}

function closeAudioPartialStream() {
  if (state.audioEventSource) {
    try { state.audioEventSource.close(); } catch (_) {}
    state.audioEventSource = null;
  }
}

async function finishRecording() {
  const button = document.querySelector("#record-toggle");
  stopRecordTimer();
  try {
    setAudioStatus(`Finalising upload · ${state.pendingUploads} chunk(s) left…`);
    await state.uploadChain;
    state.audioBytes = state.bytesSent;
    const body = await runWithTimer(audioTranscribeStage, async (signal) => {
      const response = await authFetch(
        `/api/audio/session/${state.sessionId}/finish?language=es&translate=false`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: "{}", signal },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      return data;
    });
    closeAudioPartialStream();
    if (body.silent) {
      setAudioStatus("Empty audio — nothing transcribed", "");
    } else {
      state.transcript = body.transcript || "";
      const field = document.querySelector("#transcript");
      if (field) field.value = state.transcript;
      setAudioStatus("Transcript ready — recording saved on the PC", "ok");
    }
    const redo = document.querySelector("#audio-redo");
    if (redo) redo.hidden = !state.sessionId;
  } catch (error) {
    closeAudioPartialStream();
    if (error.name === "AbortError") {
      setAudioStatus("Finish cancelled — recording is safe on the PC, tap Redo", "");
    } else {
      setAudioStatus(`Transcription failed: ${error.message} — recording is safe on the PC, tap Redo`, "error");
    }
    const redo = document.querySelector("#audio-redo");
    if (redo) redo.hidden = !state.sessionId;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Start Recording";
    }
  }
}

// Re-run whisper on the saved audio — crash recovery, or after a finish error.
async function redoTranscribe() {
  if (!state.sessionId) {
    setAudioStatus("No saved recording to redo", "error");
    return;
  }
  try {
    const body = await runWithTimer(audioTranscribeStage, async (signal) => {
      const response = await authFetch(
        `/api/audio/session/${state.sessionId}/retranscribe?language=es&translate=false`,
        { method: "POST", signal },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      return data;
    });
    state.transcript = body.transcript || "";
    const field = document.querySelector("#transcript");
    if (field) field.value = state.transcript;
    setAudioStatus(body.silent ? "Empty audio — nothing transcribed" : "Re-transcribed from saved audio", body.silent ? "" : "ok");
  } catch (error) {
    if (error.name === "AbortError") setAudioStatus("Redo cancelled", "");
    else setAudioStatus(`Redo failed: ${error.message}`, "error");
  }
}

async function matchTranscript() {
  const field = document.querySelector("#transcript");
  const transcript = (field?.value || "").trim();
  state.transcript = transcript;
  if (!transcript) {
    setAudioStatus("Add or transcribe a transcript first", "error");
    return;
  }
  if (state.audioHealth && !state.audioHealth.hub_ok) {
    setAudioStatus("LLM hub unreachable — start the hub before matching", "error");
    return;
  }
  const model = state.audioModel;
  try {
    state.matches = await runWithTimer(audioMatchStage, (signal) =>
      fetchJson("/api/audio/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(model ? { transcript, model } : { transcript }),
        signal,
      }),
    );
    const m = state.matches;
    setAudioStatus(
      `Matched ${m.items.length} item${m.items.length === 1 ? "" : "s"} · ${m.candidates} candidates · ${m.transcript_chars} chars · ${m.model}`,
      "ok",
    );
    renderMatches();
  } catch (error) {
    if (error.name === "AbortError") setAudioStatus("Match cancelled", "");
    else setAudioStatus(`Match failed: ${error.message}`, "error");
  }
}

async function applyAudio() {
  const updates = {};
  document.querySelectorAll('[data-audio-idx][aria-checked="true"]').forEach((box) => {
    updates[box.dataset.audioIdx] = Number(box.dataset.count);
  });
  document.querySelectorAll('[data-audio-zero][aria-checked="true"]').forEach((box) => {
    updates[box.dataset.audioZero] = 0;
  });
  if (!Object.keys(updates).length) {
    setAudioStatus("Nothing accepted to apply", "error");
    return;
  }
  const button = document.querySelector("#apply-audio");
  if (button) button.disabled = true;
  setAudioStatus("Applying accepted counts…");
  try {
    state.payload = await fetchJson("/api/audio/apply", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        updates,
        transcript: state.transcript,
        model: state.audioModel,
        matches: state.matches,
        audio_sha: state.audioSha || "",
        audio_bytes: state.audioBytes || 0,
      }),
    });
    const logPath = state.payload.audio_log_path || "";
    state.matches = null;
    render();
    setAudioStatus(logPath ? `Inventory updated · log ${logPath.split(/[\\/]/).pop()}` : "Inventory updated", "ok");
  } catch (error) {
    setAudioStatus(`Apply failed: ${error.message}`, "error");
    if (button) button.disabled = false;
  }
}

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
