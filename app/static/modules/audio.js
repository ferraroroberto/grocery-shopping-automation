// Audio audit — Audit's Voice segment: record a spoken walk-through,
// transcribe it, match what was said to inventory rows via the LLM hub, and
// apply the accepted counts.
//
// The view reads top to bottom: the Voice glance (when did I last count? the
// one Record button, a waveform and timer while recording), the match results
// once there are some (their glance with Apply, the one primary, then "Needs
// you" first and the detected items by zone), the checklist card, and the
// transcript card, whose menu holds the match model and Clear.
//
// Recording is hardened: every 1 s chunk is streamed to the PC and
// archived to disk by the voice-transcriber app the moment it arrives, so the
// take survives a dying phone. Rolling partials flow back over SSE; Stop yields
// the canonical transcript. grocery only proxies — VT owns the audio.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { authFetch, fetchJson } from "./api.js";
import { confirmSheet } from "./confirm.js";
import { activePaneBody, c, items, render, state, storedToken, updateHeaders } from "./core.js";
import { esc, formatBytes, formatElapsed, html, icon, shortDate, switchMarkup, text } from "./dom.js";
import { pickAudioMime } from "./media.js";
import { itemList, itemRow } from "./rows.js";
import { dialogShell } from "./stores/dialog.js";

const MENU_ID = "transcript-sheet";

// All of it local to this module — nothing outside the audio view reads it.
const audio = {
  mediaRecorder: null,
  mime: "",
  transcript: "",
  model: "",
  health: null,
  bytes: 0,
  stream: null,
  sessionId: "",
  uploadChain: Promise.resolve(),
  pendingUploads: 0,
  bytesSent: 0,
  recordStartedAt: 0,
  recordTimer: null,
  eventSource: null,
  matches: null,
  // Every row's name as of the match: Apply names the item it means for each id,
  // so a reload under the review can't re-aim it at whatever row now holds the id.
  names: {},
  // The review's choices, kept here so a hop to Manual (and back) never
  // re-ticks a match you unticked or drops a count you typed.
  picks: { accept: {}, zero: {}, counts: {} },
  last: undefined, // the last applied audit: undefined while loading, null when there is none
  wave: { ctx: null, frame: 0 },
};

let audioAbort = null;

function setAudioStatus(message, kind = "") {
  const target = document.querySelector("#audio-status");
  if (target) {
    target.textContent = message;
    target.className = `panel-status${kind ? ` ${kind}` : ""}`;
  }
}

function isRecording() {
  return audio.mediaRecorder?.state === "recording";
}

// Staged progress text, mirroring app/audio_audit.py _extract_progress — budget
// up to ~10 minutes; never imply a call is fast.
function audioMatchStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `Sending the transcript… (${t})`;
  if (elapsed < 20) return `Reading the transcript… (${t})`;
  if (elapsed < 60) return `Matching what you said to your list… (${t}) — typical 30s–2min`;
  if (elapsed < 180) return `Still working… (${t}) — long noisy transcripts take 2–4 min`;
  return `Still working… (${t}) — patience, can take up to 10 min on the longest walks`;
}

function audioTranscribeStage(elapsed) {
  const t = formatElapsed(elapsed);
  if (elapsed < 5) return `Uploading the audio… (${t})`;
  if (elapsed < 30) return `Transcribing… (${t})`;
  if (elapsed < 120) return `Still transcribing… (${t}) — long clips can take 1–3 min`;
  return `Still transcribing… (${t}) — long audio can take up to 10 min`;
}

function setAudioInFlight(busy) {
  const cancel = document.querySelector("#audio-cancel");
  if (cancel) cancel.hidden = !busy;
  ["#match-transcript", "#apply-audio", "#audio-model"].forEach((sel) => {
    const node = document.querySelector(sel);
    if (node) node.disabled = busy;
  });
  if (!busy) paintApplySummary();
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
    audioAbort = null;
    setAudioInFlight(false);
  }
}

export function cancelAudioRequest() {
  if (audioAbort) audioAbort.abort();
}

async function refreshAudioHealth() {
  try {
    audio.health = await fetchJson("/api/audio/health");
  } catch (_) {
    audio.health = null;
  }
  renderAudioHealth();
}

