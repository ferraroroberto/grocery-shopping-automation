const TOKEN_KEY = "grocery.authToken";
const SHOP_STATE_KEY = "grocery.shoppingState";

const modes = [
  ["dashboard", "Inventory"],
  ["audit", "Audit"],
  ["targets", "Targets"],
  ["edit", "Edit Item"],
  ["add", "Add Item"],
  ["shopping", "Shopping"],
  ["automation", "Automation"],
  ["audio", "Audio Audit"],
];

const state = {
  payload: null,
  access: null,
  mode: "dashboard",
  query: "",
  zone: "",
  automationSource: null,
  mediaRecorder: null,
  audioChunks: [],
  transcript: "",
  matches: null,
  shopping: loadShoppingState(),
};

const el = {
  nav: document.querySelector("#nav"),
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
    <div class="qty"><div>${html(item[cols.tenemos])}/${html(item[cols.cantidad])}</div><div class="${buy > 0 ? "buy" : "ok"}">${buy > 0 ? `Buy ${buy}` : "Stocked"}</div></div>
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
    .filter((item) => !targetsOnly || Number(item[cols.cantidad]) > 0));
  el.main.innerHTML = `<section class="panel"><div class="row"><h2>${targetsOnly ? "Audit Inventory" : "Edit Targets"}</h2><span class="hint">${html(state.zone)}</span></div>${zoneTabs()}</section>
    <section class="grid">${source.map((item) => `
      <article class="item" data-id="${item.id}">
        <div><h3>${html(item[cols.comida])}</h3><div class="meta">${html(item[cols.super])}</div></div>
        <div class="item-actions">
          ${targetsOnly ? `<button class="icon-btn" data-action="current-minus">-</button><button class="icon-btn" data-action="current-plus">+</button>` : ""}
          <span class="qty">${item[cols.tenemos]}/${item[cols.cantidad]}</span>
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
        <div class="three">
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
      <div class="three">
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
  el.main.innerHTML = stores.map((store) => {
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
  el.main.innerHTML = `<section class="panel">
    <h2>Run Automation</h2>
    <div class="three">
      <select id="automation-store" class="field"><option value="all">All stores</option>${state.payload.summary.supermarkets.map((s) => `<option value="${html(s)}">${html(s)}</option>`).join("")}</select>
      <select id="automation-cart-mode" class="field"><option value="keep">Keep cart</option><option value="clean">Clean cart</option></select>
      <label class="hint"><input id="automation-dry-run" type="checkbox" checked> Dry run</label>
    </div>
    <div class="row">
      <button id="automation-start" class="primary" type="button">Run Automation</button>
      <button id="automation-stop" class="danger" type="button">Stop</button>
    </div>
    <pre id="automation-log" class="log">(not running)</pre>
  </section>`;
  refreshAutomation();
}

async function refreshAutomation() {
  const log = document.querySelector("#automation-log");
  if (!log) return;
  const status = await fetchJson("/api/automation/status");
  log.textContent = status.lines?.join("\n") || (status.running ? "(waiting for output...)" : "(not running)");
}

function connectAutomationEvents() {
  if (state.automationSource) state.automationSource.close();
  state.automationSource = new EventSource("/api/automation/events");
  state.automationSource.onmessage = (event) => {
    const status = JSON.parse(event.data);
    const log = document.querySelector("#automation-log");
    if (log) log.textContent = status.lines?.join("\n") || "(waiting for output...)";
    if (!status.running) state.automationSource.close();
  };
}

function renderAudio() {
  const cols = c();
  const checklist = state.payload.summary.zones.map((zone) => {
    const zoneItems = items().filter((item) => item[cols.lugar] === zone && Number(item[cols.cantidad]) > 0);
    return `<details><summary>${html(zone)} · ${zoneItems.length}</summary><div class="grid">${zoneItems.map((item) => `<label><input type="checkbox"> ${html(item[cols.comida])}</label>`).join("")}</div></details>`;
  }).join("");
  el.main.innerHTML = `<section class="panel">
    <h2>Audio Audit</h2>
    <div class="row"><button id="record-toggle" class="primary">Start Recording</button><input id="audio-file" type="file" accept="audio/*"></div>
    <div class="hint">Keep the checklist visible while recording. Announce the zone, then item counts in Spanish.</div>
    <div class="grid">${checklist}</div>
  </section>
  <section class="panel">
    <div class="row"><h2>Transcript</h2><button id="transcribe-audio" class="secondary">Transcribe Upload/Recording</button></div>
    <textarea id="transcript" placeholder="Transcript appears here, or paste one manually.">${html(state.transcript)}</textarea>
    <div class="row"><button id="match-transcript" class="primary">Match Inventory</button><button id="apply-audio" class="secondary" disabled>Apply Accepted</button></div>
  </section>
  <section id="match-results" class="grid"></section>`;
  renderMatches();
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
  const byId = new Map(items().map((item) => [item.id, item]));
  target.innerHTML = `<section class="panel"><h2>Detected Items</h2><div class="grid">${state.matches.items.map((match) => {
    const item = byId.get(match.idx);
    if (!item) return "";
    return `<label class="item"><span><strong>${html(item[cols.comida])}</strong><span class="meta"> ${html(item[cols.lugar])} · ${html(match.evidence)}</span></span><span><input type="checkbox" data-audio-idx="${match.idx}" data-count="${match.count}" checked> set ${match.count}</span></label>`;
  }).join("") || '<div class="empty">No recognised items.</div>'}</div></section>
  ${state.matches.unmatched_mentions?.length ? `<section class="panel"><h2>Unmatched Mentions</h2>${state.matches.unmatched_mentions.map((m) => `<div class="meta">${html(m.phrase)} · ${html(m.note)}</div>`).join("")}</section>` : ""}`;
  if (apply) apply.disabled = !state.matches.items.length;
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
    await fetchJson("/api/automation/start", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        store: document.querySelector("#automation-store").value,
        dry_run: document.querySelector("#automation-dry-run").checked,
        cart_mode: document.querySelector("#automation-cart-mode").value,
      }),
    });
    connectAutomationEvents();
  }
  if (event.target.id === "automation-stop") await fetchJson("/api/automation/stop", { method: "POST" });
  if (event.target.id === "record-toggle") toggleRecording(event.target);
  if (event.target.id === "transcribe-audio") transcribeAudio();
  if (event.target.id === "match-transcript") matchTranscript();
  if (event.target.id === "apply-audio") applyAudio();
});

