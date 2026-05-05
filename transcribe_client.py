"""Whisper client — POSTs audio to the local whisper-server (claude-local-calls).

The whisper-server is OpenAI-compatible. The hub at :8000 does NOT proxy audio
endpoints; clients must hit :8090 directly. See claude-local-calls/README.md.
"""

import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Raised when whisper-server is unreachable or returns an error."""


def transcribe(
    audio_bytes: bytes,
    *,
    whisper_url: str,
    model: str,
    language: Optional[str] = "es",
    filename: str = "audio.wav",
    mime: str = "audio/wav",
    timeout: int = 300,
) -> str:
    """Transcribe audio via the local whisper-server.

    Returns the plain text transcript. Raises TranscriptionError on failure.
    """
    endpoint = whisper_url.rstrip("/") + "/v1/audio/transcriptions"
    files = {"file": (filename, audio_bytes, mime)}
    data = {"model": model, "response_format": "json"}
    if language:
        data["language"] = language

    logger.info(
        f"📡 POST {endpoint} (bytes={len(audio_bytes)}, model={model}, lang={language})"
    )
    try:
        resp = requests.post(endpoint, files=files, data=data, timeout=timeout)
    except requests.RequestException as exc:
        raise TranscriptionError(
            f"Could not reach whisper-server at {endpoint}: {exc}"
        ) from exc

    if resp.status_code != 200:
        raise TranscriptionError(
            f"whisper-server returned {resp.status_code}: {resp.text[:500]}"
        )

    try:
        payload = resp.json()
    except ValueError as exc:
        raise TranscriptionError(f"Non-JSON response from whisper: {resp.text[:500]}") from exc

    text = payload.get("text", "").strip()
    if not text:
        raise TranscriptionError("whisper returned an empty transcript")

    logger.info(f"✅ transcript ({len(text)} chars): {text[:120]}…")
    return text


def health_check(whisper_url: str, timeout: int = 5) -> bool:
    """Best-effort reachability probe. The whisper.cpp server has no /health,
    so we just open a TCP connection."""
    import socket
    from urllib.parse import urlparse

    parsed = urlparse(whisper_url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or 8090
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
