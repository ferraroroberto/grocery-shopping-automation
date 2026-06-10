"""Unit tests for the whisper client's ffmpeg transcode bridge."""

import shutil
import subprocess

import pytest

from src.transcribe_client import transcode_to_wav

_FFMPEG = shutil.which("ffmpeg")


@pytest.mark.skipif(not _FFMPEG, reason="ffmpeg not installed")
def test_transcode_to_wav_produces_riff(tmp_path):
    # A short tone as the source; transcode must hand back a valid 16 kHz WAV
    # (whisper-server 400s on anything that isn't WAV/PCM).
    src = tmp_path / "tone.wav"
    subprocess.run(
        [_FFMPEG, "-y", "-hide_banner", "-loglevel", "error",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=0.3",
         "-ar", "44100", "-ac", "2", str(src)],
        check=True,
    )
    out = transcode_to_wav(src.read_bytes())
    assert out[:4] == b"RIFF" and out[8:12] == b"WAVE"
    assert len(out) > 44  # header + samples
