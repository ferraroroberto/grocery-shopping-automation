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