// Only a problem is shown: an always-on "all reachable" line was noise.
function renderAudioHealth() {
  const banner = document.querySelector("#audio-health-banner");
  if (!banner) return;
  const h = audio.health;
  const problems = [];
  if (h && !h.voice_ok) problems.push("The voice recorder isn't reachable — start the voice-transcriber app");
  if (h && !h.hub_ok) problems.push("The matching service isn't reachable — start local-llm-hub");
  if (h && !h.whisper_ok) {
    const hint = h.whisper_host ? " — local-llm-hub is running it somewhere else" : "";
    problems.push(`Speech-to-text isn't reachable${hint}`);
  }
  banner.className = `panel-status${problems.length ? " error" : ""}`;
  banner.innerHTML = problems.map((line) => `<span class="voice-problem">${icon("circle-alert")}${esc(line)}</span>`).join("");
  if (!h) return;
  const matchBtn = document.querySelector("#match-transcript");
  if (matchBtn && !audioAbort) matchBtn.disabled = !h.hub_ok;
  // Record needs the voice-transcriber webapp up — disable it (and show why)
  // rather than letting a take silently hang. Paste + Match still work.
  const recordBtn = document.querySelector("#record-toggle");
  if (recordBtn && !isRecording()) {
    recordBtn.disabled = !h.voice_ok;
    recordBtn.title = h.voice_ok ? "" : "The voice recorder isn't reachable — start the voice-transcriber app";
  }
}

// The hub's model ids (config's audio_audit.llm_models_available, e.g.
// "gemini_pro") are internal identifiers, not copy (J-07) — humanize for the
// dropdown label and the transcript card's meta; the submitted value stays the
// raw id. A snake_case → Title Case split needs no per-model map to maintain
// as the hub's model list grows.
function humanizeModelId(id) {
  return id ? id.split("_").map((word) => word.charAt(0).toUpperCase() + word.slice(1)).join(" ") : "";
}

function recordLabel(recording) {
  return recording ? `${icon("square")}Stop recording` : `${icon("mic")}Record`;
}

// A glance row: a label and its one figure (the carts glance's row anatomy).
function glanceRow(label, valueId) {
  return `<li class="action-row">
      <div class="action-row-main"><span class="action-row-title">${label}</span></div>
      <span class="action-row-trail action-row-value" id="${valueId}">–</span>
    </li>`;
}

export function renderAudio() {
  const audioCfg = state.payload.audio || { models: [], default_model: "" };
  if (!audio.model) audio.model = audioCfg.default_model || audioCfg.models[0] || "";
  const recording = isRecording();
  activePaneBody().innerHTML = `<section id="voice-glance" class="card voice-glance" aria-labelledby="voice-glance-title">
      <div class="card-head">
        <h2 id="voice-glance-title" class="card-title">${icon("mic")}Voice audit</h2>
        <span class="card-head-meta" id="voice-last-date"></span>
      </div>
      <ul class="action-rows voice-glance-rows">
        ${glanceRow("Counts changed", "voice-last-changed")}
        ${glanceRow("Unmatched", "voice-last-unmatched")}
      </ul>
      <div id="audio-health-banner" class="panel-status" role="status"></div>
      <div id="voice-live" class="voice-live"${recording ? "" : " hidden"}>
        <canvas id="voice-wave" class="voice-wave" aria-hidden="true"></canvas>
        <span id="voice-timer" class="voice-timer"></span>
      </div>
      <div class="voice-actions">
        <button id="record-toggle" data-track="voice.record" class="${audio.matches ? "button-tint" : "button-primary"} btn-block" type="button">${recordLabel(recording)}</button>
        <button id="audio-redo" data-track="voice.redo" class="button-surface btn-block" type="button"${audio.sessionId && !recording ? "" : " hidden"}>${icon("refresh-cw")}Redo transcription</button>
        <button id="audio-cancel" class="button-surface btn-block" type="button" hidden>${icon("x")}Cancel</button>
      </div>
      <div id="audio-status" class="panel-status" role="status"></div>
    </section>
    <div id="match-results" class="voice-results"></div>
    ${checklistCard()}
    <section class="card voice-transcript" aria-labelledby="voice-transcript-title">
      <div class="card-head">
        <h2 id="voice-transcript-title" class="card-title">${icon("pencil")}Transcript</h2>
        <span class="card-head-meta voice-model" id="audio-model-label">${html(humanizeModelId(audio.model) || "Default model")}</span>
        <button id="audio-menu" data-track="voice.menu" class="icon-button" type="button" aria-haspopup="dialog" aria-label="Transcript options" title="Transcript options">${icon("ellipsis")}</button>
      </div>
      <textarea id="transcript" aria-label="Transcript" placeholder="The transcript appears here, or paste one.">${audio.transcript ? html(audio.transcript) : ""}</textarea>
      <button id="match-transcript" data-track="voice.match" class="button-tint btn-block" type="button">${icon("list-checks")}Match</button>
    </section>`;
  paintLastAudit();
  renderMatches();
  // A transcribe or match still running (a hop to Manual and back): its
  // Cancel stays in reach and Match stays locked; the timer refills the status.
  if (audioAbort) setAudioInFlight(true);
  renderAudioHealth();
  refreshAudioHealth();
  loadLastAudit();
}

