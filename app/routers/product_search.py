"""On-demand product search (issue #87) — speak/type an item, search both
stores, validate a candidate card to fill its `buscador`. No automated
decision. Single-flight: one search runs at a time (it drives the shared
Chrome profile), tracked in this module's `_SEARCH_RUN`."""

import logging
import time
import uuid
from typing import Any

import pandas as pd
from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel, Field

from app import audio_hub, product_search_runner
from app.api_common import (
    inventory_error,
    inventory_payload,
    load_inventory_or_error,
    save_or_error,
)
from src.data import COLUMNS, CONFIG, apply_item_edit, build_new_item_row
from src.inventory_extract import ExtractionError
from src.voice_command import parse_voice_items

router = APIRouter()

_SEARCH_RUN: dict[str, Any] = {}


class ProductSearchStartPayload(BaseModel):
    # Free Spanish text (spoken transcript or typed) — parsed into item term(s)
    # via the same LLM path as the HA voice bridge, then searched (issue #87).
    text: str = ""
    model: str | None = None
    limit: int = 6


class ProductSearchSelectPayload(BaseModel):
    # Which product the user validated for which item. `inventory_idx` is the
    # existing row to fill (or null → create a new row named `term`).
    # `lugar` / `tenemos` / `cantidad` are the staged add-time parameters
    # (issue #92); None keeps the pre-#92 behavior so older clients still work.
    term: str
    store: str
    product_url: str
    name: str = ""
    inventory_idx: int | None = None
    lugar: str | None = None
    tenemos: int | None = Field(None, ge=0)
    cantidad: int | None = Field(None, ge=0)


def _search_status() -> dict[str, Any]:
    """Build the current search run's status, merging term metadata with results."""
    run = _SEARCH_RUN
    process = run.get("process")
    running = product_search_runner.is_running(process)
    started = run.get("started_at")
    if not running and started and "finished_at" not in run:
        run["finished_at"] = time.time()  # freeze elapsed on first observed completion
    end = run.get("finished_at") or time.time()
    elapsed = round(end - started, 1) if started else 0.0

    state, error = "idle", None
    progress = None
    results_by_term: dict[str, dict] = {}
    if run.get("id"):
        progress = product_search_runner.latest_progress(run.get("chunks") or [])
        if running:
            state = "running"
        else:
            parsed = product_search_runner.parse_result(run.get("chunks") or [])
            if parsed is None:
                state, error = "error", "the search did not return any result"
            elif parsed.get("error"):
                # e.g. the stores aren't logged in — surface the reason.
                state, error = "error", parsed["error"]
            else:
                state = "done"
                for entry in parsed.get("results", []):
                    results_by_term[entry.get("query", "")] = entry

    merged = []
    for meta in run.get("items") or []:
        entry = results_by_term.get(meta["term"], {})
        merged.append({
            "term": meta["term"],
            "inventory_idx": meta.get("inventory_idx"),
            "existing_super": meta.get("existing_super", ""),
            "candidates": entry.get("candidates", []),
            "store_errors": entry.get("errors", {}),
        })
    return {"id": run.get("id"), "state": state, "elapsed_s": elapsed,
            "items": merged, "error": error, "progress": progress}


@router.post("/api/product-search/transcribe")
async def product_search_transcribe(file: UploadFile = File(...)) -> dict[str, str]:
    """Transcribe a short spoken product query — whisper with no audit prompt.

    Unlike the audio-audit transcribe, this passes no Spanish audit prompt:
    the user speaks a bare product name and we want the raw term.
    """
    transcript = await audio_hub.transcode_and_transcribe(file)
    return {"transcript": transcript}


