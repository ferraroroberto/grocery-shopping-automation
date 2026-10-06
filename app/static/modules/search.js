// On-demand product search (issue #87) — the "Find a store product" section at
// the top of Items → Add Item (#182; it was the Search tab).
//
// Speak or type a product (Spanish — the stores are); search every store; the
// user validates a candidate card (each with a link to see it). Nothing is
// auto-picked and nothing is written until you confirm:
//   - a term already on the list → "Use" opens a confirm row (zone / have /
//     target) and "Update item" posts /api/product-search/select, which fills
//     that row's `super` + `buscador`;
//   - a new term → "Use" pre-fills the Add Item form below (name, store, link,
//     target 1) and the form's own "Add Item" creates the row — the same
//     POST /api/items shape /select would build.
import { authFetch, fetchJson } from "./api.js";
import { c, defaultZone, items, state } from "./core.js";
import { formatElapsed, html, text } from "./dom.js";
import { pickAudioMime } from "./media.js";

// Local to this module — `items` holds the merged status entries (one per
// searched term, each with its candidate cards). `prefilled` is the candidate
// key last copied into the Add Item form, marked Added once that form saves it.
const search = {
  term: "", running: false, items: [], error: "", startedAt: 0,
  pollTimer: null, recorder: null, chunks: [], recording: false, notice: "",
  resolved: {}, progress: "", pendingStart: false, confirming: "", draft: null,
  prefilled: null,
};

function searchStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `Opening the browser… (${t})`;
  if (elapsed < 20) return `Searching the stores… (${t})`;
  if (elapsed < 45) return `Searching the stores… (${t}) — usually takes 15–40 s`;
  return `Still searching… (${t}) — sometimes takes 1–2 min`;
}

// Renders into the #product-search host that renderAdd() lays out, never the
// whole pane body — a search poll must not wipe what you typed in the form.
export function renderSearch() {
  const node = document.querySelector("#product-search");
  if (!node || state.mode !== "add") return; // a background poll must never clobber another pane
  const s = search;
  node.innerHTML = `
    <h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-search"></use></svg>Find a store product</h2>
    <div class="hint">Say or type a product in Spanish. Searches Mercadona, Ametller and Carrefour — pick the right one to add it to the list, or to link an item that is already on it.</div>
    <div class="search-bar">
      <button id="search-record" class="icon-button${s.recording ? " recording" : ""}" type="button" aria-pressed="${s.recording}" aria-label="Dictate a product" title="Dictate">
        <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-mic"></use></svg>
      </button>
      <input id="search-term" class="search-term" type="search" enterkeyhint="search" autocomplete="off"
             aria-label="Product to search" placeholder="e.g. sandía" value="${html(s.term)}"${s.running ? " disabled" : ""} />
      <button id="search-run" class="big-btn"${s.running ? " disabled" : ""} type="button">
        <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-search"></use></svg>Search
      </button>
    </div>
    <div id="search-status" class="panel-status" role="status" aria-live="polite"></div>
    <button id="search-cancel" class="secondary btn-block" type="button"${s.running ? "" : " hidden"}>Cancel</button>
    <div id="search-results"></div>`;
  renderSearchStatus();
  renderSearchResults();
}

function renderSearchStatus() {
  const node = document.querySelector("#search-status");
  if (!node) return;
  const s = search;
  node.className = "panel-status";
  if (s.recording) { node.textContent = "Recording… tap the mic to stop"; return; }
  if (s.error) { node.className = "panel-status error"; node.textContent = s.error; return; }
  if (s.running) {
    const t = formatElapsed(Math.floor((Date.now() - s.startedAt) / 1000));
    // Prefer the real backend phase (Searching Mercadona…, N results,
    // Preparing…) over the generic time-based stage text.
    node.textContent = s.progress ? `${s.progress} · ${t}` : searchStage(Math.floor((Date.now() - s.startedAt) / 1000));
    return;
  }
  if (s.notice) { node.className = "panel-status ok"; node.textContent = s.notice; return; }
  node.textContent = "";
}