// The one checklist card: a zone per folded row, its kept items with a check
// each, so nothing is missed while dictating. The checks are for your eyes
// only — nothing reads them.
function checklistCard() {
  const cols = c();
  let total = 0;
  const zones = state.payload.summary.zones.map((zone) => {
    const zoneItems = items().filter((item) => item[cols.lugar] === zone && Number(item[cols.cantidad]) > 0);
    if (!zoneItems.length) return "";
    total += zoneItems.length;
    return `<details class="inline-disclosure checklist-zone">
      <summary class="inline-disclosure-summary">
        <span class="checklist-zone-name">${html(zone)}</span>
        <span class="checklist-count">${zoneItems.length}</span>
        <span class="inline-chevron" aria-hidden="true">›</span>
      </summary>
      <ul class="action-rows checklist-items">${zoneItems.map((item) => `<li class="action-row">
          <div class="action-row-main"><span class="action-row-title">${html(item[cols.comida])}</span></div>
          <span class="action-row-trail">${switchMarkup(false, `Checked ${text(item[cols.comida])}`)}</span>
        </li>`).join("")}</ul>
    </details>`;
  }).join("");
  return `<section class="card voice-checklist" aria-labelledby="voice-checklist-title">
      <div class="card-head">
        <h2 id="voice-checklist-title" class="card-title">${icon("list-checks")}Checklist</h2>
        <span class="card-head-meta">${total} items</span>
      </div>
      ${zones || emptyStateEl("package", "No items to count yet.").outerHTML}
    </section>`;
}

// ----------------------------------------------------------- the last audit
// Also Home's: its This week Audit row and its page-header line.
export async function loadLastAudit() {
  try {
    audio.last = (await fetchJson("/api/audio/last")).last;
  } catch (_) {
    return; // the glance keeps its dashes; Record still works without it
  }
  paintLastAudit();
  updateHeaders();
}

export function lastAudit() {
  return audio.last;
}

export function lastAuditText() {
  return audio.last ? `Last audit ${shortDate(audio.last.timestamp)}` : "No audit yet";
}

function paintLastAudit() {
  const date = document.querySelector("#voice-last-date");
  if (!date) return;
  const last = audio.last;
  if (last !== undefined) date.textContent = lastAuditText();
  document.querySelector("#voice-last-changed").textContent = last ? String(last.changed) : "–";
  document.querySelector("#voice-last-unmatched").textContent = last ? String(last.unmatched) : "–";
}

// ----------------------------------------------------------- the menu sheet
// The transcript card's menu: the match model, and Clear
// behind a confirm. Built once at <body> level, so it carries its own listeners.
function transcriptSheet() {
  return dialogShell(MENU_ID, "Transcript", "Done", (shell) => {
    shell.classList.add("transcript-sheet");
    const done = shell.querySelector(".detail-save-btn");
    done.disabled = false;
    done.addEventListener("click", () => shell.close());
    shell.addEventListener("change", (event) => {
      if (event.target.id === "audio-model") setAudioModel(event.target.value);
    });
    shell.addEventListener("click", async (event) => {
      if (!event.target.closest("#audio-clear")) return;
      const yes = await confirmSheet({
        title: "Clear the transcript?",
        message: "The match results go too. A recording stays saved on the PC.",
        confirmLabel: "Clear",
        danger: true,
      });
      if (!yes) return;
      shell.close();
      clearAudio();
    });
    shell.addEventListener("close", () => document.querySelector("#audio-menu")?.focus());
  });
}

