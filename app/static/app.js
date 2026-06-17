const TOKEN_KEY = "grocery.authToken";
const SHOP_STATE_KEY = "grocery.shoppingState";

const modes = [
  ["dashboard", "Inventory"],
  ["audit", "Audit"],
  ["targets", "Targets"],
  ["edit", "Edit Item"],
  ["add", "Add Item"],
  ["shopping", "Shopping"],
  ["audio", "Audio Audit"],
  ["automation", "Automation"],
];

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
  audioChunks: [],
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
};

const el = {
  nav: document.querySelector("#nav"),
  themeToggle: document.querySelector("#theme-toggle"),
  title: document.querySelector("#view-title"),
  status: document.querySelector("#status"),
  search: document.querySelector("#search"),
  refresh: document.querySelector("#refresh"),
  main: document.querySelector("#main"),
  sideStats: document.querySelector("#side-stats"),
  openSheet: document.querySelector("#open-sheet"),
  copyLink: document.querySelector("#copy-link"),
  exportCsv: document.querySelector("#export-csv"),
  closeApp: document.querySelector("#close-app"),
  loginOverlay: document.querySelector("#login-overlay"),
  loginForm: document.querySelector("#login-form"),
  loginPassword: document.querySelector("#login-password"),
  loginError: document.querySelector("#login-error"),
};

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
  setStatus(message);
}

let audioAbort = null;

function formatElapsed(seconds) {
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

// Staged progress text, mirroring app/audio_audit.py _extract_progress — budget
// up to ~10 minutes; never imply a call is fast.
function audioMatchStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `📡 Sending request to LLM hub… (${t})`;
  if (elapsed < 20) return `🧠 Hub routing to model, analysing transcript… (${t})`;
  if (elapsed < 60) return `🧠 Matching mentions to candidates… (${t}) — typical 30s–2min`;
  if (elapsed < 180) return `⏳ Still working… (${t}) — long noisy transcripts take 2–4 min`;
  return `⏳ Still working… (${t}) — patience, can take up to 10 min on the longest walks`;
}

function audioTranscribeStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `📡 Uploading audio to whisper-server… (${t})`;
  if (elapsed < 30) return `🎙️ Whisper transcribing… (${t})`;
  if (elapsed < 120) return `⏳ Whisper still working… (${t}) — long clips can take 1–3 min`;
  return `⏳ Whisper still working… (${t}) — long audio can take up to 10 min`;
}