function renderSearchResults() {
  const wrap = document.querySelector("#search-results");
  if (!wrap) return;
  const s = search;
  const anyCandidates = s.items.some((i) => (i.candidates || []).length);
  if (s.running && !anyCandidates) { wrap.replaceChildren(); return; }
  wrap.innerHTML = s.items.map(searchItemGroup).join("");
}

function searchItemGroup(item) {
  const cands = item.candidates || [];
  const tag = item.inventory_idx == null
    ? '<span class="chip chip-new">New</span>'
    : '<span class="meta">Already on the list</span>';
  const header = `<div class="search-group-head"><span class="search-group-term">${html(item.term)}</span>${tag}</div>`;
  // Which stores couldn't be reached (session expired, network) — so a missing
  // store reads as "couldn't check", not "nothing there".
  const failed = Object.keys(item.store_errors || {}).map((s) => s[0].toUpperCase() + s.slice(1));
  const errNote = failed.length
    ? `<div class="panel-status">Couldn't check ${failed.join(" and ")} (session or network).</div>` : "";
  if (!cands.length) {
    return `<section class="search-group card">${header}
      <div class="panel-status">No results for “${html(item.term)}” — try another word.</div>${errNote}</section>`;
  }
  return `<section class="search-group card">${header}
    <div class="candidate-list">${cands.map((cand) => candidateRow(cand, item)).join("")}</div>${errNote}</section>`;
}

function candidateKey(term, productUrl) {
  return `${term}::${productUrl}`;
}

// The staged confirm row under a tapped candidate for an item already on the
// list: zone combo + present/target quantities (issue #92) — supermarket and
// URL come from the candidate itself.
function candidateConfirmPanel() {
  const d = search.draft || { lugar: "", tenemos: 0, cantidad: 1 };
  const zones = state.payload?.summary?.zones || [];
  const zoneField = zones.length
    ? `<select class="field" data-confirm="lugar" aria-label="Zone">${zones.map((z) =>
        `<option value="${html(z)}"${z === d.lugar ? " selected" : ""}>${html(z)}</option>`).join("")}</select>`
    : `<input class="field" data-confirm="lugar" value="${html(d.lugar)}" placeholder="Zone" aria-label="Zone" />`;
  return `<div class="candidate-confirm">
    <label class="field-label">Zone ${zoneField}</label>
    <label class="field-label">Have
      <input class="field" data-confirm="tenemos" type="number" min="0" inputmode="numeric" value="${html(d.tenemos)}" />
    </label>
    <label class="field-label">Target
      <input class="field" data-confirm="cantidad" type="number" min="0" inputmode="numeric" value="${html(d.cantidad)}" />
    </label>
    <button class="big-btn candidate-confirm-add" type="button" data-action="search-confirm">Update item</button>
  </div>`;
}

function candidateRow(cand, item) {
  const key = candidateKey(item.term, cand.product_url);
  const done = search.resolved[key];
  const open = search.confirming === key;
  const isNew = item.inventory_idx == null;
  const chip = cand.match === "strong" ? '<span class="chip chip-match">Match</span>' : "";
  const thumb = cand.thumbnail
    ? `<img class="candidate-thumb" src="${html(cand.thumbnail)}" alt="" loading="lazy" />`
    : `<div class="candidate-thumb candidate-thumb-empty" aria-hidden="true"></div>`;
  // New items hand off to the Add Item form (no confirm row to expand).
  const useLabel = done ? `${isNew ? "Added" : "Updated"} <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-check"></use></svg>` : "Use";
  const expanded = isNew ? "" : ` aria-expanded="${open}"`;
  return `<article class="candidate${open ? " confirming" : ""}" data-term="${html(item.term)}" data-idx="${isNew ? "" : item.inventory_idx}"
      data-store="${html(cand.store)}" data-url="${html(cand.product_url)}" data-name="${html(cand.name)}">
    ${thumb}
    <div class="candidate-main">
      <div class="candidate-name">${html(cand.name)}${chip}</div>
      <div class="meta">${html(cand.store)}${cand.price_text ? " · " + html(cand.price_text) : ""}</div>
    </div>
    <div class="candidate-actions">
      <a class="icon-button" href="${html(cand.product_url)}" target="_blank" rel="noopener" aria-label="Open product" title="Open">
        <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-external-link"></use></svg>
      </a>
      <button class="secondary candidate-use" type="button" data-action="search-use"${expanded}${done ? " disabled" : ""}>${useLabel}</button>
    </div>
    ${open ? candidateConfirmPanel() : ""}
  </article>`;
}

