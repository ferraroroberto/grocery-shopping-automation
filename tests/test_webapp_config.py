"""Unit tests for the persisted webapp config loader (issue #390)."""

import json

import pytest

from src.atomic_json import write_json_atomic
from src.webapp_config import WebappConfig, load_webapp_config, save_webapp_config


def test_missing_file_returns_defaults(tmp_path):
    cfg = load_webapp_config(tmp_path / "webapp_config.json")
    assert cfg == WebappConfig()


def test_valid_file_round_trips(tmp_path):
    target = tmp_path / "webapp_config.json"
    saved = WebappConfig(host="127.0.0.1", port=9000, auth_token="tok", auth_password="pw")
    save_webapp_config(saved, target)
    assert load_webapp_config(target) == saved


def test_malformed_json_raises_instead_of_defaulting(tmp_path, caplog):
    target = tmp_path / "webapp_config.json"
    target.write_text("{not valid json", encoding="utf-8")
    with caplog.at_level("WARNING"):
        with pytest.raises(json.JSONDecodeError):
            load_webapp_config(target)
    assert "Could not read" in caplog.text


def test_unreadable_file_raises_instead_of_defaulting(tmp_path, monkeypatch, caplog):
    target = tmp_path / "webapp_config.json"
    target.write_text("{}", encoding="utf-8")

    def _boom(self, encoding="utf-8"):
        raise OSError("permission denied")

    monkeypatch.setattr("pathlib.Path.read_text", _boom)
    with caplog.at_level("WARNING"):
        with pytest.raises(OSError):
            load_webapp_config(target)
    assert "Could not read" in caplog.text


def test_write_json_atomic_round_trips_and_creates_parents(tmp_path):
    target = tmp_path / "nested" / "state.json"
    write_json_atomic(target, {"name": "plátano"})
    assert json.loads(target.read_text(encoding="utf-8")) == {"name": "plátano"}
    assert [p.name for p in target.parent.iterdir()] == ["state.json"]


def test_write_json_atomic_failure_keeps_target_and_leaves_no_temp(tmp_path):
    target = tmp_path / "state.json"
    write_json_atomic(target, {"ok": 1})
    with pytest.raises(TypeError):
        write_json_atomic(target, {"bad": object()})
    assert json.loads(target.read_text(encoding="utf-8")) == {"ok": 1}
    assert [p.name for p in tmp_path.iterdir()] == ["state.json"]
