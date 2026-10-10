"""A throwaway grocery webapp on invented data, for ``/design-review --synthetic`` (#251).

fleet-config's design-review walk must never touch the household's real
spreadsheet, so the redesign walks an instance nobody uses. This script is that
instance and speaks the launcher contract ``capture.SyntheticInstance`` expects
(fleet-config#995, ``docs/skills.md`` "Two legs, two interpreters"): run with
this repo's ``.venv`` from the checkout root, it prints ``URL=<base>`` once the
webapp answers, runs until its stdin reaches EOF, then stops the server and
deletes its temp tree.

What makes it throwaway, and synthetic only:

* **The code runs from a copy.** The checkout's tracked and untracked-unignored
  files (so a redesign in progress is what gets walked) are copied into a temp
  dir; every ``REPO_ROOT``-relative file the app reads or writes (config,
  logs, benchmark state) is a throwaway one and the checkout is never written.
  Gitignored real data (``data/list.xlsx``, ``config/*.json``, ``auth/``,
  ``benchmark_runs/``) is not copied.
* **Every data path is invented.** ``src/config.json`` in the copy points the
  workbook and benchmark runs at the temp tree and every sibling-service URL at
  a closed loopback port. ``scripts/synthetic_demo_data.py`` writes the data.
* **Side effects are stubbed, then guarded.** ``scripts/synthetic_demo_app.py``
  replaces the cart automation, product search, store logins, Chrome launch,
  email poll, voice transcriber, whisper and LLM hub with invented answers, and
  ``scripts/synthetic_demo_guard.py`` refuses whatever a stub missed (see
  there).

Stdout carries only ``ROOT=`` and ``URL=``; logging goes to stderr and the
server logs to a file under the temp tree.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Dict, List, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts import synthetic_demo_guard as guard  # noqa: E402
from src.no_window import NO_WINDOW  # noqa: E402

logger = logging.getLogger("design_review_synthetic")

CLOSED_URL = "http://127.0.0.1:9"        # sibling services: a port nothing listens on
STARTUP_TIMEOUT_S = 90.0
SEED_TIMEOUT_S = 300.0
STOP_GRACE_S = 10.0
SKIP_DIRS = ("tests/", "docs/", ".claude/", ".git/")
SECRET_MARKERS = ("KEY", "TOKEN", "SECRET", "PASSWORD", "CREDENTIAL")
# Real household state under a checkout root; none of it may be opened by the instance.
PROTECTED_RELATIVE = ("data", "config", "src/config.json", "auth", "benchmark_runs", "purchase_logs",
                      "audio_audit_logs", "logs", "automation/chrome_user_data", "webapp/certificates", ".env")


def _git_lines(args: List[str]) -> List[str]:
    out = subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True, encoding="utf-8",
                         timeout=60, creationflags=NO_WINDOW, env={**os.environ, "GIT_OPTIONAL_LOCKS": "0"})
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()[:200]}")
    return [line for line in out.stdout.splitlines() if line]


def copy_checkout(dest: Path) -> Path:
    """Tracked + untracked-unignored files of this checkout into ``dest`` (no tests, docs or history)."""
    dest.mkdir(parents=True, exist_ok=True)
    for rel in _git_lines(["ls-files", "--cached", "--others", "--exclude-standard"]):
        src = REPO_ROOT / rel
        if rel.startswith(SKIP_DIRS) or not src.is_file():
            continue
        target = dest / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    return dest


def checkout_roots() -> List[Path]:
    """This checkout and, in a worktree, the primary one: both may hold the household's real files."""
    roots = [REPO_ROOT]
    common = _git_lines(["rev-parse", "--path-format=absolute", "--git-common-dir"])
    if common:
        primary = Path(common[0]).parent
        if primary.resolve() != REPO_ROOT.resolve():
            roots.append(primary)
    return roots


def protected_paths(extra: str = "") -> List[str]:
    """Real paths the instance must never open: household state under every checkout root, the configured workbook."""
    paths: List[str] = []
    for root in checkout_roots():
        paths += [str(root / rel) for rel in PROTECTED_RELATIVE]
        for cfg in (root / "src" / "config.json", root / "src" / "config.example.json"):
            try:
                xlsx = json.loads(cfg.read_text(encoding="utf-8"))["data"]["xlsx_file"]
            except (OSError, ValueError, KeyError):
                continue
            paths.append(str(Path(xlsx).expanduser() if Path(xlsx).is_absolute() else root / xlsx))
    return paths + [p for p in extra.split(os.pathsep) if p]


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False), encoding="utf-8")


def configure(root: Path, app: Path, port: int) -> None:
    """The copy's config files: everything points into ``root`` or at a closed port."""
    cfg = json.loads((app / "src" / "config.example.json").read_text(encoding="utf-8"))
    cfg["data"]["xlsx_file"] = str(root / "data" / "list.xlsx")
    audio = cfg["audio_audit"]
    audio.update({"whisper_url": CLOSED_URL, "transcribe_url": CLOSED_URL, "llm_base_url": CLOSED_URL,
                  "voice_transcriber_url": CLOSED_URL, "logs_dir": str(root / "audio_audit_logs")})
    cfg["automation"]["purchase_logs_dir"] = str(root / "purchase_logs")
    cfg["benchmark"]["runs_dir"] = str(root / "benchmark_runs")
    (root / "data").mkdir(parents=True, exist_ok=True)
    _write_json(app / "src" / "config.json", cfg)

    _write_json(app / "config" / "webapp_config.json",
                {"host": "127.0.0.1", "port": port, "auth_token": "", "auth_password": ""})
    _write_json(app / "config" / "gmail_config.json", {
        "senders": [
            {"address": "orders@mercadona.demo.invalid", "name": "Demo Mercadona", "store": "mercadona", "enabled": True},
            {"address": "orders@carrefour.demo.invalid", "name": "Demo Carrefour", "store": "carrefour", "enabled": False},
        ],
        "poller": {"enabled": False, "interval_minutes": 60},
    })
    _write_json(app / "config" / "email_check_log.json", [
        {"ts": "2026-09-30T09:00:00", "store": "mercadona", "trigger": "scheduled", "ok": True,
         "outcome": "No new email — latest already processed", "notified": False}])
    # The committed alias and product-option files name the household's real products: replaced with invented ones.
    _write_json(app / "config" / "item_name_aliases.json", {"mercadona": [
        {"website_name": "Demo meadow butter 250g", "comida": "meadow butter"}]})
    _write_json(app / "config" / "product_options.json", {"carrefour": {"DEMO000": {"cut": "Sliced", "note": "demo"}}})


