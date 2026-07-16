"""Voice-narrated audit: transcribe a walk-through, match what was said to
inventory rows via the LLM hub, and apply the accepted counts. The recording
lifecycle itself is proxied to the voice-transcriber app (see `app/audio_hub`)."""

import logging
from typing import Any

import httpx
from fastapi import APIRouter, File, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import audio_hub
from app.api_common import (
    REPO_ROOT,
    get_row,
    inventory_error,
    inventory_payload,
    load_inventory_or_error,
    mutate_or_error,
)
from src.audio_audit_core import WHISPER_PROMPT_ES, clean_transcript, write_audit_log
from src.data import COLUMNS, CONFIG, bulk_apply_tenemos
from src.inventory_extract import ExtractionError, extract
from src.net import is_port_open

router = APIRouter()


class MatchPayload(BaseModel):
    transcript: str
    model: str | None = None


class ApplyPayload(BaseModel):
    updates: dict[int, int]
    # Optional traceability context, sent by the PWA so the apply step can write
    # the same audit log the Streamlit app produced. All optional — a bare
    # {updates} still applies, it just logs less context.
    transcript: str = ""
    model: str = ""
    matches: dict[str, Any] | None = None
    audio_sha: str = ""
    audio_bytes: int = 0


@router.post("/api/audio/transcribe")
async def audio_transcribe(file: UploadFile = File(...)) -> dict[str, str]:
    transcript = await audio_hub.transcode_and_transcribe(file, prompt=WHISPER_PROMPT_ES)
    return {"transcript": transcript}


@router.get("/api/audio/health")
def audio_health() -> dict[str, Any]:
    """Reachability of the local LLM hub and whisper-server, for the audio view's
    service-status banner. Mirrors the Streamlit `_service_status_banner` probe."""
    cfg = CONFIG["audio_audit"]
    url = audio_hub.voice_url()
    return {
        "hub_ok": is_port_open(cfg["llm_base_url"]),
        "whisper_ok": is_port_open(cfg["whisper_url"]),
        "voice_ok": is_port_open(url),
        "hub_url": cfg["llm_base_url"],
        "whisper_url": cfg["whisper_url"],
        "voice_url": url,
    }


@router.post("/api/audio/session")
async def audio_session_create() -> dict[str, Any]:
    """Open a voice-transcriber session for a hardened, streamed recording.

    incognito=True so audit takes don't pile into the voice app's History — they
    live only long enough to drive this audit (and a Redo/retranscribe)."""
    cfg = CONFIG["audio_audit"]
    resp = await audio_hub.vt_request(
        "POST", "/api/sessions",
        json={"language": cfg.get("language", "es"), "incognito": True},
    )
    return {"session_id": resp.json()["session_id"]}


@router.post("/api/audio/session/{session_id}/chunk")
async def audio_session_chunk(session_id: str, request: Request) -> dict[str, Any]:
    """Stream one recording chunk straight to the PC (archived on arrival)."""
    body = await request.body()
    headers = {}
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    resp = await audio_hub.vt_request(
        "POST", f"/api/sessions/{session_id}/chunk", content=body, headers=headers,
    )
    return resp.json()


@router.get("/api/audio/session/{session_id}/events")
async def audio_session_events(session_id: str) -> StreamingResponse:
    """Proxy the voice-transcriber rolling-transcription SSE through grocery's
    own origin so EventSource on the phone never needs a second tunnel."""

    async def proxy():
        try:
            async with audio_hub.vt_client(read_timeout=None) as client:
                async with client.stream("GET", f"/api/sessions/{session_id}/events") as resp:
                    async for chunk in resp.aiter_raw():
                        yield chunk
        except httpx.RequestError:
            yield b'event: error\ndata: {"detail":"voice-transcriber unreachable"}\n\n'

    return StreamingResponse(
        proxy(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@router.post("/api/audio/session/{session_id}/finish")
async def audio_session_finish(
    session_id: str, request: Request, language: str | None = None, translate: bool = False,
) -> dict[str, Any]:
    """Close the chunked take and return the canonical transcript."""
    cfg = CONFIG["audio_audit"]
    lang = language or cfg.get("language", "es")
    body = await request.body()
    resp = await audio_hub.vt_request(
        "POST", f"/api/sessions/{session_id}/finish",
        params={"language": lang, "translate": str(translate).lower()},
        content=body or b"{}",
        headers={"Content-Type": "application/json"},
    )
    data = resp.json()
    return {
        "transcript": data.get("transcript", ""),
        "language": data.get("language", lang),
        "silent": bool(data.get("silent", False)),
    }


@router.post("/api/audio/session/{session_id}/retranscribe")
async def audio_session_retranscribe(
    session_id: str, language: str | None = None, translate: bool = False,
) -> dict[str, Any]:
    """Re-run whisper on the saved audio (Redo / crash recovery)."""
    cfg = CONFIG["audio_audit"]
    lang = language or cfg.get("language", "es")
    resp = await audio_hub.vt_request(
        "POST", f"/api/sessions/{session_id}/retranscribe",
        params={"language": lang, "translate": str(translate).lower()},
    )
    data = resp.json()
    return {
        "transcript": data.get("transcript", ""),
        "language": data.get("language", lang),
        "silent": bool(data.get("silent", False)),
    }


@router.post("/api/audio/match")
def audio_match(payload: MatchPayload) -> dict[str, Any]:
    cfg = CONFIG["audio_audit"]
    df = load_inventory_or_error()
    transcript = clean_transcript(payload.transcript.strip())
    if not transcript:
        raise inventory_error(400, "transcript is empty")
    model = payload.model or cfg["llm_model"]
    try:
        result = extract(
            transcript,
            df,
            base_url=cfg["llm_base_url"],
            model=model,
            max_tokens=cfg["llm_max_tokens"],
            timeout=cfg.get("llm_timeout", 600),
        )
    except ExtractionError as exc:
        raise inventory_error(502, str(exc)) from exc
    return {
        "items": result.items,
        "zones_mentioned": result.zones_mentioned,
        "unmatched_mentions": result.unmatched_mentions,
        "raw_text": result.raw_text,
        "model": model,
        "transcript_chars": len(transcript),
        "candidates": int(len(df)),
    }


@router.post("/api/audio/apply")
def audio_apply(payload: ApplyPayload) -> dict[str, Any]:
    df = load_inventory_or_error()
    cleaned = {int(idx): max(0, int(value)) for idx, value in payload.updates.items()}
    old_tenemos = {idx: int(get_row(df, idx)[COLUMNS["tenemos"]]) for idx in cleaned}
    mutate_or_error(bulk_apply_tenemos, df, cleaned, save=True)

    new_df = load_inventory_or_error()
    cfg = CONFIG["audio_audit"]
    log_path = ""
    if cleaned:
        try:
            path = write_audit_log(
                df=new_df,
                old_tenemos=old_tenemos,
                accepted=cleaned,
                target_xlsx=str(CONFIG["data"]["xlsx_file"]),
                transcript=payload.transcript,
                model=payload.model or cfg["llm_model"],
                whisper_model=cfg["whisper_model"],
                result=payload.matches,
                audio_sha=payload.audio_sha,
                audio_bytes_len=payload.audio_bytes,
                logs_dir=REPO_ROOT / cfg["logs_dir"],
            )
            log_path = str(path)
        except OSError as exc:  # logging must never block the inventory update
            logging.getLogger(__name__).warning("audit log write failed: %s", exc)

    result = inventory_payload(new_df)
    result["audio_log_path"] = log_path
    return result
