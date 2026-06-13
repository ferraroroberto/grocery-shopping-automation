"""Shared, UI-agnostic helpers for the audio-audit workflow.

Both the FastAPI PWA (`app/api.py`) and the legacy Streamlit mode
(`app/audio_audit.py`) import from here so the transcript cleaning and the
audit-log shape never diverge between the two front ends.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

from src.data import COLUMNS

logger = logging.getLogger(__name__)

# Vocabulary hint for the whisper-server call. Reduces transcription drift on
# long audio. Shared by both front ends so the two UIs feed whisper the same
# hint and produce the same transcription.
WHISPER_PROMPT_ES = (
    "Inventario doméstico en español. "
    "Zonas: nevera, congelador, despensa, estante, garaje, bajo escalera. "
    "Cantidades: cero, uno, una, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez. "
    "Frases típicas: tengo dos, no hay, ninguno, hay tres, paso a la nevera."
)


def clean_transcript(text: str) -> str:
    """Light pre-clean before sending to the LLM: collapse whitespace and dedupe
    immediately-repeated sentences (a common Whisper hallucination pattern).
    Idempotent and conservative — does not touch content the user actually said."""
    if not text:
        return text
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n[ \t]*\n+", "\n", text)
    text = text.strip()
    # "Y con esto es todo. Y con esto es todo." → single occurrence
    text = re.sub(r"(\b[^.!?\n]{4,}[.!?])(?:\s+\1)+", r"\1", text)
    return text


def audio_sha256(audio_bytes: bytes) -> str:
    """Stable content hash for the recorded/uploaded audio (empty string if none)."""
    return hashlib.sha256(audio_bytes).hexdigest() if audio_bytes else ""


def write_audit_log(
    *,
    df: pd.DataFrame,
    old_tenemos: Dict[int, int],
    accepted: Dict[int, int],
    target_xlsx: str,
    transcript: str,
    model: str,
    whisper_model: str,
    result: Optional[Dict[str, Any]],
    audio_sha: str,
    audio_bytes_len: int,
    logs_dir: Path,
) -> Path:
    """Write one timestamped JSON audit log to `logs_dir` and return its path.

    `df` holds the post-apply rows (for comida/lugar lookup); `old_tenemos` maps
    idx → the value before the apply so the log records old→new. `result` is the
    raw match result ({items, zones_mentioned, unmatched_mentions}) or None.
    """
    logs_dir.mkdir(parents=True, exist_ok=True)
    result = result or {}

    log = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "target_xlsx": target_xlsx,
        "audio_sha256": audio_sha,
        "audio_bytes": audio_bytes_len,
        "transcript": transcript,
        "model": model,
        "whisper_model": whisper_model,
        "result": {
            "items": result.get("items", []),
            "zones_mentioned": result.get("zones_mentioned", []),
            "unmatched_mentions": result.get("unmatched_mentions", []),
        },
        "accepted_updates": [
            {
                "idx": idx,
                "comida": str(df.at[idx, COLUMNS["comida"]]) if idx in df.index else "",
                "lugar": str(df.at[idx, COLUMNS["lugar"]]) if idx in df.index else "",
                "old_tenemos": int(old_tenemos.get(idx, 0)),
                "new_tenemos": int(value),
            }
            for idx, value in accepted.items()
        ],
    }
    path = logs_dir / f"{datetime.now().strftime('%Y-%m-%d_%H%M%S')}.json"
    path.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"📝 audit log written to {path}")
    return path
