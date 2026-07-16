"""Clients for the two external audio services the routers lean on.

Two things live here, both shared by more than one router:

* the **voice-transcriber session-API proxy** — the sibling voice-transcriber
  webapp owns hardened recording: 1 s chunks are streamed and archived to disk
  the moment they arrive (recoverable if the phone dies), whisper runs rolling
  partials over SSE, /finish returns the canonical transcript, and
  /retranscribe recovers a saved take. Rather than duplicate any of that,
  grocery proxies the recording lifecycle to it on loopback. The VT middleware
  bypasses loopback callers, so no token is needed; verify=False because it
  serves a self-signed cert. The phone only ever talks to grocery's own origin
  — no CORS, no second tunnel.
* **one-shot transcription** via the whisper hub, used by both the audio-audit
  and product-search routers.
"""

import logging
from typing import Any

import httpx
from fastapi import UploadFile

from app.api_common import inventory_error
from src.data import CONFIG
from src.transcribe_client import (
    FfmpegMissingError,
    TranscriptionError,
    transcode_to_wav,
    transcribe,
)


def voice_url() -> str:
    return CONFIG["audio_audit"].get("voice_transcriber_url", "https://127.0.0.1:8443").rstrip("/")


def vt_client(read_timeout: float | None = 600.0) -> httpx.AsyncClient:
    """AsyncClient for the voice-transcriber proxy. Factored out so tests can
    inject an httpx.MockTransport. read_timeout is None for the long-lived SSE
    stream (a walk can run >10 min) and bounded for unary calls."""
    return httpx.AsyncClient(
        base_url=voice_url(),
        verify=False,
        timeout=httpx.Timeout(connect=5.0, read=read_timeout, write=600.0, pool=5.0),
    )


async def vt_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Forward one unary request to the voice-transcriber webapp.

    Connection failures map to 502 (recorder down → the audio view shows the
    banner); a VT 4xx (unknown session, no chunks) propagates as-is so the
    client gets the meaningful detail; a VT 5xx collapses to 502.
    """
    try:
        async with vt_client() as client:
            resp = await client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise inventory_error(502, f"voice-transcriber unreachable at {voice_url()}: {exc}") from exc
    if resp.status_code >= 400:
        status = resp.status_code if resp.status_code < 500 else 502
        raise inventory_error(status, f"voice-transcriber error {resp.status_code}: {resp.text[:300]}")
    return resp


async def transcode_and_transcribe(file: UploadFile, *, prompt: str | None = None) -> str:
    """Read `file`, transcode to WAV, and transcribe it via the whisper hub.

    Shared by ``/api/product-search/transcribe`` and ``/api/audio/transcribe``
    (issue #95): both read the upload, transcode with an ffmpeg-missing
    fallback to the raw bytes, call ``transcribe()``, and translate a
    ``TranscriptionError`` into a 502. ``prompt`` (and the Spanish-forcing
    ``language``, always taken from config) is the only thing the two call
    sites differ on.
    """
    cfg = CONFIG["audio_audit"]
    audio_bytes = await file.read()
    if not audio_bytes:
        raise inventory_error(400, "empty audio file")
    # whisper-server only decodes WAV/PCM and 400s on the webm/mp4 that browser
    # MediaRecorder produces — transcode first. Fall back to the raw bytes only
    # if ffmpeg is unavailable (still correct for a genuine WAV upload).
    filename, mime = "audio.wav", "audio/wav"
    try:
        audio_bytes = transcode_to_wav(audio_bytes)
    except FfmpegMissingError:
        logging.getLogger(__name__).warning(
            "ffmpeg missing — posting raw audio to whisper (works only for WAV uploads)"
        )
        filename = file.filename or "audio.webm"
        mime = file.content_type or "audio/webm"
    except TranscriptionError as exc:
        raise inventory_error(502, str(exc)) from exc
    try:
        return transcribe(
            audio_bytes,
            whisper_url=cfg["whisper_url"],
            model=cfg["whisper_model"],
            # Force Spanish: with no language, whisper-large-v3-turbo sometimes
            # *translates* a Spanish clip to English. The stores are Spanish, so
            # we always want the Spanish term. (Same language the audio-audit uses.)
            language=cfg.get("language", "es"),
            filename=filename,
            mime=mime,
            timeout=600,
            temperature=0.0,
            prompt=prompt,
        )
    except TranscriptionError as exc:
        raise inventory_error(502, str(exc)) from exc
