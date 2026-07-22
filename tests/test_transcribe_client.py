"""Unit tests for the audio transcription client."""

import shutil
import subprocess

import pytest

from src.transcribe_client import transcode_to_wav, transcribe

_FFMPEG = shutil.which("ffmpeg")


class _Resp:
    status_code = 200
    text = '{"text": "hola"}'

    def json(self):
        return {"text": "hola"}


class TestTranscribeModelField:
    """The `model` field is what selects the hub role vs. a concrete backend."""

    def test_omits_model_for_role(self, monkeypatch):
        # No model → the hub applies roles.audio.transcribe (parakeet + failover).
        captured = {}

        def fake_post(url, files, data, timeout):
            captured["url"] = url
            captured["data"] = data
            return _Resp()

        monkeypatch.setattr("src.transcribe_client.requests.post", fake_post)
        out = transcribe(b"x", whisper_url="http://hub:8000")
        assert out == "hola"
        assert "model" not in captured["data"]
        assert captured["url"] == "http://hub:8000/v1/audio/transcriptions"

    def test_sends_model_when_given(self, monkeypatch):
        # An explicit id targets one backend directly (the audio-audit path).
        captured = {}

        def fake_post(url, files, data, timeout):
            captured["data"] = data
            return _Resp()

        monkeypatch.setattr("src.transcribe_client.requests.post", fake_post)
        transcribe(b"x", whisper_url="http://w:8090", model="whisper-large-v3-turbo")
        assert captured["data"]["model"] == "whisper-large-v3-turbo"


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
