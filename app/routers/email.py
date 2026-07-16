"""Email-monitor config + manual checks — the Auto tab's Email Watch card,
backed by the server-side poller in `app/email_poller`."""

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from app import email_poller

router = APIRouter()


class MonitorSenderPayload(BaseModel):
    address: str
    enabled: bool


class EmailMonitorConfigPayload(BaseModel):
    enabled: bool
    interval_minutes: int = Field(..., ge=5, le=1440)
    senders: list[MonitorSenderPayload] = []


class EmailCheckPayload(BaseModel):
    # force=True re-processes the latest email even if already seen — the
    # Auto tab's end-to-end test path.
    force: bool = False


@router.get("/api/email-monitor/status")
def email_monitor_status() -> dict[str, Any]:
    """Config + last-check log for the Auto tab's Email Watch card."""
    return email_poller.status()


@router.put("/api/email-monitor/config")
def email_monitor_config(payload: EmailMonitorConfigPayload) -> dict[str, Any]:
    email_poller.update_config(
        enabled=payload.enabled,
        interval_minutes=payload.interval_minutes,
        sender_flags={s.address: s.enabled for s in payload.senders},
    )
    return email_poller.status()


@router.post("/api/email-monitor/check")
def email_monitor_check(payload: EmailCheckPayload) -> dict[str, Any]:
    """Run one check now (sync — Gmail fetch takes a few seconds)."""
    email_poller.run_checks(force=payload.force, trigger="manual")
    return email_poller.status()