export function openTranscriptMenu() {
  const d = transcriptSheet();
  const audioCfg = state.payload.audio || { models: [] };
  const options = (audioCfg.models || [])
    .map((name) => `<option value="${esc(name)}"${name === audio.model ? " selected" : ""}>${html(humanizeModelId(name))}</option>`)
    .join("");
  d.querySelector(".stores-dialog-body").innerHTML = `
    <label class="field-label">Match model
      <select id="audio-model"${options && !audioAbort ? "" : " disabled"}>${options || `<option>${html(humanizeModelId(audio.model) || "Default model")}</option>`}</select>
    </label>
    <button id="audio-clear" class="button-tint danger btn-block" type="button">${icon("trash-2")}Clear transcript</button>`;
  if (!d.open) d.showModal();
}

function setAudioModel(value) {
  audio.model = value;
  const label = document.querySelector("#audio-model-label");
  if (label) label.textContent = humanizeModelId(value);
}

// ---------------------------------------------------------------- results
// The Audit header's exception: mentions the match tied to no row at all.
export function audioUnmatchedCount() {
  if (!audio.matches) return 0;
  const ids = new Set(items().map((item) => item.id));
  return (audio.matches.unmatched_mentions || []).filter((m) => !Number.isInteger(m.idx) || !ids.has(m.idx)).length;
}

// A review row's trailing control remembers its state (see `picks`).
export function rememberAudioPick(node) {
  const { picks } = audio;
  if (node.id === "transcript") audio.transcript = node.value;
  else if (node.dataset.audioIdx) picks.accept[node.dataset.audioIdx] = node.getAttribute("aria-checked") === "true";
  else if (node.dataset.audioZero) picks.zero[node.dataset.audioZero] = node.getAttribute("aria-checked") === "true";
  else if (node.dataset.audioCountIdx) picks.counts[node.dataset.audioCountIdx] = node.value;
  else return;
  paintApplySummary();
}

function resetPicks() {
  audio.picks = { accept: {}, zero: {}, counts: {} };
  audio.names = {};
}

function group(label, rows) {
  return `<section class="item-group" aria-label="${esc(label)}">
      <h3 class="overline item-group-head">${html(label)}</h3>
      ${itemList(rows)}
    </section>`;
}

function trail(markup) {
  return `<span class="action-row-trail">${markup}</span>`;
}

