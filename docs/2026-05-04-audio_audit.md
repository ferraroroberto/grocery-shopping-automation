# 🎙️ Audio Audit — voice-narrated inventory

A Streamlit mode that lets you walk the house, dictate the inventory in
Spanish, and have the household list updated automatically. Replaces nothing
— it sits alongside the existing `🔍 Audit Inventory` (manual ±1) mode.

## How it works

```
st.audio_input  →  whisper-server (:8090)  →  Spanish transcript
                                                    │
                                                    ▼
                          claude-local-calls hub (:8000)  →  matched JSON
                          model=claude-haiku-4-5 (default)
                                                    │
                                                    ▼
                          Review screen (accept / reject / "set 0")
                                                    │
                                                    ▼
                          bulk_apply_tenemos  →  list.xlsx
```

Compute is local except the Claude leg, which uses your Claude Code
subscription via `claude -p` (no API key needed). Switch to a fully-local LLM
by setting `audio_audit.llm_model` in `config.json` to a local backend like
`gemma4-26b-a4b-it`.

## Pre-requisites

The two services in `E:\automation\claude-local-calls` must be running:

```bat
:: from E:\automation\claude-local-calls
run_hub.bat                       :: hub on :8000
launchers\run_whisper.bat         :: whisper-server on :8090
```

Or use its `tray.bat` to keep both up automatically. The mode shows a clear
error banner when either port is unreachable.

## Recording technique

- **Anuncia la zona, luego los productos**, e.g.
  *"ahora en la nevera, dos yogures, un litro de leche; ahora paso al
  congelador, tres salmones, ningún pulpo…"*
- Use explicit numbers (*"dos"*, *"tres"*, *"cero"*, *"ninguno"*).
  Avoid *"algunos"* / *"varios"* — these go to the *unmatched* list.
- Six recognised zones: `nevera, congelador, despensa, estante, garaje,
  bajo escalera`.
- Common synonyms (`frigorífico → nevera`, `freezer → congelador`,
  `pantry → despensa`) are normalised by the LLM.
- 2–3 minutes is enough for the whole house.

## Review screen

Three sections after the run:

1. **🎯 Detected items** — one row per matched candidate, grouped by zone.
   `current → proposed`, evidence snippet, accept checkbox (default ON).
   If the zone the speaker said disagrees with the candidate's `lugar`, the
   list value is shown in brackets.
2. **🔍 Unmentioned items in audited zones** — items with current
   `tenemos > 0` whose `lugar` matches a zone you spoke about, but you did
   not name them. Each gets an opt-in *"set to 0"* checkbox (default OFF).
   Not seeing is not the same as not having — so the default is *not* to
   zero these out.
3. **❓ Unmatched mentions** — phrases the LLM heard but couldn't tie to any
   candidate. Useful for spotting items missing from `list.xlsx` or words
   that need the prompt tuned.

Counts are clamped to `cantidad + max_count_clamp_above_target` (default
`+5`) before being applied.

## Audit log

Each run writes to `audio_audit_logs/YYYY-MM-DD_HHMMSS.json`:

```json
{
  "timestamp": "2026-05-03T20:45:00",
  "audio_sha256": "…",
  "transcript": "…",
  "model": "claude-haiku-4-5",
  "result": { "items": […], "zones_mentioned": […], "unmatched_mentions": […] },
  "accepted_updates": [
    {"idx": 12, "comida": "pollo", "lugar": "congelador", "old_tenemos": 3, "new_tenemos": 2}
  ]
}
```

Useful for debugging the prompt and for forensics if a `tenemos` value looks
wrong after a run.

## Config

Block in `system/grocery/config.json`:

```json
"audio_audit": {
  "whisper_url": "http://127.0.0.1:8090",
  "whisper_model": "whisper-large-v3-turbo",
  "language": "es",
  "llm_base_url": "http://127.0.0.1:8000",
  "llm_model": "claude-haiku-4-5",
  "llm_max_tokens": 4096,
  "max_count_clamp_above_target": 5,
  "logs_dir": "audio_audit_logs",
  "test_fixture_path": "test_data/list_test_fixture.xlsx"
}
```

## Files

- `audio_audit.py` — Streamlit UI mode
- `transcribe_client.py` — multipart POST to whisper at `:8090`
- `inventory_extract.py` — `anthropic` SDK call to hub at `:8000`
- `data.py::bulk_apply_tenemos` — single-write batched update
- `test_data/smoke_phase1.py` — fixture round-trip without touching live xlsx
- `test_data/smoke_phase2.py` — end-to-end LLM smoke test against the running hub

## Troubleshooting

| Issue | Fix |
|---|---|
| Banner says "LLM hub unreachable" | Run `run_hub.bat` in `claude-local-calls/` (or the tray launcher). Wait until `:8000/v1/models` responds. |
| Banner says "Whisper server unreachable" | Run `launchers\run_whisper.bat`. The model loads ~1.6 GB on first start; give it ~30 s. |
| Transcript is empty / nonsense | Speak louder, hold the phone closer, or upload a pre-recorded clip via the file uploader fallback. |
| Item missing from "Detected items" | It's likely in **❓ Unmatched mentions** — check the phrase and add the canonical name to the list, or rephrase in the next take. |
| `comprar` looks stale after Apply | The save also recomputes `comprar = max(0, cantidad - tenemos)`. Refresh the page to pick up the new dataframe in other modes. |