export async function toggleSearchRecording(button) {
  const s = search;
  if (s.recording && s.recorder) { s.recorder.stop(); return; }
  let stream;
  try {
    stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  } catch (_) {
    s.error = "Microphone permission denied"; renderSearchStatus(); return;
  }
  const mime = pickAudioMime();
  const rec = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
  s.chunks = [];
  rec.ondataavailable = (e) => { if (e.data && e.data.size) s.chunks.push(e.data); };
  rec.onstop = async () => {
    stream.getTracks().forEach((t) => t.stop());
    s.recording = false;
    button.classList.remove("recording");
    button.setAttribute("aria-pressed", "false");
    await transcribeSearchClip(mime);
  };
  s.recorder = rec;
  s.recording = true;
  s.error = "";
  s.notice = "";
  button.classList.add("recording");
  button.setAttribute("aria-pressed", "true");
  renderSearchStatus();
  rec.start();
}

async function transcribeSearchClip(mime) {
  const s = search;
  const status = document.querySelector("#search-status");
  if (status) { status.className = "panel-status"; status.textContent = "Transcribing…"; }
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
    s.error = `Couldn't transcribe: ${err.message}`;
    renderSearchStatus();
  }
}

export async function startProductSearch() {
  const s = search;
  const term = (document.querySelector("#search-term")?.value ?? s.term).trim();
  if (!term) { s.error = "Say or type a product"; renderSearchStatus(); return; }
  Object.assign(s, {
    term, error: "", notice: "", items: [], resolved: {}, running: true,
    startedAt: Date.now(), progress: "", pendingStart: true, confirming: "", draft: null, prefilled: null,
  });
  renderSearch();
  startSearchPoll();
  try {
    const status = await fetchJson("/api/product-search/start", {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: term }),
    });
    s.pendingStart = false;
    applySearchStatus(status);
  } catch (err) {
    s.pendingStart = false;
    s.running = false; stopSearchPoll(); s.error = err.message; renderSearch();
  }
}

function startSearchPoll() {
  stopSearchPoll();
  const tick = async () => {
    renderSearchStatus();
    // Until /start returns, /status still describes the PREVIOUS run — applying
    // it would resurrect the old term's results under the new search (issue #92).
    if (search.pendingStart) return;
    const status = await fetchJson("/api/product-search/status").catch(() => null);
    if (status) applySearchStatus(status);
  };
  search.pollTimer = window.setInterval(tick, 1000);
}

function stopSearchPoll() {
  if (search.pollTimer) { window.clearInterval(search.pollTimer); search.pollTimer = null; }
}

function applySearchStatus(status) {
  const s = search;
  // "idle" = the run isn't registered yet (the /start call is still parsing the
  // utterance). Keep our optimistic running state; don't stop the poll early.
  if (!status || status.state === "idle") return;
  s.progress = status.progress || "";
  s.items = status.items || [];
  if (status.state === "running") { s.running = true; renderSearchResults(); renderSearchStatus(); return; }
  s.running = false;
  stopSearchPoll();
  if (status.state === "error") s.error = status.error || "The search failed";
  renderSearch();
}

export async function cancelProductSearch() {
  search.running = false;
  stopSearchPoll();
  await fetchJson("/api/product-search/cancel", { method: "POST" }).catch(() => null);
  search.notice = "Search cancelled";
  renderSearch();
}

// "Use" on a card: a new term pre-fills the Add Item form; an item already on
// the list opens (or closes) its confirm row, prefilled from the current row.
export function useCandidateClick(cardEl) {
  if (!cardEl) return;
  if (cardEl.dataset.idx === "") prefillAddForm(cardEl);
  else toggleCandidateConfirm(cardEl);
}