def instance_env(root: Path, protected: List[str]) -> Dict[str, str]:
    env = {k: v for k, v in os.environ.items() if not any(m in k.upper() for m in SECRET_MARKERS)}
    home = root / "home"
    scratch = root / "tmp"                    # upload spooling and tempfile probes stay inside the tree
    home.mkdir(parents=True, exist_ok=True)
    scratch.mkdir(parents=True, exist_ok=True)
    env.update({
        "USERPROFILE": str(home), "HOME": str(home), "TEMP": str(scratch), "TMP": str(scratch),
        "TMPDIR": str(scratch), "PYTHONUTF8": "1", "PYTHONDONTWRITEBYTECODE": "1",
        guard.ENV_ROOT: str(root), guard.ENV_PROTECT: os.pathsep.join(protected),
        guard.ENV_LOG: str(root / "guard.log"),
    })
    return env


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def _healthy(base: str) -> bool:
    try:
        with urllib.request.urlopen(f"{base}/healthz", timeout=2) as res:
            return res.status == 200
    except (urllib.error.URLError, OSError):
        return False


def stop_process(proc: subprocess.Popen) -> None:
    """Terminate the server; its process tree only if it does not exit."""
    if proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=STOP_GRACE_S)
        return
    except subprocess.TimeoutExpired:
        logger.warning("⚠️ server (pid %s) did not stop; killing its process tree", proc.pid)
    if sys.platform == "win32":
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, creationflags=NO_WINDOW)
    else:
        proc.kill()
    try:
        proc.wait(timeout=STOP_GRACE_S)
    except subprocess.TimeoutExpired:
        logger.error("❌ server (pid %s) is still running after a tree kill", proc.pid)


def remove_tree(root: Path) -> bool:
    """Delete the temp tree; a file a just-exited child still holds gets a few seconds."""
    for _ in range(10):
        shutil.rmtree(root, ignore_errors=True)
        if not root.exists():
            return True
        time.sleep(1)
    logger.warning("⚠️ could not remove %s entirely", root)
    return False


def run(protect_extra: str = "") -> int:
    root = Path(tempfile.mkdtemp(prefix="grocery-design-synthetic-"))
    server: Optional[subprocess.Popen] = None
    try:
        app = copy_checkout(root / "app")
        port = free_port()
        configure(root, app, port)
        env = instance_env(root, protected_paths(protect_extra))
        seed = subprocess.run([sys.executable, "-m", "scripts.synthetic_demo_data", str(root)], cwd=app, env=env,
                              capture_output=True, text=True, encoding="utf-8", errors="replace",
                              timeout=SEED_TIMEOUT_S, creationflags=NO_WINDOW)
        (root / "seed.log").write_text(seed.stdout + seed.stderr, encoding="utf-8")
        if seed.returncode != 0:
            raise RuntimeError(f"seeding the demo data failed ({seed.returncode}): {seed.stderr.strip()[-300:]}")

        with (root / "server.log").open("w", encoding="utf-8", errors="replace") as log:
            server = subprocess.Popen(
                [sys.executable, "-m", "uvicorn", "scripts.synthetic_demo_app:app", "--host", "127.0.0.1",
                 "--port", str(port), "--log-level", "warning"],
                cwd=app, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                creationflags=NO_WINDOW)
        base = f"http://127.0.0.1:{port}"
        deadline = time.monotonic() + STARTUP_TIMEOUT_S
        while not _healthy(base):
            if server.poll() is not None:
                raise RuntimeError(f"server exited ({server.returncode}) before it was ready; see {root / 'server.log'}")
            if time.monotonic() > deadline:
                raise RuntimeError(f"server not ready within {STARTUP_TIMEOUT_S:g}s")
            time.sleep(0.5)

        print(f"ROOT={root}", flush=True)
        print(f"URL={base}", flush=True)
        logger.info("✅ synthetic instance ready at %s; stops when stdin closes", base)
        closed = threading.Event()
        threading.Thread(target=lambda: (sys.stdin.read(), closed.set()), daemon=True).start()
        while not closed.wait(2.0):
            if server.poll() is not None:
                raise RuntimeError(f"server exited ({server.returncode}); see {root / 'server.log'}")
        return 0
    except Exception as exc:  # noqa: BLE001 — every failure is reported, then cleaned up below
        logger.error("❌ synthetic instance failed: %s", exc)
        return 1
    finally:
        if server is not None:
            stop_process(server)
        if remove_tree(root):
            logger.info("ℹ️ removed %s", root)


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="%(message)s")
    return run(os.environ.get("SYNTHETIC_PROTECT_EXTRA", ""))


if __name__ == "__main__":
    raise SystemExit(main())
