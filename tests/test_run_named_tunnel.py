"""scripts/run_named_tunnel.py must start the way webapp_tunnel_named.bat runs it."""

import os
import subprocess
import sys
from pathlib import Path

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
