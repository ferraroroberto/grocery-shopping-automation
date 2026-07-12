"""Background email-confirmation poller for the Auto tab (issue #73).

Wraps the #72 seam (`automation.email_check.check_latest_confirmation`) in a
scheduled loop: a daemon thread wakes periodically, and when polling is
enabled and the configured interval has elapsed it checks every enabled
monitored sender's store. Each check (scheduled or manual) appends one entry
to a small gitignored log so the Auto tab can show what happened last.

Already-processed emails are skipped by the seam itself (processed-state
file, #72); a manual `force=True` run re-processes the latest email — the
end-to-end test path.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from automation.email_check import ConfirmationCheckResult, check_latest_confirmation
from src.gmail_config import (
    MonitoredSender,
    PollerSettings,
    load_monitored_senders,
    load_poller_settings,
    save_gmail_monitor_config,
)

logger = logging.getLogger("email_poller")

_REPO_ROOT = Path(__file__).resolve().parent.parent
CHECK_LOG_PATH = _REPO_ROOT / "config" / "email_check_log.json"
CHECK_LOG_LIMIT = 20
# Loop wake granularity — how quickly an enable/interval change takes effect.
_WAKE_SECONDS = 30.0

_check_lock = threading.Lock()  # one check at a time (poller vs. Check now)
_thread: Optional[threading.Thread] = None
_thread_lock = threading.Lock()
_stop = threading.Event()
_last_run_at: Optional[datetime] = None


def _load_log(path: Optional[Path] = None) -> list[dict[str, Any]]:
    target = path or CHECK_LOG_PATH
    if not target.exists():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ Could not read %s (%s); treating log as empty", target, exc)
        return []
    return raw if isinstance(raw, list) else []


def _append_log(entries: list[dict[str, Any]], path: Optional[Path] = None) -> None:
    target = path or CHECK_LOG_PATH
    log = (_load_log(target) + entries)[-CHECK_LOG_LIMIT:]
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def outcome_text(result: ConfirmationCheckResult) -> str:
    """One human-readable line for the Auto tab's last-check log."""

    if not result.checked:
        return f"Check skipped: {result.reason}"
    if result.already_processed:
        return "No new email — latest already processed"
    if result.match is not None:
        total = len(result.match.matched) + len(result.match.unmatched_website_names)
        text = f"Order confirmed — {len(result.match.matched)}/{total} items matched"
        if result.match.dropped_comida:
            text += "; dropped: " + ", ".join(result.match.dropped_comida)
        return text
    return result.reason or "No matching confirmation email"


def run_checks(*, force: bool = False, trigger: str = "manual") -> list[dict[str, Any]]:
    """Check every enabled monitored store once; log and return the entries."""

    global _last_run_at
    with _check_lock:
        stores: list[str] = []
        for sender in load_monitored_senders():
            if sender.enabled and sender.store and sender.store not in stores:
                stores.append(sender.store)

        entries: list[dict[str, Any]] = []
        now = datetime.now()
        if not stores:
            entries.append(
                {
                    "ts": now.isoformat(timespec="seconds"),
                    "store": "",
                    "trigger": trigger,
                    "ok": False,
                    "outcome": "No monitored sender enabled",
                    "notified": False,
                }
            )
        for store in stores:
            # Scheduled/normal checks keep a clean order silent (issue #73);
            # a forced test run always sends, to prove the loop end to end.
            result = check_latest_confirmation(
                store, ignore_processed=force, notify_only_on_problem=not force
            )
            entries.append(
                {
                    "ts": now.isoformat(timespec="seconds"),
                    "store": store,
                    "trigger": trigger,
                    "ok": result.checked,
                    "outcome": outcome_text(result),
                    "notified": result.notified,
                }
            )
            logger.info("ℹ️ Email check (%s, %s): %s", store, trigger, entries[-1]["outcome"])
        _append_log(entries)
        _last_run_at = now
        return entries


def update_config(
    *, enabled: bool, interval_minutes: int, sender_flags: dict[str, bool]
) -> None:
    """Persist poller settings + per-sender enable flags (by address)."""

    senders = load_monitored_senders()
    for sender in senders:
        if sender.address in sender_flags:
            sender.enabled = bool(sender_flags[sender.address])
    settings = PollerSettings(
        enabled=bool(enabled), interval_minutes=max(5, min(1440, int(interval_minutes)))
    )
    save_gmail_monitor_config(senders, settings)


def status() -> dict[str, Any]:
    """Config + last-check log + next scheduled run, for the Auto tab card."""

    settings = load_poller_settings()
    senders = load_monitored_senders()
    next_check = None
    if settings.enabled:
        base = _last_run_at or datetime.now()
        eta = base + timedelta(minutes=settings.interval_minutes) if _last_run_at else base
        next_check = eta.isoformat(timespec="seconds")
    return {
        "poller": {"enabled": settings.enabled, "interval_minutes": settings.interval_minutes},
        "senders": [
            {"address": s.address, "name": s.name, "store": s.store, "enabled": s.enabled}
            for s in senders
        ],
        "last_run_at": _last_run_at.isoformat(timespec="seconds") if _last_run_at else None,
        "next_check_at": next_check,
        "checks": list(reversed(_load_log())),
    }


def _poll_loop() -> None:
    while not _stop.wait(_WAKE_SECONDS):
        try:
            settings = load_poller_settings()
            if not settings.enabled:
                continue
            due = _last_run_at is None or (
                datetime.now() - _last_run_at >= timedelta(minutes=settings.interval_minutes)
            )
            if due:
                run_checks(trigger="scheduled")
        except Exception:  # a poll failure must never kill the loop
            logger.exception("❌ Scheduled email check failed")


def start_poller() -> None:
    """Start the background poll thread (idempotent)."""

    global _thread
    with _thread_lock:
        if _thread is not None and _thread.is_alive():
            return
        _stop.clear()
        _thread = threading.Thread(target=_poll_loop, name="email-poller", daemon=True)
        _thread.start()
        logger.info("ℹ️ Email poller thread started")
