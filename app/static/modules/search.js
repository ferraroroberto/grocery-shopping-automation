// On-demand product search (issue #87).
//
// Speak or type a product (Spanish); search both stores; the user validates a
// candidate card (each with a link to see it) to fill that item's `buscador`.
// No automated decision — nothing is written until you tap "Usar".
import { authFetch, fetchJson } from "./api.js";
import { activePaneBody, c, defaultZone, items, state } from "./core.js";
import { formatElapsed, html, text } from "./dom.js";
import { pickAudioMime } from "./media.js";

// Local to this module — `items` holds the merged status entries (one per
// searched term, each with its candidate cards).
const search = {
  term: "", running: false, items: [], error: "", startedAt: 0,
  pollTimer: null, recorder: null, chunks: [], recording: false, notice: "",
  resolved: {}, progress: "", pendingStart: false, confirming: "", draft: null,
};

function searchStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `Abriendo el navegador… (${t})`;
  if (elapsed < 20) return `Buscando en las tiendas… (${t})`;
  if (elapsed < 45) return `Buscando en las tiendas… (${t}) — suele tardar 15–40 s`;
  return `Sigo buscando… (${t}) — a veces tarda 1–2 min`;
}

export function renderSearch() {
  if (state.mode !== "search") return; // a background poll must never clobber another pane
  const s = search;
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
  const s = search;
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
  const s = search;
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
    <div class="candidate-list">${cands.map((cand) => candidateRow(cand, item)).join("")}</div>${errNote}</section>`;
}

function candidateKey(term, productUrl) {
  return `${term}::${productUrl}`;
}

// The staged confirm row under a tapped candidate: zone combo + present/target
// quantities (issue #92) — supermarket and URL come from the candidate itself.
function candidateConfirmPanel() {
  const d = search.draft || { lugar: "", tenemos: 0, cantidad: 1 };
  const zones = state.payload?.summary?.zones || [];
  const zoneField = zones.length
    ? `<select class="field" data-confirm="lugar" aria-label="Zona">${zones.map((z) =>
        `<option value="${html(z)}"${z === d.lugar ? " selected" : ""}>${html(z)}</option>`).join("")}</select>`
    : `<input class="field" data-confirm="lugar" value="${html(d.lugar)}" placeholder="Zona" aria-label="Zona" />`;
  return `<div class="candidate-confirm">
    <label class="field-label">Zona ${zoneField}</label>
    <label class="field-label">Tengo
      <input class="field" data-confirm="tenemos" type="number" min="0" inputmode="numeric" value="${html(d.tenemos)}" />
    </label>
    <label class="field-label">Objetivo
      <input class="field" data-confirm="cantidad" type="number" min="0" inputmode="numeric" value="${html(d.cantidad)}" />
    </label>
    <button class="big-btn candidate-confirm-add" type="button" data-action="search-confirm">Añadir</button>
  </div>`;
}

function candidateRow(cand, item) {
  const key = candidateKey(item.term, cand.product_url);
  const done = search.resolved[key];
  const open = search.confirming === key;
  const chip = cand.match === "strong" ? '<span class="chip chip-match">coincide</span>' : "";
  const thumb = cand.thumbnail
    ? `<img class="candidate-thumb" src="${html(cand.thumbnail)}" alt="" loading="lazy" />`
    : `<div class="candidate-thumb candidate-thumb-empty" aria-hidden="true"></div>`;
  return `<article class="candidate${open ? " confirming" : ""}" data-term="${html(item.term)}" data-idx="${item.inventory_idx == null ? "" : item.inventory_idx}"
      data-store="${html(cand.store)}" data-url="${html(cand.product_url)}" data-name="${html(cand.name)}">
    ${thumb}
    <div class="candidate-main">
      <div class="candidate-name">${html(cand.name)}${chip}</div>
      <div class="meta">${html(cand.store)}${cand.price_text ? " · " + html(cand.price_text) : ""}</div>
    </div>
    <div class="candidate-actions">
      <a class="icon-btn hit-target" href="${html(cand.product_url)}" target="_blank" rel="noopener" aria-label="Ver producto" title="Ver">
        <svg class="icon" aria-hidden="true" focusable="false"><use href="#i-external-link"></use></svg>
      </a>
      <button class="secondary candidate-use" type="button" data-action="search-use" aria-expanded="${open}"${done ? " disabled" : ""}>${done ? "Añadido ✓" : "Usar"}</button>
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
  const s = search;
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

export async function startProductSearch() {
  const s = search;
  const term = (document.querySelector("#search-term")?.value ?? s.term).trim();
  if (!term) { s.error = "Di o escribe un producto"; renderSearchStatus(); return; }
  Object.assign(s, {
    term, error: "", notice: "", items: [], resolved: {}, running: true,
    startedAt: Date.now(), progress: "", pendingStart: true, confirming: "", draft: null,
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
  if (status.state === "error") s.error = status.error || "La búsqueda falló";
  renderSearch();
}

export async function cancelProductSearch() {
  search.running = false;
  stopSearchPoll();
  await fetchJson("/api/product-search/cancel", { method: "POST" }).catch(() => null);
  search.notice = "Búsqueda cancelada";
  renderSearch();
}

// "Usar" stages the add: it opens (or closes) the confirm row, prefilled from
// the existing inventory row when there is one, else zone guess + 0/1 defaults.
export function toggleCandidateConfirm(cardEl) {
  if (!cardEl) return;
  const s = search;
  const key = candidateKey(cardEl.dataset.term, cardEl.dataset.url);
  if (s.confirming === key) {
    s.confirming = "";
    s.draft = null;
  } else {
    const cols = c();
    const idxRaw = cardEl.dataset.idx;
    const existing = idxRaw === "" ? null : items().find((it) => it.id === Number(idxRaw));
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
  if (btn) { btn.disabled = true; btn.textContent = "Guardando…"; }
  try {
    state.payload = await fetchJson("/api/product-search/select", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    s.resolved[candidateKey(payload.term, payload.product_url)] = true;
    s.confirming = "";
    s.draft = null;
    s.notice = `Añadido: ${payload.name} (${payload.store}) → ${payload.lugar || "sin zona"} · ${payload.tenemos}/${payload.cantidad}`;
    renderSearch();
  } catch (err) {
    s.error = `No se pudo guardar: ${err.message}`;
    if (btn) { btn.disabled = false; btn.textContent = "Añadir"; }
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