async function toggleRecording(button) {
  if (state.mediaRecorder && state.mediaRecorder.state === "recording") {
    state.mediaRecorder.stop();
    button.textContent = "Start Recording";
    return;
  }
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  state.audioChunks = [];
  state.mediaRecorder = new MediaRecorder(stream);
  state.mediaRecorder.ondataavailable = (event) => { if (event.data.size) state.audioChunks.push(event.data); };
  state.mediaRecorder.onstop = () => stream.getTracks().forEach((track) => track.stop());
  state.mediaRecorder.start();
  button.textContent = "Stop Recording";
}

async function selectedAudioBlob() {
  const input = document.querySelector("#audio-file");
  if (input?.files?.[0]) return input.files[0];
  if (state.audioChunks.length) return new Blob(state.audioChunks, { type: "audio/webm" });
  return null;
}

async function transcribeAudio() {
  const blob = await selectedAudioBlob();
  if (!blob) {
    setStatus("No audio selected or recorded");
    return;
  }
  setStatus("Transcribing audio...");
  const form = new FormData();
  form.append("file", blob, blob.name || "recording.webm");
  const response = await authFetch("/api/audio/transcribe", { method: "POST", body: form });
  if (!response.ok) throw new Error((await response.json()).detail || "transcription failed");
  const body = await response.json();
  state.transcript = body.transcript;
  document.querySelector("#transcript").value = body.transcript;
  setStatus("Transcript ready");
}

async function matchTranscript() {
  const transcript = document.querySelector("#transcript").value.trim();
  state.transcript = transcript;
  setStatus("Matching transcript...");
  state.matches = await fetchJson("/api/audio/match", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ transcript }),
  });
  setStatus(`Matched ${state.matches.items.length} items`);
  renderMatches();
}

async function applyAudio() {
  const updates = {};
  document.querySelectorAll("[data-audio-idx]:checked").forEach((box) => {
    updates[box.dataset.audioIdx] = Number(box.dataset.count);
  });
  state.payload = await fetchJson("/api/audio/apply", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ updates }),
  });
  state.matches = null;
  setStatus("Inventory updated");
  render();
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

captureTokenFromURL();
initNav();
loadInventory();