function setAudioInFlight(busy) {
  const cancel = document.querySelector("#audio-cancel");
  if (cancel) cancel.hidden = !busy;
  ["#match-transcript", "#transcribe-audio", "#apply-audio", "#audio-model"].forEach((sel) => {
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

async function computeAudioSha(blob) {
  try {
    const buffer = await blob.arrayBuffer();
    state.audioBytes = buffer.byteLength;
    const digest = await crypto.subtle.digest("SHA-256", buffer);
    state.audioSha = [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, "0")).join("");
  } catch (_) {
    // crypto.subtle is unavailable over plain http on LAN — sha is best-effort.
    state.audioSha = "";
    state.audioBytes = blob.size || 0;
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
  const problems = [];
  if (!h.voice_ok) problems.push(`❌ Voice recorder unreachable at <code>${html(h.voice_url)}</code> — start the voice-transcriber tray`);
  if (!h.hub_ok) problems.push(`❌ LLM hub unreachable at <code>${html(h.hub_url)}</code>`);
  if (!h.whisper_ok) problems.push(`❌ Whisper server unreachable at <code>${html(h.whisper_url)}</code>`);
  if (!problems.length) {
    banner.className = "panel-status ok";
    banner.innerHTML = "✅ Voice recorder, hub and whisper-server reachable";
  } else {
    banner.className = "panel-status error";
    banner.innerHTML = `${problems.join("<br>")}<br>Voice recorder is the voice-transcriber app; hub :8000 + whisper :8090 are claude-local-calls.`;
  }
  const matchBtn = document.querySelector("#match-transcript");
  if (matchBtn && !audioAbort) matchBtn.disabled = !h.hub_ok;
  // Record needs the voice-transcriber webapp up — disable it (and show why)
  // rather than letting a take silently hang. File upload + Match still work.
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
  if (el.themeToggle) {
    el.themeToggle.textContent = theme === "dark" ? "☀️" : "🌙";
    el.themeToggle.title = theme === "dark" ? "Switch to light mode" : "Switch to dark mode";
  }
}

function toggleTheme() {
  applyTheme(currentTheme() === "dark" ? "light" : "dark");
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

async function promptForPassword() {
  return new Promise((resolve) => {
    el.loginOverlay.hidden = false;
    el.loginPassword.value = "";
    el.loginError.textContent = "";
    window.setTimeout(() => el.loginPassword.focus(), 60);

    const onSubmit = async (event) => {
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
        el.loginOverlay.hidden = true;
        el.loginForm.removeEventListener("submit", onSubmit);
        resolve(true);
      } finally {
        button.disabled = false;
      }
    };

    el.loginForm.addEventListener("submit", onSubmit);
  });
}

function initNav() {
  el.nav.innerHTML = modes
    .map(([id, label]) => `<button type="button" data-mode="${id}">${label}</button>`)
    .join("");
  el.nav.addEventListener("click", (event) => {
    const button = event.target.closest("[data-mode]");
    if (!button) return;
    state.mode = button.dataset.mode;
    render();
  });
}

async function loadInventory() {
  setStatus("Loading inventory...");
  el.refresh.disabled = true;
  try {
    state.payload = await fetchJson("/api/inventory", { headers: { Accept: "application/json" } });
    state.access = await fetchJson("/api/access").catch(() => null);
    if (!state.zone) state.zone = defaultZone();
    setStatus(`Loaded ${state.payload.summary.total_items} items`);
    render();
  } catch (error) {
    setStatus(error.message);
    el.main.innerHTML = `<div class="empty">Inventory unavailable.</div>`;
  } finally {
    el.refresh.disabled = false;
  }
}

async function mutate(url, payload, method = "POST") {
  setStatus("Saving...");
  state.payload = await fetchJson(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  setStatus(`Loaded ${state.payload.summary.total_items} items`);
  render();
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

function renderSideStats() {
  if (!state.payload) return;
  const stats = state.payload.summary.supermarket_stats;
  const stores = Object.keys(stats).sort();
  el.sideStats.innerHTML = `<div>${state.payload.summary.total_items} total tracked items</div>` + stores.map((store) => {
    const s = stats[store];
    const offset = state.shopping.offsets[store] || { items: 0, units: 0 };
    const doneItems = s.got_it_unique + Number(offset.items || 0);
    const doneUnits = s.got_it_quantity + Number(offset.units || 0);
    const pct = s.total_unique ? Math.min(100, Math.round((doneItems / s.total_unique) * 100)) : 0;
    return `<div class="store-stat">
      <strong>${html(store)}</strong>
      <div>${doneItems}/${s.total_unique} items · ${doneUnits}/${s.total_quantity} units</div>
      <div class="progress"><span style="width:${pct}%"></span></div>
    </div>`;
  }).join("");
}

function renderDashboard() {
  const cols = c();
  const cards = filteredItems().map((item) => itemCard(item, cols)).join("");
  el.main.innerHTML = `${renderSummary()}${renderStoreCards()}<section class="grid">${cards || '<div class="empty">No matching items.</div>'}</section>`;
}

function renderStoreCards() {
  const stats = state.payload.summary.supermarket_stats;
  const stores = Object.keys(stats).sort();
  if (!stores.length) return `<div class="empty">No shopping items right now.</div>`;
  return `<section class="grid">${stores.map((store) => {
    const s = stats[store];
    const pct = s.total_unique ? Math.round((s.got_it_unique / s.total_unique) * 100) : 0;
    return `<article class="card">
      <div class="row"><h2>${html(store)}</h2><div class="meta">${s.total_unique} items · ${s.total_quantity} units</div></div>
      <div class="progress"><span style="width:${pct}%"></span></div>
    </article>`;
  }).join("")}</section>`;
}

function itemCard(item, cols) {
  const buy = Number(item[cols.comprar]) || 0;
  return `<article class="item" data-id="${item.id}">
    <div><h3>${html(item[cols.comida])}</h3><div class="meta">${html(item[cols.lugar])} · ${html(item[cols.super])}</div></div>
    <div class="qty"><div>${qtyMarkup(item[cols.tenemos], item[cols.cantidad])}</div><div class="${buy > 0 ? "buy" : "ok"}">${buy > 0 ? `Buy ${buy}` : "Stocked"}</div></div>
  </article>`;
}

function zoneTabs() {
  return `<div class="tabs">${state.payload.summary.zones.map((zone) =>
    `<button type="button" class="pill ${zone === state.zone ? "active" : ""}" data-zone="${html(zone)}">${html(zone)}</button>`,
  ).join("")}</div>`;
}

function renderAudit(targetsOnly = false) {
  const cols = c();
  const source = filteredItems(items()
    .filter((item) => item[cols.lugar] === state.zone)
    .filter((item) => !targetsOnly || Number(item[cols.cantidad]) > 0))
    .sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])));
  const header = targetsOnly ? "➖ have ➕ · have/target · ⊖ target ⊕ · buy" : "have/target · ⊖ target ⊕ · buy";
  el.main.innerHTML = `<section class="panel"><div class="row"><h2>${targetsOnly ? "Audit Inventory" : "Edit Targets"}</h2><span class="hint">${html(state.zone)} · ${source.length} items</span></div>${zoneTabs()}<div class="hint">${header}</div></section>
    <section class="grid">${source.map((item) => `
      <article class="item" data-id="${item.id}">
        <div><h3>${html(item[cols.comida])}</h3><div class="meta">${html(item[cols.super])}</div></div>
        <div class="item-actions">
          ${targetsOnly ? `<button class="icon-btn" data-action="current-minus">-</button><button class="icon-btn" data-action="current-plus">+</button>` : ""}
          <span class="qty">${qtyMarkup(item[cols.tenemos], item[cols.cantidad])}</span>
          <button class="icon-btn" data-action="target-minus">-</button>
          <button class="icon-btn" data-action="target-plus">+</button>
          <span class="${Number(item[cols.comprar]) > 0 ? "buy" : "ok"}">${Number(item[cols.comprar]) > 0 ? `Buy ${item[cols.comprar]}` : "OK"}</span>
        </div>
      </article>`).join("") || '<div class="empty">No items in this zone.</div>'}</section>`;
}

function renderEdit() {
  const cols = c();
  const source = filteredItems().sort((a, b) => text(a[cols.comida]).localeCompare(text(b[cols.comida])));
  el.main.innerHTML = `<section class="grid">${source.map((item) => `
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
    </article>`).join("") || '<div class="empty">No matching items.</div>'}</section>`;
}

function renderAdd() {
  const zones = state.payload.summary.zones;
  const stores = state.payload.summary.supermarkets;
  el.main.innerHTML = `<section class="panel">
    <h2>Add Item</h2>
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
      <button class="primary" type="submit">Add Item</button>
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
    el.main.innerHTML = `<div class="empty">All stocked up.</div>`;
    return;
  }
  const missingLink = base.filter((item) => text(item[cols.buscador]) === "-" || !text(item[cols.buscador]).trim());
  const boughtCount = state.shopping.bought.size + Object.values(state.shopping.extraBought || {}).reduce((n, list) => n + (list?.length || 0), 0);
  const header = `<section class="panel">
    <div class="row"><h2>Shopping</h2>${boughtCount ? `<button class="secondary" id="shopping-unmark-all" type="button">↩️ Unmark all</button>` : ""}</div>
    ${missingLink.length ? `<div class="panel-status error">⚠️ ${missingLink.length} item(s) missing a buy link — their Buy button is disabled.</div>` : ""}
  </section>`;
  el.main.innerHTML = header + stores.map((store) => {
    const storeItems = base.filter((item) => item[cols.super] === store);
    const extras = state.shopping.extras[store] || [];
    const extraBought = new Set(state.shopping.extraBought[store] || []);
    const offset = state.shopping.offsets[store] || { items: 0, units: 0 };
    const totalItems = storeItems.length + extras.length;
    const totalUnits = storeItems.reduce((n, item) => n + Number(item[cols.comprar] || 0), 0) + extras.reduce((n, item) => n + Number(item.qty || 0), 0);
    const doneItems = storeItems.filter((item) => state.shopping.bought.has(item.id)).length + extras.filter((item) => extraBought.has(item.id)).length + Number(offset.items || 0);
    const doneUnits = storeItems.filter((item) => state.shopping.bought.has(item.id)).reduce((n, item) => n + Number(item[cols.comprar] || 0), 0) + extras.filter((item) => extraBought.has(item.id)).reduce((n, item) => n + Number(item.qty || 0), 0) + Number(offset.units || 0);
    return `<section class="panel" data-store="${html(store)}">
      <div class="row"><h2>${html(store)}</h2><span class="meta">${doneItems}/${totalItems} items · ${doneUnits}/${totalUnits} units</span></div>
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
    </section>`;
  }).join("");
}

function shoppingRow(item, cols) {
  const bought = state.shopping.bought.has(item.id);
  const url = text(item[cols.buscador]) === "-" ? "" : text(item[cols.buscador]);
  return `<article class="item" data-id="${item.id}">
    <div><h3>${bought ? `<s>${html(item[cols.comida])}</s>` : html(item[cols.comida])}</h3><div class="meta">${html(item[cols.lugar])} · ${item[cols.comprar]}x</div></div>
    <div class="item-actions">
      <button class="secondary" data-action="open-buy" ${url ? `data-url="${html(url)}"` : "disabled"}>${bought ? "Again" : "Buy"}</button>
      <button class="${bought ? "secondary" : "primary"}" data-action="${bought ? "undo-buy" : "mark-buy"}">${bought ? "Undo" : "Got it"}</button>
    </div>
  </article>`;
}

function extraRow(item, store, extraBought) {
  const bought = extraBought.has(item.id);
  return `<article class="item" data-extra-id="${item.id}" data-store="${html(store)}">
    <div><h3>${bought ? `<s>${html(item.name)}</s>` : html(item.name)} <span class="meta">+</span></h3><div class="meta">${item.qty}x</div></div>
    <div class="item-actions">
      <button class="danger" data-action="remove-extra">Remove</button>
      <button class="${bought ? "secondary" : "primary"}" data-action="${bought ? "undo-extra" : "mark-extra"}">${bought ? "Undo" : "Got it"}</button>
    </div>
  </article>`;
}

function renderAutomation() {
  const stores = state.payload.summary.supermarkets;
  el.main.innerHTML = `<section class="panel">
    <h2>Run Automation</h2>
    <div class="hint">Fills the store carts from this list via Chrome automation. You still confirm and pay in the browser.</div>
    <div class="three">
      <select id="automation-store" class="field"><option value="all">All stores</option>${stores.map((s) => `<option value="${html(s)}">${html(s)}</option>`).join("")}</select>
      <select id="automation-cart-mode" class="field"><option value="keep">Keep cart</option><option value="clean">Clean cart</option></select>
      <label class="hint"><input id="automation-dry-run" type="checkbox"> Dry run</label>
    </div>
    <div id="automation-clean-warn" class="panel-status error" hidden>⚠️ Clean mode empties the store cart first — anything added by hand will be removed.</div>
    <label id="automation-clean-confirm-wrap" class="hint" hidden><input id="automation-clean-confirm" type="checkbox"> Yes, empty the cart first</label>
    <pre id="automation-command" class="log"></pre>
    <div class="actions">
      <button id="automation-start" class="primary" type="button">▶ Run Automation</button>
      <button id="automation-stop" class="danger" type="button" hidden>🛑 Stop</button>
      <button id="automation-dismiss" class="secondary" type="button" hidden>Dismiss</button>
    </div>
    <div id="automation-elapsed" class="panel-status"></div>
    <pre id="automation-log" class="log">(not running)</pre>
  </section>`;
  updateAutomationCommand();
  refreshAutomation();
}

// Mirror the Streamlit controls: clean-mode warning + destructive confirm, and a
// live command preview pulled from the backend so the argv never diverges.
async function updateAutomationCommand() {
  const store = document.querySelector("#automation-store")?.value || "all";
  const cartMode = document.querySelector("#automation-cart-mode")?.value || "keep";
  const dryRun = document.querySelector("#automation-dry-run")?.checked ?? true;
  const clean = cartMode === "clean";
  const warn = document.querySelector("#automation-clean-warn");
  const confirmWrap = document.querySelector("#automation-clean-confirm-wrap");
  if (warn) warn.hidden = !clean;
  if (confirmWrap) confirmWrap.hidden = !(clean && !dryRun);
  const confirmBox = document.querySelector("#automation-clean-confirm");
  const start = document.querySelector("#automation-start");
  if (start) start.disabled = clean && !dryRun && !(confirmBox?.checked);
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
      ? "✅ Automation finished — exit 0. Review and pay in the browser."
      : `❌ Automation exited with code ${status.returncode}. See the log above.`;
  }
}

function startAutomationTimer() {
  if (state.automationTimer) return;
  const tick = () => {
    const elapsed = document.querySelector("#automation-elapsed");
    if (!elapsed || !state.automationStarted) return;
    elapsed.className = "panel-status";
    elapsed.textContent = `⏳ Automation running… (${formatElapsed(Math.floor((Date.now() - state.automationStarted) / 1000))} elapsed)`;
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
    return `<details class="zone"><summary>${html(zone)} · ${zoneItems.length}</summary><div class="zone-items">${zoneItems.map((item) => `<label><input type="checkbox"> ${html(item[cols.comida])}</label>`).join("")}</div></details>`;
  }).join("");
  el.main.innerHTML = `<section class="panel">
    <h2>Audio Audit</h2>
    <div id="audio-health-banner" class="panel-status"></div>
    <div class="hint">Keep the checklist visible while recording. Announce the zone, then item counts in Spanish.</div>
    <div class="actions">
      <button id="record-toggle" class="primary">Start Recording</button>
      <button id="audio-redo" class="secondary" type="button" hidden>↻ Redo</button>
      <input id="audio-file" type="file" accept="audio/*">
    </div>
    <div class="hint">Recording streams to the PC as you talk — the take is safe even if the phone dies. ↻ Redo re-transcribes the saved audio.</div>
    <div class="zone-list">${checklist}</div>
  </section>
  <section class="panel">
    <div class="row"><h2>Transcript</h2><button id="transcribe-audio" class="secondary">Transcribe File</button></div>
    <textarea id="transcript" placeholder="Transcript appears here, or paste one manually.">${html(state.transcript)}</textarea>
    <label class="field-label" for="audio-model">Match model
      <select id="audio-model"${modelOptions ? "" : " disabled"}>${modelOptions || `<option>${html(state.audioModel || "config default")}</option>`}</select>
    </label>
    <div id="audio-context" class="hint"></div>
    <div class="actions">
      <button id="match-transcript" class="primary">Match Inventory</button>
      <button id="apply-audio" class="secondary" disabled>Apply Accepted</button>
      <button id="audio-clear" class="secondary" type="button">🧽 Clear</button>
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
  node.textContent = `📡 ${hub} · candidates ${items().length} · model ${state.audioModel || "config default"}`;
}

function renderMatches() {
  const target = document.querySelector("#match-results");
  const apply = document.querySelector("#apply-audio");
  if (!target) return;
  if (!state.matches) {
    target.innerHTML = `<div class="empty">No match results yet.</div>`;
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
    return `<label class="item">
      <span><strong>${html(item[cols.comida])}</strong>${badge}<span class="meta"> ${html(match.evidence || "")}</span></span>
      <span class="match-figures"><span class="meta">${current} →</span> <strong>${proposed}</strong> <span class="${delta > 0 ? "buy" : "meta"}">${deltaTxt}</span>
        <input type="checkbox" data-audio-idx="${match.idx}" data-count="${proposed}" checked></span>
    </label>`;
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
    ? `<section class="panel"><h2>Not mentioned (in audited zones)</h2>
      <div class="hint">${unseen.length} item(s) in the zones you walked but didn't name. Tick to set them to 0.</div>
      <div class="grid">${unseen.map((item) =>
        `<label class="item"><span><strong>${html(item[cols.comida])}</strong> <span class="meta">(list: ${html(item[cols.lugar])})</span></span>
          <span class="match-figures"><span class="meta">${Number(item[cols.tenemos])} → <strong>0</strong></span>
            <input type="checkbox" data-audio-zero="${item.id}"></span></label>`,
      ).join("")}</div></section>`
    : "";

  target.innerHTML = `<section class="panel"><h2>Detected Items</h2>${
    zoneSections || '<div class="empty">No recognised items.</div>'
  }</section>
  ${unseenSection}
  ${state.matches.unmatched_mentions?.length ? `<section class="panel"><h2>Unmatched Mentions</h2>${state.matches.unmatched_mentions.map((m) => `<div class="meta">${html(m.phrase)} · ${html(m.note)}</div>`).join("")}</section>` : ""}`;
  if (apply) apply.disabled = !matched.length && !unseen.length;
}

function render() {
  if (!state.payload) return;
  el.title.textContent = modes.find(([id]) => id === state.mode)?.[1] || "Inventory";
  el.nav.querySelectorAll("[data-mode]").forEach((button) => button.classList.toggle("active", button.dataset.mode === state.mode));
  renderSideStats();
  if (state.mode === "dashboard") renderDashboard();
  if (state.mode === "audit") renderAudit(true);
  if (state.mode === "targets") renderAudit(false);
  if (state.mode === "edit") renderEdit();
  if (state.mode === "add") renderAdd();
  if (state.mode === "shopping") renderShopping();
  if (state.mode === "automation") renderAutomation();
  if (state.mode === "audio") renderAudio();
}

el.main.addEventListener("click", async (event) => {
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

el.main.addEventListener("submit", async (event) => {
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

el.main.addEventListener("change", (event) => {
  if (event.target.id === "audio-model") {
    state.audioModel = event.target.value;
    renderAudioContext();
    return;
  }
  if (["automation-store", "automation-cart-mode", "automation-dry-run", "automation-clean-confirm"].includes(event.target.id)) {
    updateAutomationCommand();
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

el.main.addEventListener("click", async (event) => {
  if (event.target.id === "automation-start") {
    state.automationStarted = Date.now();
    const status = await fetchJson("/api/automation/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        store: document.querySelector("#automation-store").value,
        dry_run: document.querySelector("#automation-dry-run").checked,
        cart_mode: document.querySelector("#automation-cart-mode").value,
      }),
    });
    applyAutomationStatus(status);
    startAutomationTimer();
    connectAutomationEvents();
  }
  if (event.target.id === "automation-stop") await fetchJson("/api/automation/stop", { method: "POST" });
  if (event.target.id === "automation-dismiss") {
    stopAutomationTimer();
    await fetchJson("/api/automation/reset", { method: "POST" }).catch(() => null);
    renderAutomation();
  }
  if (event.target.id === "shopping-unmark-all") {
    state.shopping.bought.clear();
    state.shopping.extraBought = {};
    saveShoppingState();
    render();
  }
  if (event.target.id === "record-toggle") await toggleRecording(event.target);
  if (event.target.id === "audio-redo") await redoTranscribe();
  if (event.target.id === "transcribe-audio") await transcribeAudio();
  if (event.target.id === "match-transcript") await matchTranscript();
  if (event.target.id === "apply-audio") await applyAudio();
  if (event.target.id === "audio-clear") clearAudio();
  if (event.target.id === "audio-cancel" && audioAbort) audioAbort.abort();
});

// Wipe the transcript + match results so the next audit starts from scratch.
function clearAudio() {
  if (audioAbort) audioAbort.abort();
  closeAudioPartialStream();
  state.transcript = "";
  state.matches = null;
  state.audioSha = "";
  state.audioBytes = 0;
  state.audioChunks = [];
  state.sessionId = "";
  state.bytesSent = 0;
  const fileInput = document.querySelector("#audio-file");
  if (fileInput) fileInput.value = "";
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
    button.textContent = "⏳ Finishing…";
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
    setAudioStatus(`🔴 Recording · ${formatElapsed(elapsed)} · ${formatBytes(state.bytesSent)} streamed to PC`);
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
      setAudioStatus("🤫 Empty audio — nothing transcribed", "");
    } else {
      state.transcript = body.transcript || "";
      const field = document.querySelector("#transcript");
      if (field) field.value = state.transcript;
      setAudioStatus("✅ Transcript ready — recording saved on the PC", "ok");
    }
    const redo = document.querySelector("#audio-redo");
    if (redo) redo.hidden = !state.sessionId;
  } catch (error) {
    closeAudioPartialStream();
    if (error.name === "AbortError") {
      setAudioStatus("Finish cancelled — recording is safe on the PC, tap ↻ Redo", "");
    } else {
      setAudioStatus(`Transcription failed: ${error.message} — recording is safe on the PC, tap ↻ Redo`, "error");
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
    setAudioStatus(body.silent ? "🤫 Empty audio — nothing transcribed" : "✅ Re-transcribed from saved audio", body.silent ? "" : "ok");
  } catch (error) {
    if (error.name === "AbortError") setAudioStatus("Redo cancelled", "");
    else setAudioStatus(`Redo failed: ${error.message}`, "error");
  }
}

async function selectedAudioBlob() {
  const input = document.querySelector("#audio-file");
  if (input?.files?.[0]) return input.files[0];
  if (state.audioChunks.length) {
    const type = state.audioMime || state.audioChunks[0]?.type || "audio/webm";
    return new Blob(state.audioChunks, { type });
  }
  return null;
}

async function transcribeAudio() {
  const blob = await selectedAudioBlob();
  if (!blob) {
    setAudioStatus("No audio selected or recorded", "error");
    return;
  }
  await computeAudioSha(blob);
  try {
    const body = await runWithTimer(audioTranscribeStage, async (signal) => {
      const form = new FormData();
      const ext = (blob.type || "").includes("mp4") ? "mp4" : "webm";
      form.append("file", blob, blob.name || `recording.${ext}`);
      const response = await authFetch("/api/audio/transcribe", { method: "POST", body: form, signal });
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      return data;
    });
    state.transcript = body.transcript;
    const field = document.querySelector("#transcript");
    if (field) field.value = body.transcript;
    setAudioStatus("✅ Transcript ready", "ok");
  } catch (error) {
    if (error.name === "AbortError") setAudioStatus("Transcription cancelled", "");
    else setAudioStatus(`Transcription failed: ${error.message}`, "error");
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
      `✅ Matched ${m.items.length} item${m.items.length === 1 ? "" : "s"} · ${m.candidates} candidates · ${m.transcript_chars} chars · ${m.model}`,
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
  document.querySelectorAll("[data-audio-idx]:checked").forEach((box) => {
    updates[box.dataset.audioIdx] = Number(box.dataset.count);
  });
  document.querySelectorAll("[data-audio-zero]:checked").forEach((box) => {
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
    setAudioStatus(logPath ? `✅ Inventory updated · 📝 log ${logPath.split(/[\\/]/).pop()}` : "✅ Inventory updated", "ok");
  } catch (error) {
    setAudioStatus(`Apply failed: ${error.message}`, "error");
    if (button) button.disabled = false;
  }
}

el.search.addEventListener("input", () => { state.query = el.search.value.trim().toLowerCase(); render(); });
el.refresh.addEventListener("click", loadInventory);
el.openSheet.addEventListener("click", () => fetchJson("/api/actions/open-spreadsheet", { method: "POST" }).then(() => setStatus("Spreadsheet opened")));
el.copyLink.addEventListener("click", async () => {
  const url = state.access?.cloudflare || state.access?.lan || window.location.href;
  await navigator.clipboard.writeText(url);
  setStatus("Link copied");
});
el.exportCsv.addEventListener("click", () => { window.location.href = "/api/export.csv"; });
el.closeApp.addEventListener("click", () => { if (confirm("Close the FastAPI app?")) fetchJson("/api/actions/close", { method: "POST" }); });

el.themeToggle.addEventListener("click", toggleTheme);

captureTokenFromURL();
applyTheme(currentTheme());
initNav();
loadInventory();
