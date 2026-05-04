"""Audio Audit mode — record a Spanish narration of the inventory walk,
transcribe it via the local whisper-server, match phrases to candidates via
the local LLM hub, review and apply.

Pre-requisites (see README.md and the claude-local-calls sibling project):
  - LLM hub running on :8000
  - whisper-server running on :8090
"""

from __future__ import annotations

import hashlib
import json
import logging
import socket
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional
from urllib.parse import urlparse

import pandas as pd
import streamlit as st

from app.ui_helpers import render_save_error
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


def _reset_state() -> None:
    for k in (
        "audio_audit_stage",
        "audio_audit_result",
        "audio_audit_transcript",
        "audio_audit_audio_bytes",
        "audio_audit_audio_mime",
        "audio_audit_audio_filename",
        "audio_audit_log_path",
    ):
        st.session_state.pop(k, None)
    st.session_state.audio_audit_stage = "record"


def _ensure_state() -> None:
    if "audio_audit_stage" not in st.session_state:
        _reset_state()


def _pipeline_run(df: pd.DataFrame, cfg: Dict) -> None:
    """Transcribe + extract. Stores results in session_state.

    Sends ALL rows as candidates (not just cantidad>0) so items with target=0
    can still be updated when the user mentions them by name.
    """
    audio_bytes: bytes = st.session_state.audio_audit_audio_bytes
    mime = st.session_state.get("audio_audit_audio_mime", "audio/wav")
    filename = st.session_state.get("audio_audit_audio_filename", "audio.wav")

    with st.spinner("Transcribing audio…"):
        try:
            transcript = transcribe(
                audio_bytes,
                whisper_url=cfg["whisper_url"],
                model=cfg["whisper_model"],
                language=cfg.get("language", "es"),
                filename=filename,
                mime=mime,
            )
        except TranscriptionError as exc:
            st.error(f"Transcription failed: {exc}")
            return

    st.session_state.audio_audit_transcript = transcript

    with st.spinner("Matching against inventory…"):
        try:
            result = extract(
                transcript,
                df,
                base_url=cfg["llm_base_url"],
                model=cfg["llm_model"],
                max_tokens=cfg.get("llm_max_tokens", 4096),
            )
        except ExtractionError as exc:
            st.error(f"Inventory extraction failed: {exc}")
            return

    st.session_state.audio_audit_result = result
    st.session_state.audio_audit_stage = "review"


def _write_audit_log(
    df: pd.DataFrame,
    result: ExtractionResult,
    accepted: Dict[int, int],
    target_xlsx: str,
) -> Path:
    cfg = _audio_cfg()
    logs_dir = Path(__file__).resolve().parent.parent / cfg["logs_dir"]
    logs_dir.mkdir(parents=True, exist_ok=True)

    audio_bytes = st.session_state.get("audio_audit_audio_bytes", b"")
    audio_sha = hashlib.sha256(audio_bytes).hexdigest() if audio_bytes else ""

    log = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "target_xlsx": target_xlsx,
        "audio_sha256": audio_sha,
        "audio_bytes": len(audio_bytes),
        "transcript": st.session_state.get("audio_audit_transcript", ""),
        "model": cfg["llm_model"],
        "whisper_model": cfg["whisper_model"],
        "result": {
            "items": result.items,
            "zones_mentioned": result.zones_mentioned,
            "unmatched_mentions": result.unmatched_mentions,
        },
        "accepted_updates": [
            {
                "idx": idx,
                "comida": str(df.at[idx, COLUMNS["comida"]]),
                "lugar": str(df.at[idx, COLUMNS["lugar"]]),
                "old_tenemos": int(df.at[idx, COLUMNS["tenemos"]]),
                "new_tenemos": int(value),
            }
            for idx, value in accepted.items()
        ],
    }
    path = logs_dir / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"📝 audit log written to {path}")
    return path


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

    audio_input = st.audio_input(
        "🎙️ Record walk",
        key="audio_audit_recorder",
        help="Works from mobile too (Chrome/Safari). Tap the mic and speak.",
    )

    uploaded = st.file_uploader(
        "…or upload an audio file",
        type=["wav", "webm", "m4a", "mp3", "ogg"],
        key="audio_audit_uploader",
    )

    audio_bytes: Optional[bytes] = None
    mime = "audio/wav"
    filename = "audio.wav"
    if audio_input is not None:
        audio_bytes = audio_input.getvalue()
        mime = audio_input.type or "audio/wav"
        filename = audio_input.name or "audio.wav"
    elif uploaded is not None:
        audio_bytes = uploaded.getvalue()
        mime = uploaded.type or "audio/octet-stream"
        filename = uploaded.name

    if audio_bytes:
        st.session_state.audio_audit_audio_bytes = audio_bytes
        st.session_state.audio_audit_audio_mime = mime
        st.session_state.audio_audit_audio_filename = filename
        st.caption(f"📦 audio ready · {len(audio_bytes) / 1024:.0f} KB · {mime}")

    run_disabled = audio_bytes is None
    if st.button(
        "🚀 Transcribe and match",
        type="primary",
        width="stretch",
        disabled=run_disabled,
    ):
        _pipeline_run(df, cfg)
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
            try:
                new_df = bulk_apply_tenemos(df, accepted, save=True)
            except (SpreadsheetLockedError, InventoryFileError) as e:
                render_save_error(e)
            else:
                log_path = _write_audit_log(new_df, result, accepted, target_path)
                st.session_state.inventory_data = new_df
                st.session_state.audio_audit_log_path = str(log_path)
                st.session_state.audio_audit_stage = "done"
                st.rerun()
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


def main(df: pd.DataFrame) -> pd.DataFrame:
    """Entry point for the Audio Audit Streamlit mode."""
    cfg = _audio_cfg()
    _ensure_state()

    if not _service_status_banner(cfg):
        st.stop()

    stage = st.session_state.audio_audit_stage
    if stage == "record":
        _render_record(df, cfg)
    elif stage == "review":
        df = _render_review(df, cfg)
    elif stage == "done":
        _render_done()
    return df
