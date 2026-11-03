"""Telegram notifier credentials for this app.

Wires the app's own config layer to the vendored, domain-free
``src.notify`` primitive (see ``src/notify/README.md``). Credentials come from
the gitignored ``config/notify_config.json`` and/or the environment
(``TELEGRAM_BOT_TOKEN`` / ``TELEGRAM_CHAT_ID``, which take precedence). The
security alert for repeated wrong passwords goes to its own chat,
``alert_chat_id`` / ``TELEGRAM_ALERT_CHAT_ID`` (the fleet's ``coding alerts``
chat); with none set it falls back to ``chat_id`` so an alert is never dropped. A
missing file is not an error — the same "graceful default" pattern as
``webapp_config.py``; with no credentials the notifier resolves to ``none`` and
every send becomes a silent no-op.

The real ``config/notify_config.json`` is gitignored (the bot token and chat id
are secrets in a public repo); ``config/notify_config.sample.json`` is the
committed template.
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv

from src.notify import Notifier, TelegramConfig, build_notifier

logger = logging.getLogger("notify_config")

DEFAULT_PATH = Path(__file__).resolve().parent.parent / "config" / "notify_config.json"


def _read_file(target: Path) -> dict:
    """The config file's JSON object, or ``{}`` when it is missing or unreadable."""
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ Could not read %s (%s); using env/defaults", target, exc)
        return {}
    if not isinstance(raw, dict):
        logger.warning("⚠️ %s is not a JSON object; ignoring", target)
        return {}
    return raw


def load_notify_config(path: Optional[Path] = None) -> TelegramConfig:
    """Resolve Telegram credentials from env (preferred) then the config file.

    Returns a :class:`TelegramConfig` whose fields may be empty strings when
    nothing is configured — callers treat empty creds as "notifier disabled".
    """

    load_dotenv(override=True)
    file_values = _read_file(Path(path) if path is not None else DEFAULT_PATH)
    bot_token = (os.getenv("TELEGRAM_BOT_TOKEN") or str(file_values.get("bot_token") or "")).strip()
    chat_id = (os.getenv("TELEGRAM_CHAT_ID") or str(file_values.get("chat_id") or "")).strip()
    return TelegramConfig(bot_token=bot_token, chat_id=chat_id)


def is_notify_configured(path: Optional[Path] = None) -> bool:
    """True when both a bot token and a chat id are available."""

    cfg = load_notify_config(path)
    return bool(cfg.bot_token and cfg.chat_id)


def build_notify_notifier(path: Optional[Path] = None) -> Optional[Notifier]:
    """Return a configured Telegram notifier, or ``None`` when unconfigured."""

    cfg = load_notify_config(path)
    name = "telegram" if (cfg.bot_token and cfg.chat_id) else "none"
    return build_notifier(name, cfg)


def build_alert_notifier(path: Optional[Path] = None) -> Optional[Notifier]:
    """A notifier bound to the alert chat, or ``None`` when unconfigured.

    The alert chat is ``alert_chat_id`` (env ``TELEGRAM_ALERT_CHAT_ID`` first);
    it falls back to the app's ordinary ``chat_id`` so a guessing alert still
    reaches the owner before the dedicated chat is set.
    """

    cfg = load_notify_config(path)
    file_values = _read_file(Path(path) if path is not None else DEFAULT_PATH)
    alert_chat = (os.getenv("TELEGRAM_ALERT_CHAT_ID") or str(file_values.get("alert_chat_id") or "")).strip()
    chat_id = alert_chat or cfg.chat_id
    name = "telegram" if (cfg.bot_token and chat_id) else "none"
    return build_notifier(name, TelegramConfig(bot_token=cfg.bot_token, chat_id=chat_id))
