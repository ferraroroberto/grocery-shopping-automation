"""Unit tests for the shared audio-audit helpers."""

import json
from pathlib import Path

import pandas as pd

from src.audio_audit_core import audio_sha256, clean_transcript, read_last_audit, write_audit_log


def test_clean_transcript_collapses_whitespace_and_dedupes():
    raw = "Hola    mundo.\n\n\n  Y con esto es todo. Y con esto es todo."
    cleaned = clean_transcript(raw)
    assert "    " not in cleaned
    # the immediately-repeated sentence is deduped to a single occurrence
    assert cleaned.count("Y con esto es todo.") == 1
    # idempotent
    assert clean_transcript(cleaned) == cleaned


def test_clean_transcript_handles_empty():
    assert clean_transcript("") == ""


def test_audio_sha256_stable_and_empty():
    assert audio_sha256(b"") == ""
    assert audio_sha256(b"abc") == audio_sha256(b"abc")
    assert len(audio_sha256(b"abc")) == 64


def test_write_audit_log_shape(tmp_path: Path):
    df = pd.DataFrame(
        {"comida": ["yogur"], "lugar": ["nevera"], "tenemos": [2]},
        index=[0],
    )
    logs_dir = tmp_path / "logs"
    path = write_audit_log(
        df=df,
        old_tenemos={0: 1},
        accepted={0: 2},
        target_xlsx="C:/x.xlsx",
        transcript="dos yogures",
        model="gemini_pro",
        whisper_model="whisper-large-v3-turbo",
        result={"items": [{"idx": 0, "count": 2}], "zones_mentioned": ["nevera"], "unmatched_mentions": []},
        audio_sha="deadbeef",
        audio_bytes_len=123,
        logs_dir=logs_dir,
    )
    assert path.exists()
    log = json.loads(path.read_text(encoding="utf-8"))
    assert log["model"] == "gemini_pro"
    assert log["audio_sha256"] == "deadbeef"
    assert log["result"]["zones_mentioned"] == ["nevera"]
    update = log["accepted_updates"][0]
    assert update == {"idx": 0, "comida": "yogur", "lugar": "nevera", "old_tenemos": 1, "new_tenemos": 2}


def _log(logs_dir: Path, name: str, body) -> None:
    logs_dir.mkdir(parents=True, exist_ok=True)
    (logs_dir / name).write_text(body if isinstance(body, str) else json.dumps(body), encoding="utf-8")


def test_read_last_audit_summarises_the_newest_log(tmp_path: Path):
    """The Audit glance (#259): the newest log, counts that moved, mentions with no row."""
    logs_dir = tmp_path / "logs"
    assert read_last_audit(logs_dir) is None
    _log(logs_dir, "2026-10-01_190000.json", {"timestamp": "2026-10-01T19:00:00", "accepted_updates": []})
    _log(logs_dir, "2026-10-08_191500.json", {
        "timestamp": "2026-10-08T19:15:00",
        "result": {"unmatched_mentions": [{"phrase": "a", "idx": None}, {"phrase": "b", "idx": 3}]},
        "accepted_updates": [
            {"idx": 1, "old_tenemos": 1, "new_tenemos": 2},
            {"idx": 2, "old_tenemos": 4, "new_tenemos": 4},
        ],
    })
    assert read_last_audit(logs_dir) == {"timestamp": "2026-10-08T19:15:00", "changed": 1, "unmatched": 1}


def test_read_last_audit_skips_an_unreadable_log(tmp_path: Path):
    logs_dir = tmp_path / "logs"
    _log(logs_dir, "2026-10-01_190000.json", {"timestamp": "2026-10-01T19:00:00"})
    _log(logs_dir, "2026-10-08_191500.json", "{not json")
    assert read_last_audit(logs_dir) == {"timestamp": "2026-10-01T19:00:00", "changed": 0, "unmatched": 0}
