// Audio audit: record a spoken walk-through, transcribe it, match what was
// said to inventory rows via the LLM hub, and apply the accepted counts.
//
// Recording is hardened (issue #30): every 1 s chunk is streamed to the PC and
// archived to disk by the voice-transcriber app the moment it arrives, so the
// take survives a dying phone. Rolling partials flow back over SSE; Stop yields
// the canonical transcript. grocery only proxies — VT owns the audio.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { authFetch, fetchJson } from "./api.js";
import { activePaneBody, c, items, render, state, storedToken } from "./core.js";
import { formatBytes, formatElapsed, html, switchMarkup, text } from "./dom.js";
import { pickAudioMime } from "./media.js";

// All of it local to this module — nothing outside the audio view reads it.
const audio = {
  mediaRecorder: null,
  mime: "",
  transcript: "",
  model: "",
  health: null,
  sha: "",
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
};

let audioAbort = null;

function setAudioStatus(message, kind = "") {
  const target = document.querySelector("#audio-status");
  if (target) {
    target.textContent = message;
    target.className = `panel-status${kind ? ` ${kind}` : ""}`;
  }
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

function renderAudioHealth() {
  const banner = document.querySelector("#audio-health-banner");
  if (!banner) return;
  const h = audio.health;
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
  const recording = audio.mediaRecorder && audio.mediaRecorder.state === "recording";
  if (recordBtn && !recording) {
    recordBtn.disabled = !h.voice_ok;
    recordBtn.title = h.voice_ok ? "" : "Voice recorder unreachable — start the voice-transcriber tray";
  }
}

export function renderAudio() {
  const cols = c();
  const audioCfg = state.payload.audio || { models: [], default_model: "" };
  if (!audio.model) audio.model = audioCfg.default_model || audioCfg.models[0] || "";
  const modelOptions = (audioCfg.models || [])
    .map((name) => `<option value="${html(name)}" ${name === audio.model ? "selected" : ""}>${html(name)}</option>`)
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
    <textarea id="transcript" placeholder="Transcript appears here, or paste one manually.">${audio.transcript ? html(audio.transcript) : ""}</textarea>
    <label class="field-label" for="audio-model">Match model
      <select id="audio-model"${modelOptions ? "" : " disabled"}>${modelOptions || `<option>${html(audio.model || "config default")}</option>`}</select>
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
  const hub = audio.health?.hub_url || state.payload.audio?.hub_url || "local hub";
  node.textContent = `${hub} · candidates ${items().length} · model ${audio.model || "config default"}`;
}

export function setAudioModel(value) {
  audio.model = value;
  renderAudioContext();
}

function renderMatches() {
  const target = document.querySelector("#match-results");
  const apply = document.querySelector("#apply-audio");
  if (!target) return;
  if (!audio.matches) {
    // The placeholder gets its own card so the results area reads as a
    // surface, not a floating caption.
    target.innerHTML = `<section class="panel">${emptyStateEl("mic", "No match results yet.").outerHTML}</section>`;
    if (apply) apply.disabled = true;
    return;
  }
  const cols = c();
  const clamp = Number(state.payload.audio?.clamp ?? 5);
  const byId = new Map(items().map((item) => [item.id, item]));
  const matched = audio.matches.items.filter((match) => byId.has(match.idx));

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
  const zonesMentioned = new Set((audio.matches.zones_mentioned || []).map((z) => String(z).toLowerCase().trim()));
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
  ${audio.matches.unmatched_mentions?.length ? `<section class="panel"><h2 class="card-title"><svg class="icon" aria-hidden="true" focusable="false"><use href="#i-circle-alert"></use></svg>Unmatched Mentions</h2>${audio.matches.unmatched_mentions.map((m) => `<div class="meta">${html(m.phrase)} · ${html(m.note)}</div>`).join("")}</section>` : ""}`;
  if (apply) apply.disabled = !matched.length && !unseen.length;
}

// Wipe the transcript + match results so the next audit starts from scratch.
export function clearAudio() {
  if (audioAbort) audioAbort.abort();
  closeAudioPartialStream();
  audio.transcript = "";
  audio.matches = null;
  audio.sha = "";
  audio.bytes = 0;
  audio.sessionId = "";
  audio.bytesSent = 0;
  render();
  setAudioStatus("Cleared — ready for a new audit", "");
}

export async function toggleRecording(button) {
  if (audio.mediaRecorder && audio.mediaRecorder.state === "recording") {
    button.disabled = true;
    button.textContent = "Finishing…";
    audio.mediaRecorder.stop();
    return;
  }
  if (audio.health && !audio.health.voice_ok) {
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

  audio.stream = stream;
  audio.sessionId = session.session_id;
  audio.mime = pickAudioMime();
  audio.uploadChain = Promise.resolve();
  audio.pendingUploads = 0;
  audio.bytesSent = 0;
  audio.sha = "";
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

  startRecordTimer();
  openAudioPartialStream(audio.sessionId);
  button.textContent = "Stop Recording";
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
    const elapsed = Math.floor((Date.now() - audio.recordStartedAt) / 1000);
    setAudioStatus(`Recording · ${formatElapsed(elapsed)} · ${formatBytes(audio.bytesSent)} streamed to PC`);
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
  const button = document.querySelector("#record-toggle");
  stopRecordTimer();
  try {
    setAudioStatus(`Finalising upload · ${audio.pendingUploads} chunk(s) left…`);
    await audio.uploadChain;
    audio.bytes = audio.bytesSent;
    const body = await runWithTimer(audioTranscribeStage, async (signal) => {
      const response = await authFetch(
        `/api/audio/session/${audio.sessionId}/finish?language=es&translate=false`,
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
      audio.transcript = body.transcript || "";
      const field = document.querySelector("#transcript");
      if (field) field.value = audio.transcript;
      setAudioStatus("Transcript ready — recording saved on the PC", "ok");
    }
    const redo = document.querySelector("#audio-redo");
    if (redo) redo.hidden = !audio.sessionId;
  } catch (error) {
    closeAudioPartialStream();
    if (error.name === "AbortError") {
      setAudioStatus("Finish cancelled — recording is safe on the PC, tap Redo", "");
    } else {
      setAudioStatus(`Transcription failed: ${error.message} — recording is safe on the PC, tap Redo`, "error");
    }
    const redo = document.querySelector("#audio-redo");
    if (redo) redo.hidden = !audio.sessionId;
  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = "Start Recording";
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
    const body = await runWithTimer(audioTranscribeStage, async (signal) => {
      const response = await authFetch(
        `/api/audio/session/${audio.sessionId}/retranscribe?language=es&translate=false`,
        { method: "POST", signal },
      );
      const data = await response.json().catch(() => ({}));
      if (!response.ok) throw new Error(data.detail || `HTTP ${response.status}`);
      return data;
    });
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
    setAudioStatus("LLM hub unreachable — start the hub before matching", "error");
    return;
  }
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
    const m = audio.matches;
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

export async function applyAudio() {
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
        transcript: audio.transcript,
        model: audio.model,
        matches: audio.matches,
        audio_sha: audio.sha || "",
        audio_bytes: audio.bytes || 0,
      }),
    });
    const logPath = state.payload.audio_log_path || "";
    audio.matches = null;
    render();
    setAudioStatus(logPath ? `Inventory updated · log ${logPath.split(/[\\/]/).pop()}` : "Inventory updated", "ok");
  } catch (error) {
    setAudioStatus(`Apply failed: ${error.message}`, "error");
    if (button) button.disabled = false;
  }
}
