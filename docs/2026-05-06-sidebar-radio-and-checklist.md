# 2026-05-06 — Sidebar radio mode picker + per-zone checklist

## What changed

1. **Tabs → sidebar radio.** The main app no longer uses `st.tabs()`; mode
   selection is now a `st.radio()` at the top of the sidebar, and only the
   selected mode's `main(df)` is invoked per render.
2. **Per-zone reference checklist** in the Audio Audit record view: one
   collapsed expander per zone, items listed alphabetically, filtered to those
   with `cantidad > 0` (actively tracked). Helps the speaker not miss anything
   while dictating.
3. **Cleanup.** Removed the `@st.fragment` wrapper, the in-app `🐛 Debug log`
   expander, the `_debug_log` helper, the `audio_diag.py` standalone test
   page, and stale logging imports in `app/app.py`.

## Why

Audio Audit was freezing on mobile after pressing **Stop** in `st.audio_input`.
The widget worked perfectly in an isolated diagnostic page over HTTPS but
froze inside the main app. The trigger turned out to be `st.tabs()`: every
tab's render code runs on every rerun, and that interaction with the
file-upload widget on mobile prevented the post-upload rerun from completing.

Switching to a sidebar radio means only one mode renders at a time, matching
the diagnostic page's behaviour. The fix was confirmed end-to-end on mobile
(record → transcribe → review → apply).

## Files modified

- `app/app.py` — sidebar radio replaces tabs; dispatch on `mode_key`; removed
  fragment helper and unused imports (`logging`, `datetime`).
- `app/audio_audit.py` — added per-zone checklist in `_render_record`;
  removed `_debug_log` helper, `audio_audit_debug_log` state key, and the
  bottom debug expander; replaced debug calls with plain `logger.info`.
- `audio_diag.py` — deleted (was only used to isolate the freeze).

## Validation

- `py_compile` clean on `app/app.py` and `app/audio_audit.py`.
- Manual end-to-end on mobile over HTTPS / Tailscale: record → Stop →
  Transcribe → Match → Apply, all stages now advance without stalling.
- Diagnostic page on HTTPS (port 8503) confirmed the audio widget itself was
  fine; it was the multi-tab render context that broke it.

---

# 2026-05-06 — Addendum: paste transcript, editable transcript, Whisper hardening

## What changed

1. **Paste/type transcript path** on the record screen. New collapsed
   expander "📝 Or paste / type a transcript instead" with a text area and
   "✅ Use this transcript" button — jumps straight to the `transcribed`
   stage, skipping audio capture and Whisper. Useful when you already have
   a transcript from another tool, or want to type quick notes.
2. **Transcript is now editable before matching.** The text area on the
   `transcribed` stage is bound to `key="audio_audit_transcript_input"` so
   edits persist into `_run_extract` (see also point 8 on why widget key and
   canonical state key are separate). Previously the box accepted typing but
   silently discarded edits — the original (often noisy) Whisper output
   was sent to the LLM regardless.
3. **Longer timeouts for 10-minute walks.** Whisper transcribe timeout
   bumped 60 s → 600 s; LLM extract timeout 90 s → 300 s.
4. **Live progress UI for long calls.** Replaced the static `st.spinner`
   with a worker-thread + `st.empty()` placeholder pattern: the request
   runs in a background thread while the main thread updates a status
   line once a second with elapsed time and a stage hint that escalates
   ("📡 Sending request…" → "🧠 LLM analysing…" → "⏳ Still working…
   long transcripts take 2–4 min"). A static info panel above the
   spinner shows hub URL, model, transcript size, and candidate count
   so you can see exactly what's being sent. Same pattern applied to
   the Whisper transcribe step (audio size + model + URL).
5. **Light transcript pre-clean before matching.** Collapses runs of
   whitespace and dedupes immediately-repeated sentences (e.g. the
   "Y con esto es todo. Y con esto es todo." pattern Whisper sometimes
   emits at the tail of long audio). Conservative — does not touch real
   content. Cleanup is one-shot for the LLM payload only; the visible
   transcript text area is left untouched (Streamlit forbids writing
   to a session_state key after its bound widget has rendered).
6. **Whisper hardening.** Pass `temperature=0.0` and a Spanish-vocabulary
   `prompt` (zone names + counters) to the whisper-server. Lowers the
   chance of the repetition-loop hallucination on long audio with quiet
   stretches.
7. **"Re-record" → "Reset".** Same behaviour, more accurate label now
   that the path can also start from paste.
8. **Split widget key from canonical state key for the transcript.** The
   text area on the transcribed stage is bound to
   `audio_audit_transcript_input`; the canonical post-match value lives at
   `audio_audit_transcript`. Streamlit deletes a key when the widget that
   owns it stops rendering — leaving them as the same key meant the
   review/log/done stages crashed on `AttributeError` after Match. Now
   `_run_extract` copies input → canonical at click time and downstream
   stages read the canonical key only.
9. **Suppress two third-party log noise sources** (in `src/data.py` with
   a long inline comment block):
   - `asyncio` ERROR records whose traceback contains `WinError 10054` —
     the harmless Windows Proactor reset that fires on every Streamlit
     websocket disconnect (tab refresh, mobile sleep, "Close app").
     Filter is narrow: any other asyncio error still surfaces.
   - `httpx` logger raised from INFO to WARNING — drops the per-request
     `HTTP Request: POST …` echoes from the Anthropic SDK; our own
     loggers already record those calls with more context.
   Re-enable instructions are in the suppression block's docstring.

## Why

Two pain points reported on a real 10-minute audit walk: matching against
a long noisy transcript appeared "stuck" (it was running, but past the
old 90 s timeout the spinner gave no signal and the call eventually
errored), and there was no way to feed in a transcript captured outside
the app. The editable-transcript fix is also a latent-bug fix — the box
looked editable but its edits were thrown away.

## Files modified

- `app/audio_audit.py` — added `_clean_transcript`, `WHISPER_PROMPT_ES`,
  `_run_with_progress` worker-thread helper, `_transcribe_progress` /
  `_extract_progress` status formatters; paste expander in `_render_record`;
  text area on `_render_transcribed` bound to `audio_audit_transcript_input`;
  `_run_extract` reads input, copies to canonical `audio_audit_transcript`,
  pre-cleans, then runs the call in a thread with a live status placeholder;
  same threaded-progress pattern in `_run_transcribe`; bumped extract timeout
  90 → 300 s and Whisper timeout 60 → 600 s.
- `src/transcribe_client.py` — added `temperature` and optional `prompt`
  parameters; sent as multipart form fields.
- `src/data.py` — added `_AsyncioConnectionResetFilter` and
  `_suppress_known_log_noise()` (with a documented re-enable path);
  raises `httpx` logger to WARNING; filter installed at module import.

## Validation

- `py_compile` clean on all four edited files.
- Smoke-tested `_AsyncioConnectionResetFilter` with two synthetic records
  on the `asyncio` logger: a `ConnectionResetError [WinError 10054]` was
  dropped, an unrelated `RuntimeError` from the same logger passed through.
- Manual end-to-end on the live app: paste path → Match → Review → Apply,
  and Whisper path → editable transcript edit → Match → Review → Apply both
  reach completion without `StreamlitAPIException` or `AttributeError`.
- `src/data.py`'s suppression hooks run at module import. Streamlit's
  hot-reload picks up the changes on next rerun for app code, but the
  filters are only installed when the Python process starts — restart the
  Streamlit server to activate them.
