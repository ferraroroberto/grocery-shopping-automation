"""Read-only Gmail credentials and sender whitelist for this app.

Wires the app's own config layer to the vendored, domain-free
``gmail_readonly`` package (see ``gmail_readonly/README.md`` in
``docs/gmail-reuse.md`` upstream). Credential/token paths default to
``auth/gmail/`` (a gitignored directory whose OAuth client + refresh token are
reused verbatim from the ``whatsapp-radar`` sister repo — same Google account,
same read-only ``gmail.readonly`` scope) and may be overridden with
``GMAIL_CREDENTIALS_PATH`` / ``GMAIL_TOKEN_PATH``. The sender whitelist comes
from the gitignored ``config/gmail_config.json``; a missing file just resolves
to an empty whitelist. ``config/gmail_config.sample.json`` is the committed
template.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from gmail_readonly import (
    GmailMailbox,
    GmailReadClient,
    GmailSender,
    build_google_read_client,
)

logger = logging.getLogger("gmail_config")

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CREDENTIALS_PATH = _REPO_ROOT / "auth" / "gmail" / "credentials.json"
DEFAULT_TOKEN_PATH = _REPO_ROOT / "auth" / "gmail" / "token.json"
DEFAULT_WHITELIST_PATH = _REPO_ROOT / "config" / "gmail_config.json"


DEFAULT_POLLER_INTERVAL_MINUTES = 60


@dataclass
class MonitoredSender:
    """One whitelisted sender plus its Auto-tab monitoring metadata (#73)."""

    address: str
    name: str = ""
    store: str = ""
    enabled: bool = True


@dataclass
class PollerSettings:
    """Background email-poller switch + cadence (Auto tab, issue #73)."""

    enabled: bool = False
    interval_minutes: int = DEFAULT_POLLER_INTERVAL_MINUTES


def _read_config(target: Path) -> dict:
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ Could not read %s (%s); treating config as empty", target, exc)
        return {}
    if not isinstance(raw, dict):
        logger.warning("⚠️ %s is not a JSON object; treating config as empty", target)
        return {}
    return raw


def load_gmail_senders(path: Optional[Path] = None) -> tuple[GmailSender, ...]:
    """Return the configured sender whitelist, or an empty tuple if unset."""

    target = Path(path) if path is not None else DEFAULT_WHITELIST_PATH
    senders = _read_config(target).get("senders") or []
    return tuple(
        GmailSender(str(entry.get("address") or ""), str(entry.get("name") or ""))
        for entry in senders
        if isinstance(entry, dict) and entry.get("address")
    )


def load_monitored_senders(path: Optional[Path] = None) -> list[MonitoredSender]:
    """Return the senders with their store mapping + per-sender enable flag."""

    target = Path(path) if path is not None else DEFAULT_WHITELIST_PATH
    senders = _read_config(target).get("senders") or []
    return [
        MonitoredSender(
            address=str(entry.get("address") or ""),
            name=str(entry.get("name") or ""),
            store=str(entry.get("store") or ""),
            enabled=bool(entry.get("enabled", True)),
        )
        for entry in senders
        if isinstance(entry, dict) and entry.get("address")
    ]


def load_poller_settings(path: Optional[Path] = None) -> PollerSettings:
    """Return the poller switch + cadence; defaults to off / hourly."""

    target = Path(path) if path is not None else DEFAULT_WHITELIST_PATH
    raw = _read_config(target).get("poller") or {}
    if not isinstance(raw, dict):
        raw = {}
    try:
        interval = int(raw.get("interval_minutes", DEFAULT_POLLER_INTERVAL_MINUTES))
    except (TypeError, ValueError):
        interval = DEFAULT_POLLER_INTERVAL_MINUTES
    return PollerSettings(
        enabled=bool(raw.get("enabled", False)),
        interval_minutes=max(5, min(1440, interval)),
    )


def save_gmail_monitor_config(
    senders: list[MonitoredSender],
    settings: PollerSettings,
    path: Optional[Path] = None,
) -> Path:
    """Persist the full monitoring config (senders + poller), atomically."""

    target = Path(path) if path is not None else DEFAULT_WHITELIST_PATH
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "senders": [
            {"address": s.address, "name": s.name, "store": s.store, "enabled": s.enabled}
            for s in senders
        ],
        "poller": {"enabled": settings.enabled, "interval_minutes": settings.interval_minutes},
    }
    tmp = target.with_suffix(target.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, target)
    return target


def _resolved_path(env_var: str, default: Path) -> Path:
    load_dotenv(override=True)
    override = os.getenv(env_var)
    return Path(override) if override else default


def credentials_path() -> Path:
    return _resolved_path("GMAIL_CREDENTIALS_PATH", DEFAULT_CREDENTIALS_PATH)


def token_path() -> Path:
    return _resolved_path("GMAIL_TOKEN_PATH", DEFAULT_TOKEN_PATH)


def is_gmail_configured(path: Optional[Path] = None) -> bool:
    """True when a token file exists and at least one sender is whitelisted."""

    return token_path().is_file() and bool(load_gmail_senders(path))


def build_gmail_read_client() -> GmailReadClient:
    """Build the portable Google client from this app's resolved token path."""

    return build_google_read_client(token_path())


def build_gmail_mailbox() -> GmailMailbox:
    """Return a ready-to-use :class:`GmailMailbox` over the resolved client."""

    return GmailMailbox(build_gmail_read_client())
