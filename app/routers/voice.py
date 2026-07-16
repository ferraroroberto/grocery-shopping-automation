"""Voice bridge for the HA Voice PE pucks (home-automation#315, #86)."""

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.api_common import inventory_error, load_inventory_or_error
from src.data import CONFIG, InventoryFileError, SpreadsheetLockedError, save_inventory_data
from src.inventory_extract import ExtractionError
from src.voice_command import (
    SPEECH_BUSY,
    apply_add,
    apply_set,
    build_add_speech,
    build_query_speech,
    build_set_speech,
    parse_voice_items,
)

router = APIRouter()


class VoiceCommandPayload(BaseModel):
    # intent is decided by HA's deterministic sentence match (home-automation
    # #315) — the LLM only ever parses items/quantities, never the operation.
    intent: str = Field(..., pattern="^(add|target|stock|query)$")
    text: str = ""
    model: str | None = None


@router.post("/api/voice/command")
def voice_command(payload: VoiceCommandPayload) -> dict[str, Any]:
    """Voice bridge for the HA Voice PE pucks (home-automation#315, #86).

    Receives free Spanish text relayed by an HA ``rest_command`` and returns a
    short ready-to-speak ``speech`` string the HA intent template relays
    verbatim — the same contract as home-automation's ``/api/wake-alarms/voice``.
    A locked spreadsheet answers 200 with a spoken "busy" line, so the puck
    says something useful instead of the generic failure branch.
    """
    try:
        df = load_inventory_or_error()
    except HTTPException as exc:
        if exc.status_code == 423:
            return {"speech": SPEECH_BUSY, "applied": [], "unmatched": []}
        raise

    if payload.intent == "query":
        return {"speech": build_query_speech(df), "applied": [], "unmatched": []}

    text = payload.text.strip()
    if not text:
        raise inventory_error(400, "text is required for this intent")

    cfg = CONFIG["audio_audit"]
    model = payload.model or cfg["llm_model"]
    try:
        parsed = parse_voice_items(
            text,
            df,
            base_url=cfg["llm_base_url"],
            model=model,
            max_tokens=cfg["llm_max_tokens"],
            timeout=cfg.get("llm_timeout", 600),
        )
    except ExtractionError as exc:
        raise inventory_error(502, str(exc)) from exc

    if payload.intent == "add":
        df, outcome = apply_add(df, parsed.items)
        speech = build_add_speech(outcome, parsed.ambiguous)
        changed = bool(outcome.bumped or outcome.created)
        applied = [
            {"name": name, "qty": qty, "action": "bumped"} for name, qty in outcome.bumped
        ] + [
            {"name": name, "qty": qty, "action": "created"} for name, qty in outcome.created
        ]
        unmatched = [str(m.get("phrase", "")) for m in parsed.ambiguous if m.get("phrase")]
    else:
        column_key = "cantidad" if payload.intent == "target" else "tenemos"
        df, outcome = apply_set(df, parsed.items, column_key)
        speech = build_set_speech(outcome, "target" if payload.intent == "target" else "stock")
        changed = bool(outcome.set_items)
        applied = [
            {"name": name, "qty": qty, "action": payload.intent}
            for name, qty in outcome.set_items
        ]
        unmatched = outcome.not_found + outcome.no_value

    if changed:
        try:
            save_inventory_data(df)
        except SpreadsheetLockedError:
            return {"speech": SPEECH_BUSY, "applied": [], "unmatched": unmatched}
        except InventoryFileError as exc:
            raise inventory_error(500, str(exc)) from exc

    return {"speech": speech, "applied": applied, "unmatched": unmatched, "model": model}
