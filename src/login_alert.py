"""The owner alert for a client guessing the login password.

Binds the vendored :class:`~src.login_lockout.LoginLockout` callback to the
app's Telegram notifier. The message carries the app name, the client address,
the failure count and any alerts the cooldown held back, never a password or a
token.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from typing import Optional

from src.login_lockout import LockoutAlert
from src.notify import Notifier, NotifierError

logger = logging.getLogger("login_alert")


def alert_text(app_name: str, alert: LockoutAlert) -> str:
    held = f" (+{alert.suppressed} more held back)" if alert.suppressed else ""
    return f"🚨 {app_name}: {alert.failures} wrong passwords in a row from {alert.key}{held}"


def build_login_alert(
    get_notifier: Callable[[], Optional[Notifier]], app_name: str
) -> Callable[[LockoutAlert], None]:
    """An ``on_alert`` callback that sends from a thread, so a login never waits on Telegram.

    ``get_notifier`` runs on that thread too, so reading the config file stays
    off the request path as well.
    """

    def alert_owner(alert: LockoutAlert) -> None:
        text = alert_text(app_name, alert)

        def send() -> None:
            try:
                notifier = get_notifier()
                if notifier is not None:
                    notifier.send_text(text)
            except NotifierError as exc:
                logger.warning(f"⚠️ login alert not delivered: {type(exc).__name__}")

        threading.Thread(target=send, daemon=True, name="login-alert").start()

    return alert_owner
