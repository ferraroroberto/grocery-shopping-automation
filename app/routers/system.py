"""App shell + local-machine actions: the PWA document, build identity,
install manifest, auth handshake, health, access URLs, and the two desktop
actions (open the spreadsheet, close the server)."""

import hmac
import os
import platform
import subprocess
import threading
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse

from app.api_common import REPO_ROOT, STATIC_DIR, inventory_error
from app.static_files import BUILD_INFO
from src.data import CONFIG
from src.net import local_ip
from src.webapp_config import WebappConfig, append_auth_token

router = APIRouter()


def _https_cert_present() -> bool:
    return (
        (REPO_ROOT / "webapp" / "certificates" / "cert.pem").exists()
        or (REPO_ROOT / "certificates" / "cert.pem").exists()
    )


@router.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    """Serve the PWA shell.

    Stamp the directly-referenced asset URLs with their content hash and
    force the entry document to revalidate (``no-cache``), so an app restart
    after an edit is always picked up — no stale iOS PWA cache.
    """
    html = BUILD_INFO.stamp_html((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


@router.get("/api/version")
def version() -> JSONResponse:
    """Build identity (git SHA, build time, fleet asset hash).

    Feeds the PWA's footer build readout + stale-shell reload guard — the
    same contract as home-automation's ``/api/version``.
    """
    return JSONResponse(BUILD_INFO.as_dict())


@router.get("/manifest.json", include_in_schema=False)
def manifest() -> JSONResponse:
    """Return install metadata for the generated fleet-standard icon family."""
    return JSONResponse(
        {
            "name": CONFIG["app"]["title"],
            "short_name": "Grocery",
            "description": CONFIG["app"]["description"],
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#ffffff",
            "theme_color": "#ffffff",
            "icons": [
                {
                    "src": "/static/icon-192.png",
                    "sizes": "192x192",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "/static/icon-512.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "any",
                },
                {
                    "src": "/static/icon-512-maskable.png",
                    "sizes": "512x512",
                    "type": "image/png",
                    "purpose": "maskable",
                },
            ],
        }
    )


@router.post("/api/login")
async def login(request: Request) -> dict[str, str]:
    """Swap the configured password for the bearer token."""
    cfg: WebappConfig = request.app.state.webapp_config
    if not cfg.auth_password:
        raise HTTPException(status_code=503, detail="password auth not configured")
    if not cfg.auth_token:
        raise HTTPException(status_code=503, detail="bearer token not configured")

    try:
        body = await request.json()
    except ValueError:
        body = {}
    presented = str(body.get("password") or "")
    if not hmac.compare_digest(presented, cfg.auth_password):
        raise HTTPException(status_code=401, detail="bad password")
    return {"token": cfg.auth_token}


@router.get("/healthz")
@router.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/api/access")
def access_urls(request: Request) -> dict[str, Any]:
    cfg: WebappConfig = request.app.state.webapp_config
    scheme = "https" if _https_cert_present() else "http"
    local = f"{scheme}://127.0.0.1:{cfg.port}"
    lan = f"{scheme}://{local_ip()}:{cfg.port}"
    cloudflare_url = ""
    tunnel_file = REPO_ROOT / "webapp" / "last_tunnel_url.txt"
    if tunnel_file.exists():
        cloudflare_url = tunnel_file.read_text(encoding="utf-8").strip()
    token = cfg.auth_token

    return {
        "local": append_auth_token(local, token),
        "lan": append_auth_token(lan, token),
        "cloudflare": cloudflare_url,
        "auth_enabled": bool(token),
    }


@router.post("/api/actions/open-spreadsheet")
def open_spreadsheet() -> dict[str, str]:
    path = Path(CONFIG["data"]["xlsx_file"]).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists():
        raise inventory_error(404, f"file not found: {path}")
    if platform.system() == "Windows":
        os.startfile(str(path))  # noqa: S606
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
    return {"status": "opened"}


@router.post("/api/actions/close")
def close_app() -> dict[str, str]:
    def _exit_later() -> None:
        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=_exit_later, daemon=True).start()
    return {"status": "closing"}
