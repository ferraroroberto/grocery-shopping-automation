"""Audio Audit mode — record a Spanish narration of the inventory walk,
transcribe it via the local whisper-server, match phrases to candidates via
the local LLM hub, review and apply.

Pre-requisites (see README.md and the claude-local-calls sibling project):
  - LLM hub running on :8000
  - whisper-server running on :8090
"""

from __future__ import annotations

import logging
import socket
import threading
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from app.ui_helpers import render_save_error
from src.audio_audit_core import audio_sha256, clean_transcript, write_audit_log
from src.data import (
    COLUMNS,
    CONFIG,
    InventoryFileError,
    SpreadsheetLockedError,
    bulk_apply_tenemos,
)
from src.inventory_extract import ExtractionError, ExtractionResult, extract
from src.transcribe_client import TranscriptionError, transcribe

logger = logging.getLogger(__name__)

ZONE_KEYWORDS = ["nevera", "congelador", "despensa", "estante", "garaje", "bajo escalera"]

# Vocabulary hint for whisper-server. Reduces transcription drift on long audio.
WHISPER_PROMPT_ES = (
    "Inventario doméstico en español. "
    "Zonas: nevera, congelador, despensa, estante, garaje, bajo escalera. "
    "Cantidades: cero, uno, una, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez. "
    "Frases típicas: tengo dos, no hay, ninguno, hay tres, paso a la nevera."
)