function renderMatches() {
  updateHeaders();
  const target = document.querySelector("#match-results");
  if (!target) return;
  // After a match, Apply is the view's one primary.
  const record = document.querySelector("#record-toggle");
  if (record) record.className = `${audio.matches ? "button-tint" : "button-primary"} btn-block`;
  if (!audio.matches) {
    target.innerHTML = "";
    return;
  }
  const cols = c();
  const { picks } = audio;
  const clamp = Number(state.payload.audio?.clamp ?? 5);
  const byId = new Map(items().map((item) => [item.id, item]));
  const matched = audio.matches.items.filter((match) => byId.has(match.idx));
  const name = (item) => text(item[cols.comida]);

  // Mentions the speaker made that resolved to a row but carried no count —
  // either the LLM named the row or src/audit_resolve.py did. These
  // are *named* items: they get their own "type the count" row, and they must
  // never reach the zero-list below, which would zero what was just dictated.
  const matchedIdx = new Set(matched.map((m) => m.idx));
  const mentions = audio.matches.unmatched_mentions || [];
  const needCount = mentions.filter((m) =>
    Number.isInteger(m.idx) && byId.has(m.idx) && !matchedIdx.has(m.idx),
  );
  const namedIdx = new Set([...matchedIdx, ...needCount.map((m) => m.idx)]);
  // Whatever is left really is unmatched — no row to apply to. Its row opens
  // Manual searching every zone for what was heard.
  const stillUnmatched = mentions.filter((m) => !Number.isInteger(m.idx) || !byId.has(m.idx));

  // "Needs you" comes first: typing a number *is* the accept on a count row —
  // an empty box leaves the row untouched, so a mention resolved wrongly costs
  // nothing.
  const needRows = needCount.map((m) => {
    const item = byId.get(m.idx);
    const heard = m.phrase && m.phrase.toLowerCase() !== name(item).toLowerCase() ? ` · heard “${m.phrase}”` : "";
    const value = picks.counts[m.idx] ?? (Number.isInteger(m.approx_count) ? m.approx_count : "");
    return itemRow({
      title: item[cols.comida],
      meta: `Count missing · in stock ${Number(item[cols.tenemos]) || 0}${heard}`,
      trail: trail(`<input class="field count-input hit-target" type="number" inputmode="numeric" min="0" step="1"
        value="${esc(value)}" placeholder="?" aria-label="Count for ${esc(name(item))}" data-audio-count-idx="${m.idx}">`),
      attrs: `data-id="${m.idx}"`,
    });
  }).concat(stillUnmatched.map((m) => itemRow({
    title: `“${m.phrase || "…"}”`,
    meta: m.note || "No item matched",
    trail: `<button type="button" class="icon-button action-row-verb action-row-trail voice-find" data-action="audio-find" aria-label="Find “${esc(m.phrase)}” in Manual" title="Find “${esc(m.phrase)}” in Manual">${icon("search")}</button>`,
    attrs: `data-phrase="${esc(m.phrase)}"`,
    action: "audio-find",
  }))).join("");

  // Detected items, grouped by the zone the speaker was in (fall back to lugar).
  const byZone = {};
  matched.forEach((match) => {
    const zone = match.zone || byId.get(match.idx)[cols.lugar] || "—";
    (byZone[zone] ||= []).push(match);
  });
  const detectedRow = (match) => {
    const item = byId.get(match.idx);
    const current = Number(item[cols.tenemos]) || 0;
    const proposed = Math.min(Number(match.count) || 0, (Number(item[cols.cantidad]) || 0) + clamp);
    const listZone = match.zone && match.zone !== item[cols.lugar] ? ` · list: ${text(item[cols.lugar])}` : "";
    const evidence = match.evidence ? ` · ${match.evidence}` : "";
    return itemRow({
      title: item[cols.comida],
      meta: `${current} → ${proposed}${listZone}${evidence}`,
      trail: trail(switchMarkup(picks.accept[match.idx] ?? true, `Accept ${name(item)}`, { "data-audio-idx": match.idx, "data-count": proposed })),
      attrs: `data-id="${match.idx}"`,
    });
  };
  const zoneGroups = Object.keys(byZone).sort().map((zone) => group(zone, byZone[zone].map(detectedRow).join(""))).join("");

  // "Not mentioned" — items in a walked zone with target>0 and current>0 that
  // the speaker didn't name. Tick to zero them.
  const zonesMentioned = new Set((audio.matches.zones_mentioned || []).map((z) => String(z).toLowerCase().trim()));
  const unseen = items().filter((item) =>
    !namedIdx.has(item.id)
    && zonesMentioned.has(String(item[cols.lugar]).toLowerCase().trim())
    && Number(item[cols.cantidad]) > 0
    && Number(item[cols.tenemos]) > 0,
  );
  const unseenRows = unseen.map((item) => itemRow({
    title: item[cols.comida],
    meta: `Not mentioned · ${Number(item[cols.tenemos])} → 0 · ${text(item[cols.lugar])}`,
    trail: trail(switchMarkup(picks.zero[item.id] ?? false, `Set ${name(item)} to zero`, { "data-audio-zero": item.id })),
    attrs: `data-id="${item.id}"`,
  })).join("");

  const needYou = needCount.length + stillUnmatched.length;
  target.innerHTML = `<section id="voice-results" class="card voice-glance" aria-labelledby="voice-results-title">
      <div class="card-head">
        <h2 id="voice-results-title" class="card-title">${icon("circle-check")}Match results</h2>
        <span class="card-head-meta">${html(humanizeModelId(audio.matches.model))}</span>
      </div>
      <ul class="action-rows voice-glance-rows">
        ${glanceRow("Counts to apply", "voice-apply-count")}
        ${glanceRow("Needs you", "voice-need-count")}
      </ul>
      <button id="apply-audio" data-track="voice.apply" class="button-primary btn-block" type="button">Apply</button>
    </section>
    ${needRows ? group("Needs you", needRows) : ""}
    ${zoneGroups || (needRows ? "" : `<section class="card">${emptyStateEl("mic", "No recognised items.").outerHTML}</section>`)}
    ${unseenRows ? group("Not mentioned", unseenRows) : ""}`;
  document.querySelector("#voice-need-count").textContent = String(needYou);
  paintApplySummary();
}

