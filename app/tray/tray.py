"""System-tray launcher — owns the FastAPI/PWA webapp lifecycle.

Mobile-first design means there's no real desktop UI to surface; the tray
exists so launching ``tray.bat`` brings the webapp up alongside Windows login
without keeping a console window open.

Menu:
    Open grocery                — open the local URL in the default browser
    Copy local URL               — clipboard the local URL (with ?token=…)
    Restart webapp                — stop + start so a new pull is picked up
    Status                        — popup with webapp state
    --
    Quit                          — stop the webapp and exit
"""

from __future__ import annotations

import logging
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

from app.tray.manager import WebappManager, load_config
from app.tray.single_instance import SingleInstance
from src.webapp_config import append_auth_token, load_webapp_config

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _build_icon():
    """Lazy import pystray + Pillow so plain CLI use doesn't drag them in."""
    from PIL import Image
    # No dedicated tray asset ships in this repo yet — fall back straight to
    # the app's brand color (matches the generated /app-icon.svg teal).
    return Image.new("RGB", (32, 32), (15, 118, 110))


def _clipboard_copy(text: str) -> bool:
    """Best-effort Windows clipboard. Returns True on success."""
    if sys.platform == "win32":
        try:
            p = subprocess.run(
                ["clip"],
                input=text,
                text=True,
                check=False,
                encoding="utf-8",
            )
            return p.returncode == 0
        except OSError as exc:
            logger.debug(f"clip failed: {exc}")
    return False


def _notify(icon, title: str, message: str) -> None:
    """Surface a tray message via the OS notification balloon, plus a log line.

    ``icon`` may be ``None`` when this fires before the tray icon exists yet
    (the webapp-start notification can race the icon's own construction); the
    OS balloon is skipped then, but the log line still lands.
    """
    logger.info(f"🔔 {title}: {message}")
    if icon is not None:
        try:
            icon.notify(message, title)
        except Exception as exc:  # noqa: BLE001 — the OS balloon is best-effort
            logger.debug(f"tray notify failed: {exc}")


def run_tray() -> int:
    """Run the tray icon. Returns when the user picks Quit."""
    try:
        import pystray  # type: ignore
        from pystray import Menu, MenuItem
    except ImportError as exc:
        logger.error(
            f"❌ pystray not installed ({exc}); install via `pip install -r requirements.txt`"
        )
        return 1

    # In-process single-instance guard (project-scaffolding#39): the tray.bat CIM
    # pre-check can let two near-simultaneous launches through, so the guarantee
    # must live in the process. Held for the tray's lifetime; the OS frees the
    # named mutex on exit. `instance` is intentionally kept referenced (quit).
    instance = SingleInstance(r"Global\grocery-shopping-automation-tray")
    if not instance.acquired:
        logger.info("ℹ️  Another grocery tray is already running; exiting.")
        return 0

    manager = WebappManager(load_config())

    def _open_url() -> str:
        webapp_cfg = load_webapp_config()
        return append_auth_token(manager.base_url, webapp_cfg.auth_token)

    def copy_local(icon, item):  # noqa: ARG001
        url = _open_url()
        if _clipboard_copy(url):
            _notify(icon, "Copied local URL", url)
        else:
            _notify(icon, "Local URL", url)

    def restart_webapp(icon, item):  # noqa: ARG001
        def _do_restart():
            try:
                _notify(icon, "Grocery", "Restarting webapp…")
                manager.restart(wait=True)
                _notify(icon, "Grocery webapp restarted", manager.base_url)
            except Exception as exc:  # noqa: BLE001
                logger.error(f"❌ webapp restart failed: {exc}")
                _notify(icon, "Restart failed", str(exc))

        threading.Thread(target=_do_restart, daemon=True).start()

    def show_status(icon, item):  # noqa: ARG001
        s = manager.status()
        _notify(icon, "Grocery status", f"{s.detail} · {s.base_url}")

    def quit_app(icon, item):  # noqa: ARG001
        logger.info("👋 Tray quit requested")
        try:
            manager.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"⚠️  stop failed: {exc}")
        instance.release()
        icon.stop()

    def on_left_click(icon, item):  # noqa: ARG001
        webbrowser.open(_open_url())

    menu = Menu(
        MenuItem("🛒 Open grocery", on_left_click, default=True),
        MenuItem("📋 Copy local URL", copy_local),
        Menu.SEPARATOR,
        MenuItem("🔄 Restart webapp", restart_webapp),
        MenuItem("ℹ️ Status", show_status),
        Menu.SEPARATOR,
        MenuItem("🚪 Quit", quit_app),
    )

    # Built (with its menu) before the background start thread below, so
    # `_start` always has a live `icon` to notify through instead of racing
    # `icon.run()` — which only has to happen last, to block until Quit.
    icon = pystray.Icon(
        "grocery-shopping-automation",
        icon=_build_icon(),
        title="Grocery",
        menu=menu,
    )

    starter_error: dict = {"exc": None}

    def _start():
        try:
            manager.start(wait=True)
            _notify(icon, "Grocery webapp ready", manager.base_url)
        except Exception as exc:  # noqa: BLE001
            starter_error["exc"] = exc
            logger.error(f"❌ webapp start failed: {exc}")
            _notify(icon, "Grocery start failed", str(exc))

    threading.Thread(target=_start, daemon=True).start()

    icon.run()
    if starter_error["exc"] is not None:
        return 1
    return 0
