"""Audio transcription client — POSTs audio to an OpenAI-shaped endpoint.

Production transcription goes through the local-llm-hub on `:8000`: with no
`model` field, the hub applies its `roles.audio.transcribe` chain (parakeet
primary + whisper failover) and records the request in its observability ring.
Pass a concrete `model` to target one model directly (what the audio-audit
diagnostic does against whisper-server on `:8090`). Both speak the same
OpenAI `/v1/audio/transcriptions` shape.
"""

import logging
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import requests

logger = logging.getLogger(__name__)


class TranscriptionError(RuntimeError):
    """Raised when whisper-server is unreachable or returns an error."""


class FfmpegMissingError(TranscriptionError):
    """Raised when ffmpeg can't be located to transcode browser audio."""


def transcode_to_wav(audio_bytes: bytes, *, sample_rate: int = 16000, timeout: int = 120) -> bytes:
    """Transcode arbitrary audio (browser webm/opus or mp4/aac) to 16 kHz mono WAV.

    whisper-server only decodes WAV/PCM — it returns ``400 Invalid request`` for
    the compressed formats MediaRecorder produces (webm on Chrome/Android,
    mp4 on iOS Safari). ffmpeg bridges the gap, exactly as the voice-transcriber
    app does. Output goes to a temp file (not a pipe) so the WAV header carries
    correct RIFF sizes.

    Raises FfmpegMissingError when ffmpeg is absent so callers can fall back to
    posting the raw bytes (still correct for genuine WAV uploads).
    """
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise FfmpegMissingError("ffmpeg not found on PATH")
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "input"
        out = Path(tmp) / "audio.wav"
        src.write_bytes(audio_bytes)
        try:
            proc = subprocess.run(
                [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
                 "-i", str(src), "-ar", str(sample_rate), "-ac", "1", str(out)],
                capture_output=True,
                timeout=timeout,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise TranscriptionError(f"ffmpeg transcode failed: {exc}") from exc
        if proc.returncode != 0 or not out.exists():
            detail = proc.stderr.decode("utf-8", "replace")[:300]
            raise TranscriptionError(f"ffmpeg returned {proc.returncode}: {detail}")
        return out.read_bytes()


def transcribe(
    audio_bytes: bytes,
    *,
    whisper_url: str,
    model: Optional[str] = None,
    language: Optional[str] = "es",
    filename: str = "audio.wav",
    mime: str = "audio/wav",
    timeout: int = 300,
    temperature: float = 0.0,
    prompt: Optional[str] = None,
) -> str:
    """Transcribe audio via an OpenAI-shaped `/v1/audio/transcriptions` endpoint.

    Omit ``model`` (the default) to address the hub's transcribe **role**
    (parakeet + whisper failover); pass a concrete id to target one model.
    Returns the plain text transcript. Raises TranscriptionError on failure.
    """
    endpoint = whisper_url.rstrip("/") + "/v1/audio/transcriptions"
    files = {"file": (filename, audio_bytes, mime)}
    data = {
        "response_format": "json",
        "temperature": str(temperature),
    }
    # Only send `model` when explicitly targeting one — an absent model lets the
    # hub apply its role chain rather than pinning a single backend.
    if model:
        data["model"] = model
    if language:
        data["language"] = language
    if prompt:
        data["prompt"] = prompt

    logger.info(
        f"📡 POST {endpoint} (bytes={len(audio_bytes)}, model={model or 'role'}, "
        f"lang={language}, temp={temperature}, prompt={'yes' if prompt else 'no'})"
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