// Copy the candidate into the Add Item form: the name is the searched term
// (what /select names a new row), the store and link come from the card, and
// the target starts at 1 — a target of 0 would leave it unbuyable. The zone
// and quantities stay yours to set; nothing is saved until "Add Item".
function prefillAddForm(cardEl) {
  const form = document.querySelector("#add-form");
  if (!form) return;
  const f = form.elements;
  f.comida.value = cardEl.dataset.term;
  f.super.value = cardEl.dataset.store.toLowerCase();
  f.buscador.value = cardEl.dataset.url;
  if (!(Number(f.cantidad.value) > 0)) f.cantidad.value = "1";
  search.prefilled = { key: candidateKey(cardEl.dataset.term, cardEl.dataset.url), url: cardEl.dataset.url, name: cardEl.dataset.name, store: cardEl.dataset.store };
  search.confirming = "";
  search.draft = null;
  search.error = "";
  search.notice = `Filled in the Add Item form below with ${cardEl.dataset.name} (${cardEl.dataset.store}) — pick a zone, then tap Add Item.`;
  renderSearchStatus();
  (form.closest(".panel") || form).scrollIntoView({ behavior: "smooth", block: "start" });
}

// Called after the Add Item form saved: if it saved the pre-filled candidate
// (same link), mark that card Added so it can't be added twice.
export function markPrefillAdded(savedUrl) {
  const p = search.prefilled;
  search.prefilled = null;
  if (!p || String(savedUrl || "").trim() !== p.url) return;
  search.resolved[p.key] = true;
  search.error = "";
  search.notice = `Added: ${p.name} (${p.store})`;
  renderSearch();
}

function toggleCandidateConfirm(cardEl) {
  const s = search;
  const key = candidateKey(cardEl.dataset.term, cardEl.dataset.url);
  if (s.confirming === key) {
    s.confirming = "";
    s.draft = null;
  } else {
    const cols = c();
    const existing = items().find((it) => it.id === Number(cardEl.dataset.idx));
    s.confirming = key;
    s.draft = existing
      ? {
          lugar: text(existing[cols.lugar]) === "-" ? defaultZone() : existing[cols.lugar],
          tenemos: Number(existing[cols.tenemos]) || 0,
          cantidad: Math.max(Number(existing[cols.cantidad]) || 0, 1),
        }
      : { lugar: defaultZone(), tenemos: 0, cantidad: 1 };
  }
  renderSearchResults();
}

// Confirm row → /select: fills the existing row's `super` + `buscador` and
// applies the staged zone and quantities.
export async function useCandidate(cardEl) {
  if (!cardEl) return;
  const s = search;
  const idxRaw = cardEl.dataset.idx;
  const d = s.draft || { lugar: defaultZone(), tenemos: 0, cantidad: 1 };
  const payload = {
    term: cardEl.dataset.term,
    store: cardEl.dataset.store,
    product_url: cardEl.dataset.url,
    name: cardEl.dataset.name,
    inventory_idx: idxRaw === "" ? null : Number(idxRaw),
    lugar: String(d.lugar ?? ""),
    tenemos: Math.max(Number(d.tenemos) || 0, 0),
    cantidad: Math.max(Number(d.cantidad) || 0, 0),
  };
  const btn = cardEl.querySelector(".candidate-confirm-add");
  if (btn) { btn.disabled = true; btn.textContent = "Saving…"; }
  try {
    state.payload = await fetchJson("/api/product-search/select", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    s.resolved[candidateKey(payload.term, payload.product_url)] = true;
    s.confirming = "";
    s.draft = null;
    s.notice = `Updated: ${payload.name} (${payload.store}) → ${payload.lugar || "no zone"} · ${payload.tenemos}/${payload.cantidad}`;
    renderSearch();
  } catch (err) {
    s.error = `Couldn't save: ${err.message}`;
    if (btn) { btn.disabled = false; btn.textContent = "Update item"; }
    renderSearchStatus();
  }
}

// The term box and the staged confirm-row fields live inside this module's
// state, so app.js's input delegation hands their edits straight here.
export function handleSearchInput(target) {
  if (target.id === "search-term") search.term = target.value;
  const field = target.dataset?.confirm;
  if (field && search.draft) search.draft[field] = target.value;
}
