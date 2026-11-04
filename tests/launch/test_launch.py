"""The webapp child launches through ``app.webapp.serve``: HTTP/2 first, uvicorn as fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tray.manager import WebappManager, WebappManagerConfig
from src.webapp_config import WebappConfig, load_webapp_config, save_webapp_config
from src.uvicorn_launch import build_webapp_command


def test_command_runs_the_serve_module_against_the_api_app(tmp_path: Path) -> None:
    cmd = build_webapp_command("127.0.0.1", 18502, project_root=tmp_path)
    assert cmd[cmd.index("-m") + 1] == "app.webapp.serve"
    assert cmd[cmd.index("--app") + 1] == "app.api:app"
    assert cmd[cmd.index("--server") + 1] == "hypercorn"
    assert build_webapp_command("127.0.0.1", 18502, project_root=tmp_path, server="uvicorn")[-1] == "uvicorn"


def test_manager_passes_the_configured_server() -> None:
    cmd = WebappManager(WebappManagerConfig(port=18502, server="uvicorn"))._build_command()
    assert cmd[cmd.index("--server") + 1] == "uvicorn"


def test_config_server_defaults_round_trips_and_rejects_unknown_values(tmp_path: Path) -> None:
    path = tmp_path / "webapp_config.json"
    assert load_webapp_config(path).server == "hypercorn"
    save_webapp_config(WebappConfig(server="uvicorn"), path)
    assert load_webapp_config(path).server == "uvicorn"
    path.write_text('{"server": "gunicorn"}', encoding="utf-8")
    with pytest.raises(ValueError):
        load_webapp_config(path)