@router.post("/api/product-search/start")
def product_search_start(payload: ProductSearchStartPayload) -> dict[str, Any]:
    """Parse the spoken/typed text into item term(s) and start the store search."""
    if product_search_runner.is_running(_SEARCH_RUN.get("process")):
        raise inventory_error(409, "a product search is already running")
    text = payload.text.strip()
    if not text:
        raise inventory_error(400, "text is required")

    df = load_inventory_or_error()
    cfg = CONFIG["audio_audit"]
    model = payload.model or cfg["llm_model"]

    # Same LLM parse as the HA voice bridge — strips "añade"/quantities and maps
    # to an existing row when possible. If the hub is down, fall back to the raw
    # text as a single term so a typed query still works.
    items_meta: list[dict[str, Any]] = []
    try:
        parsed = parse_voice_items(
            text, df, base_url=cfg["llm_base_url"], model=model,
            max_tokens=cfg["llm_max_tokens"], timeout=cfg.get("llm_timeout", 600),
        )
        raw_items = [(it.name, it.idx) for it in parsed.items]
    except ExtractionError:
        logging.getLogger(__name__).warning("voice parse failed; searching raw text %r", text)
        raw_items = [(text, None)]

    seen: set[str] = set()
    for name, idx in raw_items:
        term = (name or "").strip()
        if not term or term.lower() in seen:
            continue
        seen.add(term.lower())
        existing_super = ""
        if idx is not None and idx in df.index:
            existing_super = str(df.at[idx, COLUMNS["super"]] or "").strip()
        items_meta.append({"term": term, "inventory_idx": idx, "existing_super": existing_super})

    if not items_meta:
        raise inventory_error(422, "could not find an item to search in what you said")

    terms = [m["term"] for m in items_meta]
    process, chunks, reader = product_search_runner.start(terms, max(1, payload.limit))
    _SEARCH_RUN.clear()
    _SEARCH_RUN.update({
        "id": str(uuid.uuid4()), "process": process, "chunks": chunks,
        "reader": reader, "items": items_meta, "started_at": time.time(),
    })
    return _search_status()


@router.get("/api/product-search/status")
def product_search_status() -> dict[str, Any]:
    return _search_status()


@router.post("/api/product-search/cancel")
def product_search_cancel() -> dict[str, Any]:
    product_search_runner.stop(_SEARCH_RUN.get("process"))
    return _search_status()


@router.post("/api/product-search/select")
def product_search_select(payload: ProductSearchSelectPayload) -> dict[str, Any]:
    """Fill `buscador` (+ `super`) from the validated card — the human's pick.

    Updates the existing row when `inventory_idx` is given, else creates a new
    row named `term`. The staged add-time parameters (`lugar` / `tenemos` /
    `cantidad`, issue #92) apply when sent; when omitted the pre-#92 defaults
    hold (keep the existing row's values with target raised to at least 1, or
    zone-less present 0 / target 1 for a new row).
    """
    store = payload.store.strip().lower()
    url = payload.product_url.strip()
    if not url:
        raise inventory_error(400, "product_url is required")

    df = load_inventory_or_error()
    idx = payload.inventory_idx
    if idx is not None and idx in df.index:
        row = df.loc[idx]
        # The chosen card's store is authoritative (the user picked *that*
        # product). Without an explicit target, ensure the item is actually on
        # a shopping list: a target of 0 leaves it unbuyable, so raise it to 1
        # — never lower an existing one. An explicit choice always wins.
        df = apply_item_edit(
            df, idx,
            super_value=store or str(row[COLUMNS["super"]] or ""),
            lugar=payload.lugar if payload.lugar is not None
            else str(row[COLUMNS["lugar"]] if pd.notna(row[COLUMNS["lugar"]]) else ""),
            comida=str(row[COLUMNS["comida"]] or ""),
            cantidad=payload.cantidad if payload.cantidad is not None
            else max(int(row[COLUMNS["cantidad"]]), 1),
            tenemos=payload.tenemos if payload.tenemos is not None
            else int(row[COLUMNS["tenemos"]]),
            buscador=url,
        )
    else:
        term = payload.term.strip() or payload.name.strip()
        if not term:
            raise inventory_error(400, "term or name is required to create an item")
        new_row = build_new_item_row(
            super_value=store,
            lugar=payload.lugar or "",
            comida=term,
            cantidad=1 if payload.cantidad is None else payload.cantidad,
            tenemos=payload.tenemos or 0,
            buscador=url,
        )
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    save_or_error(df)
    return inventory_payload(load_inventory_or_error())
