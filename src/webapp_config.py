"""Persisted FastAPI/PWA access configuration."""

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
from urllib.parse import urlencode, urlparse, urlunparse

DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "webapp_config.json"
SAMPLE_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "webapp_config.sample.json"
DEFAULT_HOST = "0.0.0.0"
DEFAULT_PORT = 8502


@dataclass
class WebappConfig:
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    auth_token: str = ""
    auth_password: str = ""


def load_webapp_config(path: Optional[Path] = None) -> WebappConfig:
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    if not target.exists():
        return WebappConfig()

    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return WebappConfig()

    cfg = WebappConfig(
        host=str(raw.get("host", DEFAULT_HOST)),
        port=int(raw.get("port", DEFAULT_PORT)),
        auth_token=str(raw.get("auth_token", "")),
        auth_password=str(raw.get("auth_password", "")),
    )
    _validate(cfg)
    return cfg


def save_webapp_config(cfg: WebappConfig, path: Optional[Path] = None) -> Path:
    target = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": cfg.host,
        "port": cfg.port,
        "auth_token": cfg.auth_token,
        "auth_password": cfg.auth_password,
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def append_auth_token(url: str, token: Optional[str]) -> str:
    if not token:
        return url
    parsed = urlparse(url)
    extra = urlencode({"token": token})
    query = f"{parsed.query}&{extra}" if parsed.query else extra
    return urlunparse(parsed._replace(query=query))


def _validate(cfg: WebappConfig) -> None:
    if not (1 <= cfg.port <= 65535):
        raise ValueError(f"port out of range: {cfg.port}")