def _format_elapsed(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    return f"{seconds // 60}m {seconds % 60:02d}s"


def _run_with_progress(
    placeholder,
    work: Callable[[], Any],
    progress_msg: Callable[[int], str],
    poll_seconds: float = 1.0,
) -> Tuple[Optional[Any], Optional[BaseException]]:
    """Run `work()` in a worker thread, updating `placeholder` once per
    `poll_seconds` with `progress_msg(elapsed_seconds)`. Returns
    (result, error) — exactly one is non-None."""
    box: Dict[str, Any] = {}

    def runner() -> None:
        try:
            box["result"] = work()
        except BaseException as exc:  # noqa: BLE001 — propagate to caller
            box["error"] = exc

    thread = threading.Thread(target=runner, daemon=True)
    start = time.monotonic()
    thread.start()
    while thread.is_alive():
        elapsed = int(time.monotonic() - start)
        placeholder.info(progress_msg(elapsed))
        thread.join(timeout=poll_seconds)
    return box.get("result"), box.get("error")


def _transcribe_progress(elapsed: int) -> str:
    t = _format_elapsed(elapsed)
    if elapsed < 5:
        return f"📡 Uploading audio to whisper-server… ({t})"
    if elapsed < 30:
        return f"🎙️ Whisper transcribing… ({t})"
    if elapsed < 120:
        return f"⏳ Whisper still working… ({t}) — long clips can take 1–3 min"
    return f"⏳ Whisper still working… ({t}) — large-v3-turbo on long audio can take up to 10 min"


def _extract_progress(elapsed: int) -> str:
    t = _format_elapsed(elapsed)
    if elapsed < 5:
        return f"📡 Sending request to LLM hub… ({t})"
    if elapsed < 20:
        return f"🧠 Hub routing to model, LLM analysing transcript… ({t})"
    if elapsed < 60:
        return f"🧠 LLM matching mentions to candidates… ({t}) — typical 30s–2min"
    if elapsed < 180:
        return f"⏳ Still working… ({t}) — long noisy transcripts take 2–4 min"
    return f"⏳ Still working… ({t}) — patience, can take up to 5 min on the longest walks"


def _audio_cfg() -> Dict:
    return CONFIG["audio_audit"]


def _is_port_open(url: str, timeout: float = 1.5) -> bool:
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 80
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def _service_status_banner(cfg: Dict) -> bool:
    hub_ok = _is_port_open(cfg["llm_base_url"])
    whisper_ok = _is_port_open(cfg["whisper_url"])
    if hub_ok and whisper_ok:
        return True
    msgs = []
    if not hub_ok:
        msgs.append(f"❌ LLM hub unreachable at `{cfg['llm_base_url']}`")
    if not whisper_ok:
        msgs.append(f"❌ Whisper server unreachable at `{cfg['whisper_url']}`")
    msgs.append(
        "Start the local LLM hub on :8000 and whisper-server on :8090. See the "
        "[`claude-local-calls`](https://github.com/ferraroroberto/claude-local-calls) "
        "sibling project for setup."
    )
    st.error("\n\n".join(msgs))
    return False


def _model_label(model_id: str) -> str:
    """Render `gemini_pro` as `Gemini Pro` — mirrors voice-transcriber's
    polishModelLabel rule so the two apps speak the same vocabulary."""
    if not model_id:
        return ""
    parts = [p for p in model_id.replace("__", "_").split("_") if p]
    return " ".join(p[:1].upper() + p[1:] for p in parts)


def _reset_state() -> None:
    for k in (
        "audio_audit_stage",
        "audio_audit_result",
        "audio_audit_transcript",
        "audio_audit_transcript_input",
        "audio_audit_audio_bytes",
        "audio_audit_audio_mime",
        "audio_audit_audio_filename",
        "audio_audit_log_path",
        "audio_audit_paste_input",
    ):
        st.session_state.pop(k, None)
    st.session_state.audio_audit_stage = "record"


def _ensure_state() -> None:
    if "audio_audit_stage" not in st.session_state:
        _reset_state()


def _run_transcribe(cfg: Dict) -> None:
    """Step 1: transcribe audio → store transcript, advance to 'transcribed' stage."""
    audio_bytes: bytes = st.session_state.audio_audit_audio_bytes
    mime = st.session_state.get("audio_audit_audio_mime", "audio/wav")
    filename = st.session_state.get("audio_audit_audio_filename", "audio.wav")

    size_mb = len(audio_bytes) / (1024 * 1024)
    logger.info(f"🎙️ transcribe — {filename} · {mime} · {len(audio_bytes)} bytes")

    info_panel = st.container()
    with info_panel:
        st.markdown("**🎙️ Transcribing audio**")
        st.caption(
            f"📡 `{cfg['whisper_url']}` · model `{cfg['whisper_model']}` · "
            f"lang `{cfg.get('language', 'es')}` · "
            f"audio {size_mb:.2f} MB ({mime}, `{filename}`)"
        )
    progress = st.empty()

    def work() -> str:
        return transcribe(
            audio_bytes,
            whisper_url=cfg["whisper_url"],
            model=cfg["whisper_model"],
            language=cfg.get("language", "es"),
            filename=filename,
            mime=mime,
            timeout=600,
            temperature=0.0,
            prompt=WHISPER_PROMPT_ES,
        )

    transcript, error = _run_with_progress(progress, work, _transcribe_progress)
    progress.empty()

    if error is not None:
        logger.exception("❌ transcription failed", exc_info=error)
        st.error(f"Transcription failed: {error}")
        return

    logger.info(f"✅ transcript ({len(transcript)} chars)")
    st.session_state.audio_audit_transcript = transcript
    # Pre-populate the editable text_area on the next stage. Safe to write here
    # because that widget has not been instantiated yet this run.
    st.session_state.audio_audit_transcript_input = transcript
    st.session_state.audio_audit_stage = "transcribed"


def _run_extract(df: pd.DataFrame, cfg: Dict) -> None:
    """Step 2: match transcript against inventory → advance to 'review' stage."""
    # The visible text_area on the transcribed stage is bound to
    # `audio_audit_transcript_input`; canonical post-match storage is
    # `audio_audit_transcript`. We copy from input → canonical here so the
    # later stages (review, log) read a stable value that survives the widget
    # being unmounted.
    raw_transcript: str = st.session_state.get(
        "audio_audit_transcript_input",
        st.session_state.get("audio_audit_transcript", ""),
    )
    cleaned = clean_transcript(raw_transcript)
    st.session_state.audio_audit_transcript = raw_transcript
    cleaned_note = ""
    if cleaned != raw_transcript:
        cleaned_note = f" · cleaned {len(raw_transcript)}→{len(cleaned)}"
        logger.info(
            f"🧹 transcript cleaned for matching — {len(raw_transcript)} → {len(cleaned)} chars"
        )
    model = st.session_state.get("audio_audit_llm_model", cfg["llm_model"])
    logger.info(f"🔍 extract — model={model} · transcript_chars={len(cleaned)}")

    info_panel = st.container()
    with info_panel:
        st.markdown("**🔍 Matching transcript against inventory**")
        st.caption(
            f"📡 `{cfg['llm_base_url']}` · model `{model}` · "
            f"transcript {len(cleaned)} chars{cleaned_note} · candidates {len(df)}"
        )
    progress = st.empty()

    def work():
        return extract(
            cleaned,
            df,
            base_url=cfg["llm_base_url"],
            model=model,
            max_tokens=cfg.get("llm_max_tokens", 4096),
            timeout=300,
        )

    result, error = _run_with_progress(progress, work, _extract_progress)
    progress.empty()

    if error is not None:
        logger.exception("❌ extraction failed", exc_info=error)
        st.error(f"Inventory matching failed: {error}")
        return

    logger.info(f"✅ extract done — {len(result.items)} items, zones: {result.zones_mentioned}")
    st.session_state.audio_audit_result = result
    st.session_state.audio_audit_stage = "review"


def _write_audit_log(
    df: pd.DataFrame,
    result: ExtractionResult,
    accepted: Dict[int, int],
    target_xlsx: str,
    old_tenemos: Dict[int, int],
) -> Path:
    cfg = _audio_cfg()
    audio_bytes = st.session_state.get("audio_audit_audio_bytes", b"")
    return write_audit_log(
        df=df,
        old_tenemos=old_tenemos,
        accepted=accepted,
        target_xlsx=target_xlsx,
        transcript=st.session_state.get("audio_audit_transcript", ""),
        model=st.session_state.get("audio_audit_llm_model", cfg["llm_model"]),
        whisper_model=cfg["whisper_model"],
        result={
            "items": result.items,
            "zones_mentioned": result.zones_mentioned,
            "unmatched_mentions": result.unmatched_mentions,
        },
        audio_sha=audio_sha256(audio_bytes),
        audio_bytes_len=len(audio_bytes),
        logs_dir=Path(__file__).resolve().parent.parent / cfg["logs_dir"],
    )


def _render_record(df: pd.DataFrame, cfg: Dict) -> None:
    st.markdown(
        "**How to record (in Spanish):** announce the zone, then the items. "
        "E.g. *\"ahora en la nevera, dos yogures, un litro de leche, "
        "ningún queso… ahora en el congelador, tres salmones…\"*"
    )
    st.caption(f"Recognised zones: {', '.join(ZONE_KEYWORDS)}")

    with st.expander("ℹ️ Quick tips", expanded=False):
        st.markdown(
            "- Use explicit numbers (*dos*, *tres*). Avoid *algunos* / *varios*.\n"
            "- To zero an item, say *cero* or *ninguno*.\n"
            "- Switch zone out loud: *\"ahora paso a la despensa\"*.\n"
            "- A 2–3 minute clip is enough for the whole house."
        )

    tracked = df[df[COLUMNS["cantidad"]] > 0]
    zones_in_df = sorted(tracked[COLUMNS["lugar"]].dropna().astype(str).unique(), key=str.lower)
    if zones_in_df:
        st.markdown(
            "**📋 Items per zone** — tap to open while recording "
            "(only items with target ≥ 1)"
        )
        for zone in zones_in_df:
            items = sorted(
                tracked[tracked[COLUMNS["lugar"]] == zone][COLUMNS["comida"]].astype(str).tolist(),
                key=str.lower,
            )
            with st.expander(f"{zone.title()} ({len(items)})", expanded=False):
                st.markdown("\n".join(f"- {it}" for it in items))

    audio_input = st.audio_input(
        "🎙️ Record walk",
        key="audio_audit_recorder",
        help="Works on mobile and desktop. Tap the mic, speak, tap Stop.",
    )

    uploaded = st.file_uploader(
        "…or upload an audio file",
        type=["wav", "webm", "m4a", "mp3", "ogg", "mp4"],
        key="audio_audit_uploader",
    )

    with st.expander("📝 Or paste / type a transcript instead", expanded=False):
        st.caption(
            "Skip recording — paste a transcript from elsewhere (e.g. WhatsApp voice "
            "message transcription) or type your audit notes directly."
        )
        pasted = st.text_area(
            "Paste transcript",
            key="audio_audit_paste_input",
            height=200,
            placeholder="ahora en la nevera, dos yogures, un litro de leche…",
            label_visibility="collapsed",
        )
        if st.button(
            "✅ Use this transcript",
            key="audio_audit_use_paste_btn",
            disabled=not pasted.strip(),
            width="stretch",
        ):
            text = pasted.strip()
            st.session_state.audio_audit_transcript = text
            st.session_state.audio_audit_transcript_input = text
            for k in (
                "audio_audit_audio_bytes",
                "audio_audit_audio_mime",
                "audio_audit_audio_filename",
            ):
                st.session_state.pop(k, None)
            st.session_state.audio_audit_stage = "transcribed"
            st.rerun()

    audio_bytes: Optional[bytes] = None
    mime = "audio/wav"
    filename = "audio.wav"
    source = None
    if audio_input is not None:
        audio_bytes = audio_input.getvalue()
        mime = audio_input.type or "audio/wav"
        filename = audio_input.name or "audio.wav"
        source = "mic"
    elif uploaded is not None:
        audio_bytes = uploaded.getvalue()
        mime = uploaded.type or "audio/octet-stream"
        filename = uploaded.name
        source = "upload"

    # Persist fresh audio immediately so reruns don't lose it
    if audio_bytes:
        st.session_state.audio_audit_audio_bytes = audio_bytes
        st.session_state.audio_audit_audio_mime = mime
        st.session_state.audio_audit_audio_filename = filename

    # On mobile a rerun can fire before the button is clicked, resetting the
    # widget to None — fall back to whatever is already stored in session_state
    if not audio_bytes:
        cached = st.session_state.get("audio_audit_audio_bytes")
        if cached:
            audio_bytes = cached
            mime = st.session_state.get("audio_audit_audio_mime", "audio/wav")
            filename = st.session_state.get("audio_audit_audio_filename", "audio.wav")
            source = "cached"

    if source:
        size_kb = len(audio_bytes) / 1024 if audio_bytes else 0
        if audio_bytes:
            st.caption(f"📦 {source} · {size_kb:.0f} KB · {mime} · {filename}")
        else:
            st.warning("⚠️ Audio captured but 0 bytes received — try again or use the file uploader.")

    run_disabled = not bool(audio_bytes)
    if st.button(
        "🎙️ Transcribe",
        type="primary",
        width="stretch",
        disabled=run_disabled,
    ):
        _run_transcribe(cfg)
        st.rerun()


def _render_review(df: pd.DataFrame, cfg: Dict) -> pd.DataFrame:
    result: ExtractionResult = st.session_state.audio_audit_result
    transcript: str = st.session_state.audio_audit_transcript

    st.success(
        f"✅ {len(result.items)} items detected · "
        f"{len(result.unmatched_mentions)} unmatched · "
        f"zones: {', '.join(result.zones_mentioned) or '—'}"
    )

    with st.expander("📜 Transcript", expanded=False):
        st.text_area("Transcript", value=transcript, height=160, label_visibility="collapsed")

    detected_idxs = {it["idx"]: it for it in result.items}
    detected_zones = {z.lower().strip() for z in result.zones_mentioned}

    items_by_zone: Dict[str, List[Dict]] = {}
    for it in result.items:
        items_by_zone.setdefault(it.get("zone", "—") or "—", []).append(it)

    st.markdown("### 🎯 Detected items")
    if not detected_idxs:
        st.info("The transcript didn't mention any recognisable item.")
    for zone in sorted(items_by_zone):
        st.markdown(f"**{zone.title()}**")
        h1, h2, h3, h4, h5, h6 = st.columns([4, 1, 1, 1, 3, 1])
        h1.caption("item")
        h2.caption("current")
        h3.caption("new")
        h4.caption("Δ")
        h5.caption("evidence")
        h6.caption("apply")
        for it in items_by_zone[zone]:
            idx = it["idx"]
            comida = str(df.at[idx, COLUMNS["comida"]])
            lugar = str(df.at[idx, COLUMNS["lugar"]])
            cur = int(df.at[idx, COLUMNS["tenemos"]])
            target = int(df.at[idx, COLUMNS["cantidad"]])
            clamp = target + cfg.get("max_count_clamp_above_target", 5)
            proposed = min(it["count"], clamp)
            delta = proposed - cur

            c1, c2, c3, c4, c5, c6 = st.columns([4, 1, 1, 1, 3, 1])
            badge = "" if lugar == zone else f" *(list: {lugar})*"
            c1.markdown(f"{comida}{badge}")
            c2.markdown(f"{cur}")
            c3.markdown(f"**{proposed}**")
            c4.markdown(f"{'+' if delta > 0 else ''}{delta}")
            c5.markdown(f"_{it.get('evidence', '')}_")
            c6.checkbox(
                " ",
                value=True,
                key=f"audio_audit_accept_{idx}",
                label_visibility="collapsed",
            )

    unseen = [
        idx
        for idx in df.index
        if idx not in detected_idxs
        and str(df.at[idx, COLUMNS["lugar"]]).lower() in detected_zones
        and int(df.at[idx, COLUMNS["cantidad"]]) > 0
        and int(df.at[idx, COLUMNS["tenemos"]]) > 0
    ]

    if unseen:
        st.markdown("### 🔍 Not mentioned (in audited zones)")
        st.caption(
            f"{len(unseen)} items in the zones you walked but didn't name. "
            f"Tick to set them to 0."
        )
        for idx in unseen:
            comida = str(df.at[idx, COLUMNS["comida"]])
            lugar = str(df.at[idx, COLUMNS["lugar"]])
            cur = int(df.at[idx, COLUMNS["tenemos"]])
            c1, c2, c3 = st.columns([5, 2, 1])
            c1.markdown(f"{comida} *(list: {lugar})*")
            c2.markdown(f"current: {cur} → **0**")
            c3.checkbox(
                " ",
                value=False,
                key=f"audio_audit_zero_{idx}",
                label_visibility="collapsed",
            )

    if result.unmatched_mentions:
        st.markdown("### ❓ Unmatched mentions")
        for mention in result.unmatched_mentions:
            phrase = mention.get("phrase", "")
            note = mention.get("note", "")
            st.markdown(f"- *{phrase}* — {note}")

    st.divider()
    bcol1, bcol2 = st.columns([1, 1])
    if bcol1.button(
        "💾 Apply changes to inventory",
        type="primary",
        width="stretch",
    ):
        accepted = _collect_accepted(df, result, unseen)
        if not accepted:
            st.warning("Nothing to apply.")
        else:
            target_path = CONFIG["data"]["xlsx_file"]
            old_tenemos = {idx: int(df.at[idx, COLUMNS["tenemos"]]) for idx in accepted}
            try:
                new_df = bulk_apply_tenemos(df, accepted, save=True)
            except (SpreadsheetLockedError, InventoryFileError) as e:
                render_save_error(e)
            else:
                log_path = _write_audit_log(new_df, result, accepted, target_path, old_tenemos)
                st.session_state.inventory_data = new_df
                st.session_state.audio_audit_log_path = str(log_path)
                st.session_state.audio_audit_stage = "done"
                st.rerun()  # full rerun — inventory changed, sidebar must update
    if bcol2.button("🔄 Cancel and start over", width="stretch"):
        _reset_state()
        st.rerun()

    return df


def _collect_accepted(
    df: pd.DataFrame, result: ExtractionResult, unseen_idxs: List[int]
) -> Dict[int, int]:
    cfg = _audio_cfg()
    clamp_extra = cfg.get("max_count_clamp_above_target", 5)
    accepted: Dict[int, int] = {}
    for it in result.items:
        if not st.session_state.get(f"audio_audit_accept_{it['idx']}", True):
            continue
        target = int(df.at[it["idx"], COLUMNS["cantidad"]])
        proposed = min(int(it["count"]), target + clamp_extra)
        accepted[it["idx"]] = proposed
    for idx in unseen_idxs:
        if st.session_state.get(f"audio_audit_zero_{idx}", False):
            accepted[idx] = 0
    return accepted


def _render_done() -> None:
    log_path = st.session_state.get("audio_audit_log_path", "")
    st.success("✅ Inventory updated.")
    if log_path:
        st.caption(f"📝 Log: `{log_path}`")
    if st.button("🆕 New audit", type="primary"):
        _reset_state()
        st.rerun()


def _render_transcribed(df: pd.DataFrame, cfg: Dict) -> None:
    """Show transcript and offer the Match button as a separate step."""
    st.success("✅ Transcript ready!")
    st.text_area(
        "Transcript",
        key="audio_audit_transcript_input",
        height=200,
        label_visibility="collapsed",
    )
    st.caption(
        "Edit if Whisper added repetitions or noise, then pick the matching "
        "model and tap Match. Long noisy transcripts can take a few minutes."
    )

    models = list(cfg.get("llm_models_available") or [cfg["llm_model"]])
    default_model = cfg["llm_model"]
    if default_model not in models:
        models = [default_model, *models]
    selected = st.session_state.get("audio_audit_llm_model", default_model)
    if selected not in models:
        selected = default_model
    st.selectbox(
        "🧠 Matching model",
        options=models,
        index=models.index(selected),
        format_func=_model_label,
        key="audio_audit_llm_model",
        help="Routed through the local LLM hub. Defaults to Gemini Pro.",
    )

    hub_ok = _is_port_open(cfg["llm_base_url"])
    if not hub_ok:
        st.error(
            f"❌ LLM hub unreachable at `{cfg['llm_base_url']}`. Start the hub "
            "before matching."
        )

    c1, c2 = st.columns(2)
    if c1.button(
        "🔍 Match inventory",
        type="primary",
        width="stretch",
        key="audio_audit_match_btn",
        disabled=(
            not st.session_state.get("audio_audit_transcript_input", "").strip()
            or not hub_ok
        ),
    ):
        _run_extract(df, cfg)
        st.rerun()
    if c2.button("🔄 Reset", width="stretch", key="audio_audit_reset_btn"):
        _reset_state()
        st.rerun()


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Entry point for the Audio Audit Streamlit mode."""
    cfg = _audio_cfg()
    _ensure_state()

    if not _service_status_banner(cfg):
        st.stop()

    stage = st.session_state.audio_audit_stage
    if stage == "record":
        _render_record(df, cfg)
    elif stage == "transcribed":
        _render_transcribed(df, cfg)
    elif stage == "review":
        df = _render_review(df, cfg)
    elif stage == "done":
        _render_done()

    return df
