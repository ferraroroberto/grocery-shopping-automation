"""Cart-automation control: start/stop the Playwright run, preview its argv,
and stream its output. One run at a time — it drives the shared Chrome
profile — tracked in this module's `_AUTOMATION_RUN`."""

import asyncio
import json
import uuid
from typing import Any

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app import automation_runner
from app.api_common import inventory_error

router = APIRouter()

_AUTOMATION_RUN: dict[str, Any] = {}


class AutomationStartPayload(BaseModel):
    store: str = "all"
    dry_run: bool = True
    cart_mode: str = "keep"


@router.post("/api/automation/start")
def automation_start(payload: AutomationStartPayload) -> dict[str, Any]:
    process = _AUTOMATION_RUN.get("process")
    if automation_runner.is_running(process):
        raise inventory_error(409, "automation already running")
    if payload.cart_mode not in {"keep", "clean"}:
        raise inventory_error(400, "cart_mode must be keep or clean")
    process, output_lines, reader_thread = automation_runner.start_run(
        payload.store,
        payload.dry_run,
        payload.cart_mode,
    )
    run_id = str(uuid.uuid4())
    _AUTOMATION_RUN.clear()
    _AUTOMATION_RUN.update(
        {
            "id": run_id,
            "process": process,
            "output_lines": output_lines,
            "reader_thread": reader_thread,
            "store": payload.store,
            "dry_run": payload.dry_run,
            "cart_mode": payload.cart_mode,
        }
    )
    return automation_status()


@router.post("/api/automation/stop")
def automation_stop() -> dict[str, Any]:
    automation_runner.stop_run(_AUTOMATION_RUN.get("process"))
    return automation_status()


@router.post("/api/automation/reset")
def automation_reset() -> dict[str, Any]:
    """Dismiss a finished run — clears server state so the panel can start fresh."""
    if not automation_runner.is_running(_AUTOMATION_RUN.get("process")):
        _AUTOMATION_RUN.clear()
    return automation_status()


@router.get("/api/automation/command")
def automation_command(store: str = "all", dry_run: bool = True, cart_mode: str = "keep") -> dict[str, str]:
    """Preview the exact argv a run would spawn (mirrors the Streamlit command preview)."""
    return {"command": " ".join(automation_runner.build_command(store, dry_run, cart_mode))}


@router.get("/api/automation/status")
def automation_status() -> dict[str, Any]:
    process = _AUTOMATION_RUN.get("process")
    lines = list(_AUTOMATION_RUN.get("output_lines") or [])
    running = automation_runner.is_running(process)
    return {
        "id": _AUTOMATION_RUN.get("id"),
        "running": running,
        "returncode": None if process is None or running else process.returncode,
        "store": _AUTOMATION_RUN.get("store", "all"),
        "dry_run": bool(_AUTOMATION_RUN.get("dry_run", True)),
        "cart_mode": _AUTOMATION_RUN.get("cart_mode", "keep"),
        "lines": lines,
    }


@router.get("/api/automation/events")
async def automation_events():
    async def event_stream():
        last_count = -1
        while True:
            status = automation_status()
            lines = status["lines"]
            if len(lines) != last_count or not status["running"]:
                last_count = len(lines)
                yield f"data: {json.dumps(status, ensure_ascii=False)}\n\n"
            if not status["running"]:
                break
            await asyncio.sleep(0.75)

    return StreamingResponse(event_stream(), media_type="text/event-stream")
