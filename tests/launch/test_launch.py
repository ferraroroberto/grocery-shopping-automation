"""The webapp child launches through ``app.webapp.serve``: HTTP/2 first, uvicorn as fallback."""

from __future__ import annotations

from pathlib import Path

import pytest

from app.tray.manager import WebappManager, WebappManagerConfig
from src.uvicorn_launch import build_webapp_command
from src.webapp_config import WebappConfig, load_webapp_config, save_webapp_config


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


def test_webapp_bat_serves_through_the_shared_launcher() -> None:
    """``webapp.bat`` must not rebuild the cert lookup or the serve command line.

    A second copy once ignored ``server`` in ``webapp_config.json``; the batch
    file now delegates to ``src.uvicorn_launch``, so any cert, flag or server
    knowledge showing up in it again is the same drift.
    """
    bat = (Path(__file__).resolve().parents[2] / "webapp.bat").read_text(encoding="utf-8")
    assert "-m src.uvicorn_launch" in bat
    for duplicated in ("cert.pem", "key.pem", "--ssl-", "app.webapp.serve", "src.webapp_config"):
        assert duplicated not in bat, f"webapp.bat re-implements {duplicated!r}"


def test_manual_launcher_runs_the_tray_command_for_the_configured_server(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import src.uvicorn_launch as launch

    save_webapp_config(WebappConfig(host="127.0.0.1", port=18502, server="uvicorn"), tmp_path / "w.json")
    monkeypatch.setattr(launch, "load_webapp_config", lambda: load_webapp_config(tmp_path / "w.json"))
    monkeypatch.setattr(launch, "PROJECT_ROOT", tmp_path)
    ran: list[list[str]] = []
    monkeypatch.setattr(launch.subprocess, "call", lambda cmd, **_k: ran.append(cmd) or 0)

    assert launch.main() == 0
    assert ran == [build_webapp_command("127.0.0.1", 18502, project_root=tmp_path, server="uvicorn")]