// What Apply would write, read from the review rows: an accepted match, a
// ticked zero, a typed count.
function collectUpdates() {
  const updates = {};
  document.querySelectorAll('[data-audio-idx][aria-checked="true"]').forEach((box) => {
    updates[box.dataset.audioIdx] = Number(box.dataset.count);
  });
  document.querySelectorAll('[data-audio-zero][aria-checked="true"]').forEach((box) => {
    updates[box.dataset.audioZero] = 0;
  });
  document.querySelectorAll("[data-audio-count-idx]").forEach((field) => {
    const value = field.value.trim();
    if (value === "") return;
    const count = Number(value);
    if (Number.isFinite(count) && count >= 0) updates[field.dataset.audioCountIdx] = Math.round(count);
  });
  return updates;
}

function paintApplySummary() {
  const count = document.querySelector("#voice-apply-count");
  if (!count) return;
  const n = Object.keys(collectUpdates()).length;
  count.textContent = String(n);
  const apply = document.querySelector("#apply-audio");
  if (apply && !audioAbort) apply.disabled = n === 0;
}

// Wipe the transcript + match results so the next audit starts from scratch.
function clearAudio() {
  if (audioAbort) audioAbort.abort();
  closeAudioPartialStream();
  audio.transcript = "";
  audio.matches = null;
  resetPicks();
  audio.bytes = 0;
  audio.sessionId = "";
  audio.bytesSent = 0;
  render();
  setAudioStatus("Cleared — ready for a new audit", "");
}

// ------------------------------------------------------------- recording
// The glance's live strip: a level waveform drawn from the mic stream. No
// AudioContext (or it refuses) → no waveform; the timer still runs.
function startWave(stream) {
  const Ctx = window.AudioContext || window.webkitAudioContext;
  let analyser;
  try {
    audio.wave.ctx = new Ctx();
    analyser = audio.wave.ctx.createAnalyser();
    analyser.fftSize = 512;
    audio.wave.ctx.createMediaStreamSource(stream).connect(analyser);
  } catch (_) {
    return;
  }
  const samples = new Uint8Array(analyser.fftSize);
  const BARS = 40;
  const draw = () => {
    audio.wave.frame = window.requestAnimationFrame(draw);
    const canvas = document.querySelector("#voice-wave");
    if (!canvas || !canvas.clientWidth) return;
    const ratio = window.devicePixelRatio || 1;
    const w = Math.round(canvas.clientWidth * ratio);
    const h = Math.round(canvas.clientHeight * ratio);
    if (canvas.width !== w || canvas.height !== h) {
      canvas.width = w;
      canvas.height = h;
    }
    analyser.getByteTimeDomainData(samples);
    const g = canvas.getContext("2d");
    g.clearRect(0, 0, w, h);
    g.fillStyle = getComputedStyle(canvas).color;
    const slice = Math.floor(samples.length / BARS);
    const step = w / BARS;
    for (let i = 0; i < BARS; i += 1) {
      let peak = 0;
      for (let j = i * slice; j < (i + 1) * slice; j += 1) peak = Math.max(peak, Math.abs(samples[j] - 128) / 128);
      const bar = Math.max(2 * ratio, Math.min(1, peak * 2.5) * h);
      g.fillRect(i * step + step * 0.2, (h - bar) / 2, step * 0.6, bar);
    }
  };
  draw();
}

function stopWave() {
  window.cancelAnimationFrame(audio.wave.frame);
  audio.wave.ctx?.close().catch(() => {});
  audio.wave.ctx = null;
}

