"""scripts/run_named_tunnel.py must start the way webapp_tunnel_named.bat runs it."""

import os
import subprocess
import sys
from pathlib import Path

import scripts.run_named_tunnel as tunnel
from src.no_window import NEW_PROCESS_GROUP_NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_script_imports_and_stops_cleanly_without_a_cloudflared_config(tmp_path):
    """Regression for #228: run as a plain script (sys.path[0] is scripts/), the
    launcher died with ModuleNotFoundError on `import src`. A missing tunnel
    config makes it exit 1 right after its imports, before any spawn."""
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    env["CLOUDFLARED_CONFIG"] = str(tmp_path / "missing.yml")
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts" / "run_named_tunnel.py")],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=30,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    assert "ModuleNotFoundError" not in result.stderr, result.stderr
    assert result.returncode == 1, result.stderr
    assert "missing.yml" in result.stderr + result.stdout


def test_spawn_cloudflared_gets_its_own_process_group(tmp_path, monkeypatch):
    """Regression for audit #340: without NEW_PROCESS_GROUP_NO_WINDOW, cloudflared
    shares uvicorn's process group, so stop_process's CTRL_BREAK_EVENT (which
    targets a group) cannot stop cloudflared alone."""
    monkeypatch.setattr(tunnel.shutil, "which", lambda _name: "cloudflared.exe")
    calls = {}

    def fake_popen(cmd, **kwargs):
        calls["kwargs"] = kwargs
        return object()

    monkeypatch.setattr(tunnel.subprocess, "Popen", fake_popen)
    tunnel._spawn_cloudflared(tmp_path / "cloudflared.yml")
    assert calls["kwargs"]["creationflags"] == NEW_PROCESS_GROUP_NO_WINDOW
