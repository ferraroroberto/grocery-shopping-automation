"""Start the grocery FastAPI/PWA and a named Cloudflare tunnel."""

import logging
import os
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import yaml

from src.webapp_config import append_auth_token, load_webapp_config

logger = logging.getLogger("run_named_tunnel")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "webapp" / "cloudflared.yml"
SAMPLE_CONFIG = PROJECT_ROOT / "webapp" / "cloudflared.sample.yml"
TUNNEL_URL_FILE = PROJECT_ROOT / "webapp" / "last_tunnel_url.txt"


def _have_listener(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.2)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _find_python() -> Path:
    venv_py = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
    if venv_py.exists():
        return venv_py
    return Path(sys.executable)


def _cert_pair() -> tuple[Path, Path]:
    cert = PROJECT_ROOT / "webapp" / "certificates" / "cert.pem"
    key = PROJECT_ROOT / "webapp" / "certificates" / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    return PROJECT_ROOT / "certificates" / "cert.pem", PROJECT_ROOT / "certificates" / "key.pem"


def _spawn_uvicorn(port: int) -> subprocess.Popen:
    cert, key = _cert_pair()
    cmd = [
        str(_find_python()),
        "-m",
        "uvicorn",
        "app.api:app",
        "--host",
        "127.0.0.1",
        "--port",
        str(port),
        "--log-level",
        "warning",
    ]
    if cert.exists() and key.exists():
        cmd.extend(["--ssl-keyfile", str(key), "--ssl-certfile", str(cert)])
    logger.info("Starting uvicorn on :%s", port)
    kwargs: dict = {"cwd": str(PROJECT_ROOT)}
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    return subprocess.Popen(cmd, **kwargs)


def _wait_for_uvicorn(port: int, timeout: float = 15.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _have_listener(port):
            return True
        time.sleep(0.3)
    return False


def _spawn_cloudflared(config_path: Path) -> subprocess.Popen:
    bin_path = shutil.which("cloudflared")
    if bin_path is None:
        raise SystemExit("cloudflared not found on PATH. Install: winget install Cloudflare.cloudflared")
    cmd = [bin_path, "tunnel", "--config", str(config_path), "run"]
    logger.info("Starting cloudflared")
    return subprocess.Popen(
        cmd,
        cwd=str(PROJECT_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
    )


def _read_hostname(config_path: Path) -> Optional[str]:
    try:
        data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("Could not parse %s: %s", config_path, exc)
        return None
    for entry in data.get("ingress") or []:
        if isinstance(entry, dict) and entry.get("hostname"):
            return str(entry["hostname"]).strip()
    return None


def _persist_tunnel_url(hostname: str) -> None:
    url = append_auth_token(f"https://{hostname}", load_webapp_config().auth_token)
    TUNNEL_URL_FILE.parent.mkdir(parents=True, exist_ok=True)
    TUNNEL_URL_FILE.write_text(url + "\n", encoding="utf-8")
    logger.info("Tunnel URL written to %s", TUNNEL_URL_FILE)
    logger.info(url)


def _stream(proc: subprocess.Popen) -> None:
    for line in proc.stdout or ():
        sys.stdout.write(line)
        sys.stdout.flush()


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    config_path = Path(os.environ.get("CLOUDFLARED_CONFIG", str(DEFAULT_CONFIG)))
    if not config_path.exists():
        logger.error("%s missing. Copy %s to cloudflared.yml and fill in tunnel + hostname.", config_path, SAMPLE_CONFIG.name)
        return 1

    web_cfg = load_webapp_config()
    port = int(os.environ.get("WEBAPP_PORT", web_cfg.port))
    uvicorn_proc: Optional[subprocess.Popen] = None
    if _have_listener(port):
        logger.info("Adopting existing webapp on :%s", port)
    else:
        uvicorn_proc = _spawn_uvicorn(port)
        if not _wait_for_uvicorn(port):
            logger.error("uvicorn failed to start within 15 seconds")
            uvicorn_proc.terminate()
            return 1

    hostname = _read_hostname(config_path)
    cloudflared = _spawn_cloudflared(config_path)
    threading.Thread(target=_stream, args=(cloudflared,), daemon=True).start()
    if hostname:
        _persist_tunnel_url(hostname)

    try:
        cloudflared.wait()
    except KeyboardInterrupt:
        logger.info("Stopping")
    finally:
        for proc, name in ((cloudflared, "cloudflared"), (uvicorn_proc, "uvicorn")):
            if proc is None:
                continue
            logger.info("Stopping %s pid=%s", name, proc.pid)
            if sys.platform == "win32":
                try:
                    proc.send_signal(signal.CTRL_BREAK_EVENT)
                except Exception:
                    pass
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