function showLive(on) {
  const live = document.querySelector("#voice-live");
  if (live) live.hidden = !on;
}

export async function toggleRecording(button) {
  if (isRecording()) {
    button.disabled = true;
    button.textContent = "Finishing…";
    audio.mediaRecorder.stop();
    return;
  }
  if (audio.health && !audio.health.voice_ok) {
    setAudioStatus("The voice recorder isn't reachable — start the voice-transcriber app", "error");
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

  audio.stream = stream;
  audio.sessionId = session.session_id;
  audio.mime = pickAudioMime();
  audio.uploadChain = Promise.resolve();
  audio.pendingUploads = 0;
  audio.bytesSent = 0;
  audio.bytes = 0;
  audio.recordStartedAt = Date.now();

  audio.mediaRecorder = new MediaRecorder(stream, audio.mime ? { mimeType: audio.mime } : undefined);
  audio.mediaRecorder.ondataavailable = (event) => {
    if (event.data && event.data.size) enqueueChunkUpload(event.data);
  };
  audio.mediaRecorder.onstop = () => {
    stream.getTracks().forEach((track) => track.stop());
    audio.stream = null;
    finishRecording();
  };
  audio.mediaRecorder.start(1000); // 1 s cadence — survives a connection drop

  showLive(true);
  startWave(stream);
  startRecordTimer();
  openAudioPartialStream(audio.sessionId);
  button.innerHTML = recordLabel(true);
  setAudioStatus("Say the zone, then each item and its count.");
  const redo = document.querySelector("#audio-redo");
  if (redo) redo.hidden = true;
}

// Serialised upload chain — each chunk POSTs after the previous resolves so
// they land on disk in order without overwhelming the connection.
function enqueueChunkUpload(chunk) {
  audio.pendingUploads += 1;
  const sessionId = audio.sessionId;
  audio.uploadChain = audio.uploadChain.then(async () => {
    try {
      const response = await authFetch(`/api/audio/session/${sessionId}/chunk`, {
        method: "POST",
        headers: { "Content-Type": chunk.type || audio.mime || "audio/webm" },
        body: chunk,
      });
      if (response.ok) audio.bytesSent += chunk.size;
      else console.warn("chunk upload failed", response.status);
    } catch (error) {
      console.warn("chunk upload errored", error);
    } finally {
      audio.pendingUploads -= 1;
    }
  });
}

function startRecordTimer() {
  stopRecordTimer();
  const tick = () => {
    const timer = document.querySelector("#voice-timer");
    if (!timer) return;
    const elapsed = Math.floor((Date.now() - audio.recordStartedAt) / 1000);
    timer.textContent = `${formatElapsed(elapsed)} · ${formatBytes(audio.bytesSent)} saved on the PC`;
  };
  tick();
  audio.recordTimer = window.setInterval(tick, 1000);
}

function stopRecordTimer() {
  if (audio.recordTimer) {
    window.clearInterval(audio.recordTimer);
    audio.recordTimer = null;
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
  audio.eventSource = es;
  es.addEventListener("partial", (event) => {
    try {
      const data = JSON.parse(event.data);
      if (typeof data.transcript === "string") {
        audio.transcript = data.transcript;
        const field = document.querySelector("#transcript");
        if (field) field.value = data.transcript;
      }
    } catch (_) {}
  });
  es.addEventListener("final", () => closeAudioPartialStream());
  es.onerror = () => {}; // browser auto-retries; leave the handle in place
}

function closeAudioPartialStream() {
  if (audio.eventSource) {
    try { audio.eventSource.close(); } catch (_) {}
    audio.eventSource = null;
  }
}

async function finishRecording() {
  stopRecordTimer();
  stopWave();
  showLive(false);
  try {
    setAudioStatus(`Finalising upload · ${audio.pendingUploads} chunk(s) left…`);
    await audio.uploadChain;
    audio.bytes = audio.bytesSent;
    const body = await runWithTimer(audioTranscribeStage, (signal) =>
      fetchJson(`/api/audio/session/${audio.sessionId}/finish?language=es&translate=false`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
        signal,
      }),
    );
    closeAudioPartialStream();
    if (body.silent) {
      setAudioStatus("Empty audio — nothing transcribed", "");
    } else {
      audio.transcript = body.transcript || "";
      const field = document.querySelector("#transcript");
      if (field) field.value = audio.transcript;
      setAudioStatus("Transcript ready — recording saved on the PC. Match it below.", "ok");
    }
  } catch (error) {
    closeAudioPartialStream();
    if (error.name === "AbortError") {
      setAudioStatus("Finish cancelled — recording is safe on the PC, tap Redo", "");
    } else {
      setAudioStatus(`Transcription failed: ${error.message} — recording is safe on the PC, tap Redo`, "error");
    }
  } finally {
    const redo = document.querySelector("#audio-redo");
    if (redo) redo.hidden = !audio.sessionId;
    // Looked up now, not at Stop: a re-render while the take finished made a new one.
    const button = document.querySelector("#record-toggle");
    if (button) {
      button.disabled = false;
      button.innerHTML = recordLabel(false);
    }
  }
}

// Re-run whisper on the saved audio — crash recovery, or after a finish error.
export async function redoTranscribe() {
  if (!audio.sessionId) {
    setAudioStatus("No saved recording to redo", "error");
    return;
  }
  try {
    const body = await runWithTimer(audioTranscribeStage, (signal) =>
      fetchJson(`/api/audio/session/${audio.sessionId}/retranscribe?language=es&translate=false`, {
        method: "POST",
        signal,
      }),
    );
    audio.transcript = body.transcript || "";
    const field = document.querySelector("#transcript");
    if (field) field.value = audio.transcript;
    setAudioStatus(body.silent ? "Empty audio — nothing transcribed" : "Re-transcribed from saved audio", body.silent ? "" : "ok");
  } catch (error) {
    if (error.name === "AbortError") setAudioStatus("Redo cancelled", "");
    else setAudioStatus(`Redo failed: ${error.message}`, "error");
  }
}

export async function matchTranscript() {
  const field = document.querySelector("#transcript");
  const transcript = (field?.value || "").trim();
  audio.transcript = transcript;
  if (!transcript) {
    setAudioStatus("Add or transcribe a transcript first", "error");
    return;
  }
  if (audio.health && !audio.health.hub_ok) {
    setAudioStatus("The matching service isn't reachable — start local-llm-hub before matching", "error");
    return;
  }
  // The progress, and then the results, are in and under the glance.
  document.querySelector("#voice-glance")?.scrollIntoView({ block: "start" });
  const model = audio.model;
  try {
    audio.matches = await runWithTimer(audioMatchStage, (signal) =>
      fetchJson("/api/audio/match", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(model ? { transcript, model } : { transcript }),
        signal,
      }),
    );
    resetPicks();
    audio.names = Object.fromEntries(items().map((item) => [item.id, String(item[c().comida] ?? "")]));
    const m = audio.matches;
    setAudioStatus(`Matched ${m.items.length} item${m.items.length === 1 ? "" : "s"} · ${humanizeModelId(m.model)}`, "ok");
    renderMatches();
  } catch (error) {
    if (error.name === "AbortError") setAudioStatus("Match cancelled", "");
    else setAudioStatus(`Match failed: ${error.message}`, "error");
  }
}

export async function applyAudio() {
  const updates = collectUpdates();
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
        names: Object.fromEntries(Object.keys(updates).filter((id) => id in audio.names).map((id) => [id, audio.names[id]])),
        transcript: audio.transcript,
        model: audio.model,
        matches: audio.matches,
        audio_bytes: audio.bytes || 0,
      }),
    });
    const logPath = state.payload.audio_log_path || "";
    audio.matches = null;
    resetPicks();
    render(); // re-reads the last audit into the glance
    setAudioStatus(logPath ? `Inventory updated · log ${logPath.split(/[\\/]/).pop()}` : "Inventory updated", "ok");
  } catch (error) {
    if (error.stale) {
      // The ids this review holds point at other rows now: it can't be applied, only
      // redone. fetchJson already toasted the reason and is reloading the list.
      audio.matches = null;
      resetPicks();
      render();
      return;
    }
    setAudioStatus(`Apply failed: ${error.message}`, "error");
    if (button) button.disabled = false;
  }
}
