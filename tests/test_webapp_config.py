"""Unit tests for the persisted webapp config loader (issue #390)."""

import json

import pytest

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
