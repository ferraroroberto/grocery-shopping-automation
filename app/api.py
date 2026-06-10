"""FastAPI entrypoint for the PWA rebuild."""

import asyncio
import csv
import hmac
import os
import platform
import socket
import subprocess
import threading
import uuid
from io import StringIO
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import automation_runner
from app.middleware import BearerTokenMiddleware
from src.data import (
    COLUMNS,
    CONFIG,
    InventoryFileError,
    SpreadsheetLockedError,
    bulk_apply_tenemos,
    get_supermarket_stats,
    load_inventory_data,
    save_inventory_data,
    update_item_quantity,
    update_target_quantity,
)
from src.audio_audit_core import clean_transcript, write_audit_log
from src.inventory_extract import ExtractionError, extract
from src.transcribe_client import FfmpegMissingError, TranscriptionError, transcode_to_wav, transcribe
from src.webapp_config import WebappConfig, load_webapp_config

STATIC_DIR = Path(__file__).resolve().parent / "static"
REPO_ROOT = Path(__file__).resolve().parent.parent
WHISPER_PROMPT_ES = (
    "Inventario domestico en espanol. "
    "Zonas: nevera, congelador, despensa, estante, garaje, bajo escalera. "
    "Cantidades: cero, uno, una, dos, tres, cuatro, cinco, seis, siete, ocho, nueve, diez."
)

_AUTOMATION_RUN: dict[str, Any] = {}
NO_CACHE_PATH_PREFIXES = ("/", "/static/")

app = FastAPI(
    title=CONFIG["app"]["title"],
    version=CONFIG["app"]["version"],
    description=CONFIG["app"]["description"],
)
app.state.webapp_config = load_webapp_config()
app.add_middleware(
    BearerTokenMiddleware,
    get_token=lambda: getattr(app.state.webapp_config, "auth_token", ""),
)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.middleware("http")
async def no_cache_for_pwa_assets(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == "/" or any(request.url.path.startswith(prefix) for prefix in NO_CACHE_PATH_PREFIXES[1:]):
        response.headers["Cache-Control"] = "no-store, max-age=0"
        response.headers["Pragma"] = "no-cache"
    return response


def _inventory_error(status_code: int, detail: str) -> HTTPException:
    return HTTPException(status_code=status_code, detail=detail)


def _load_inventory_or_error() -> pd.DataFrame:
    try:
        df = load_inventory_data()
    except SpreadsheetLockedError as exc:
        raise _inventory_error(423, str(exc)) from exc
    except InventoryFileError as exc:
        raise _inventory_error(500, str(exc)) from exc

    if df is None:
        raise _inventory_error(
            404,
            "Inventory file is missing or does not contain the configured columns.",
        )
    return df


def _save_or_error(df: pd.DataFrame) -> None:
    try:
        save_inventory_data(df)
    except SpreadsheetLockedError as exc:
        raise _inventory_error(423, str(exc)) from exc
    except InventoryFileError as exc:
        raise _inventory_error(500, str(exc)) from exc


def _records_from_frame(df: pd.DataFrame) -> list[dict[str, Any]]:
    indexed_df = df.reset_index(names="id")
    safe_df = indexed_df.astype(object).where(pd.notna(indexed_df), None)
    return safe_df.to_dict(orient="records")


def _inventory_payload(df: pd.DataFrame) -> dict[str, Any]:
    shopping_items = df[df[COLUMNS["comprar"]] > 0].copy()
    stats = get_supermarket_stats(shopping_items, set()) if not shopping_items.empty else {}
    zones = sorted(str(zone) for zone in df[COLUMNS["lugar"]].dropna().unique())
    supermarkets = sorted(str(sm) for sm in df[COLUMNS["super"]].dropna().unique())

    return {
        "app": {
            "title": CONFIG["app"]["title"],
            "version": CONFIG["app"]["version"],
        },
        "columns": COLUMNS,
        "summary": {
            "total_items": int(len(df)),
            "shopping_items": int(len(shopping_items)),
            "shopping_units": int(shopping_items[COLUMNS["comprar"]].sum()) if not shopping_items.empty else 0,
            "zones": zones,
            "supermarkets": supermarkets,
            "supermarket_stats": stats,
        },
        "audio": {
            "models": CONFIG["audio_audit"]["llm_models_available"],
            "default_model": CONFIG["audio_audit"]["llm_model"],
            "clamp": int(CONFIG["audio_audit"].get("max_count_clamp_above_target", 5)),
        },
        "items": _records_from_frame(df),
    }


def _https_cert_present() -> bool:
    return (
        (REPO_ROOT / "webapp" / "certificates" / "cert.pem").exists()
        or (REPO_ROOT / "certificates" / "cert.pem").exists()
    )


def _is_port_open(url: str, timeout: float = 1.5) -> bool:
    """TCP reachability probe for a service URL (hub / whisper-server)."""
    from urllib.parse import urlparse

    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


class DeltaPayload(BaseModel):
    delta: int = Field(..., ge=-1000, le=1000)


class QuantityPayload(BaseModel):
    value: int = Field(..., ge=0)


class ItemPayload(BaseModel):
    super_value: str = Field(..., alias="super")
    lugar: str
    comida: str
    cantidad: int = Field(..., ge=0)
    tenemos: int = Field(..., ge=0)
    buscador: str = ""


class AutomationStartPayload(BaseModel):
    store: str = "all"
    dry_run: bool = True
    cart_mode: str = "keep"


class MatchPayload(BaseModel):
    transcript: str
    model: str | None = None


class ApplyPayload(BaseModel):
    updates: dict[int, int]
    # Optional traceability context, sent by the PWA so the apply step can write
    # the same audit log the Streamlit app produced. All optional — a bare
    # {updates} still applies, it just logs less context.
    transcript: str = ""
    model: str = ""
    matches: dict[str, Any] | None = None
    audio_sha: str = ""
    audio_bytes: int = 0


@app.get("/", include_in_schema=False)
def index() -> FileResponse:
    """Serve the PWA shell."""
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/manifest.json", include_in_schema=False)
def manifest() -> JSONResponse:
    """Return the install metadata without adding another static file yet."""
    return JSONResponse(
        {
            "name": CONFIG["app"]["title"],
            "short_name": "Grocery",
            "description": CONFIG["app"]["description"],
            "start_url": "/",
            "scope": "/",
            "display": "standalone",
            "background_color": "#f8fafc",
            "theme_color": "#1E88E5",
            "icons": [
                {
                    "src": "/app-icon.svg",
                    "sizes": "any",
                    "type": "image/svg+xml",
                    "purpose": "any maskable",
                }
            ],
        }
    )


@app.get("/app-icon.svg", include_in_schema=False)
def app_icon() -> Response:
    """Small generated PWA icon for install previews."""
    svg = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128">
<rect width="128" height="128" rx="24" fill="#0f766e"/>
<path d="M35 42h65l-9 39H46z" fill="#f8fafc"/>
<path d="M28 32h14l6 49" fill="none" stroke="#f8fafc" stroke-width="8" stroke-linecap="round" stroke-linejoin="round"/>
<circle cx="52" cy="98" r="8" fill="#f8fafc"/>
<circle cx="86" cy="98" r="8" fill="#f8fafc"/>
</svg>"""
    return Response(content=svg, media_type="image/svg+xml")


@app.post("/api/login")
async def login(request: Request) -> dict[str, str]:
    """Swap the configured password for the bearer token."""
    cfg: WebappConfig = request.app.state.webapp_config
    if not cfg.auth_password:
        raise HTTPException(status_code=503, detail="password auth not configured")
    if not cfg.auth_token:
        raise HTTPException(status_code=503, detail="bearer token not configured")

    try:
        body = await request.json()
    except ValueError:
        body = {}
    presented = str(body.get("password") or "")
    if not hmac.compare_digest(presented, cfg.auth_password):
        raise HTTPException(status_code=401, detail="bad password")
    return {"token": cfg.auth_token}


@app.get("/healthz")
@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/inventory")
def inventory() -> dict[str, Any]:
    df = _load_inventory_or_error()
    return _inventory_payload(df)


@app.get("/api/access")
def access_urls() -> dict[str, Any]:
    cfg: WebappConfig = app.state.webapp_config
    scheme = "https" if _https_cert_present() else "http"
    local = f"{scheme}://127.0.0.1:{cfg.port}"
    lan = f"{scheme}://{_local_ip()}:{cfg.port}"
    cloudflare_url = ""
    tunnel_file = REPO_ROOT / "webapp" / "last_tunnel_url.txt"
    if tunnel_file.exists():
        cloudflare_url = tunnel_file.read_text(encoding="utf-8").strip()
    token = cfg.auth_token
    from src.webapp_config import append_auth_token

    return {
        "local": append_auth_token(local, token),
        "lan": append_auth_token(lan, token),
        "cloudflare": cloudflare_url,
        "auth_enabled": bool(token),
    }


@app.post("/api/items/{item_id}/current-delta")
def current_delta(item_id: int, payload: DeltaPayload) -> dict[str, Any]:
    df = _load_inventory_or_error()
    _get_row(df, item_id)
    try:
        update_item_quantity(df, item_id, payload.delta)
    except SpreadsheetLockedError as exc:
        raise _inventory_error(423, str(exc)) from exc
    except InventoryFileError as exc:
        raise _inventory_error(500, str(exc)) from exc
    return _inventory_payload(_load_inventory_or_error())


@app.post("/api/items/{item_id}/target-delta")
def target_delta(item_id: int, payload: DeltaPayload) -> dict[str, Any]:
    df = _load_inventory_or_error()
    _get_row(df, item_id)
    try:
        update_target_quantity(df, item_id, payload.delta)
    except SpreadsheetLockedError as exc:
        raise _inventory_error(423, str(exc)) from exc
    except InventoryFileError as exc:
        raise _inventory_error(500, str(exc)) from exc
    return _inventory_payload(_load_inventory_or_error())


@app.post("/api/items/{item_id}/current")
def set_current(item_id: int, payload: QuantityPayload) -> dict[str, Any]:
    df = _load_inventory_or_error()
    _get_row(df, item_id)
    try:
        bulk_apply_tenemos(df, {item_id: payload.value}, save=True)
    except SpreadsheetLockedError as exc:
        raise _inventory_error(423, str(exc)) from exc
    except InventoryFileError as exc:
        raise _inventory_error(500, str(exc)) from exc
    return _inventory_payload(_load_inventory_or_error())


@app.put("/api/items/{item_id}")
def update_item(item_id: int, payload: ItemPayload) -> dict[str, Any]:
    df = _load_inventory_or_error()
    snap = _get_row(df, item_id).copy()
    df.at[item_id, COLUMNS["super"]] = payload.super_value.strip()
    df.at[item_id, COLUMNS["lugar"]] = payload.lugar.strip()
    df.at[item_id, COLUMNS["comida"]] = payload.comida.strip()
    df.at[item_id, COLUMNS["cantidad"]] = int(payload.cantidad)
    df.at[item_id, COLUMNS["tenemos"]] = int(payload.tenemos)
    df.at[item_id, COLUMNS["buscador"]] = payload.buscador.strip()
    df.at[item_id, COLUMNS["comprar"]] = max(0, int(payload.cantidad) - int(payload.tenemos))
    try:
        _save_or_error(df)
    except HTTPException:
        df.loc[item_id] = snap
        raise
    return _inventory_payload(_load_inventory_or_error())


@app.delete("/api/items/{item_id}")
def delete_item(item_id: int) -> dict[str, Any]:
    df = _load_inventory_or_error()
    _get_row(df, item_id)
    df = df.drop(item_id)
    _save_or_error(df)
    return _inventory_payload(_load_inventory_or_error())


@app.post("/api/items")
def add_item(payload: ItemPayload) -> dict[str, Any]:
    if not payload.comida.strip():
        raise _inventory_error(400, "item name is required")
    df = _load_inventory_or_error()
    new_row = {
        COLUMNS["super"]: payload.super_value.strip(),
        COLUMNS["lugar"]: payload.lugar.strip(),
        COLUMNS["comida"]: payload.comida.strip(),
        COLUMNS["cantidad"]: int(payload.cantidad),
        COLUMNS["tenemos"]: int(payload.tenemos),
        COLUMNS["buscador"]: payload.buscador.strip(),
        COLUMNS["comprar"]: max(0, int(payload.cantidad) - int(payload.tenemos)),
    }
    df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)
    _save_or_error(df)
    return _inventory_payload(_load_inventory_or_error())


@app.get("/api/export.csv")
def export_csv() -> Response:
    df = _load_inventory_or_error()
    buffer = StringIO()
    writer = csv.writer(buffer)
    writer.writerow(df.columns.tolist())
    for row in df.itertuples(index=False):
        writer.writerow(list(row))
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="inventory.csv"'},
    )


@app.post("/api/actions/open-spreadsheet")
def open_spreadsheet() -> dict[str, str]:
    path = Path(CONFIG["data"]["xlsx_file"]).expanduser()
    if not path.is_absolute():
        path = (REPO_ROOT / path).resolve()
    if not path.exists():
        raise _inventory_error(404, f"file not found: {path}")
    if platform.system() == "Windows":
        os.startfile(str(path))  # noqa: S606
    elif platform.system() == "Darwin":
        subprocess.run(["open", str(path)], check=False)
    else:
        subprocess.run(["xdg-open", str(path)], check=False)
    return {"status": "opened"}


@app.post("/api/actions/close")
def close_app() -> dict[str, str]:
    def _exit_later() -> None:
        import time

        time.sleep(0.3)
        os._exit(0)

    threading.Thread(target=_exit_later, daemon=True).start()
    return {"status": "closing"}


@app.post("/api/automation/start")
def automation_start(payload: AutomationStartPayload) -> dict[str, Any]:
    process = _AUTOMATION_RUN.get("process")
    if automation_runner.is_running(process):
        raise _inventory_error(409, "automation already running")
    if payload.cart_mode not in {"keep", "clean"}:
        raise _inventory_error(400, "cart_mode must be keep or clean")
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


@app.post("/api/automation/stop")
def automation_stop() -> dict[str, Any]:
    automation_runner.stop_run(_AUTOMATION_RUN.get("process"))
    return automation_status()


@app.get("/api/automation/command")
def automation_command(store: str = "all", dry_run: bool = True, cart_mode: str = "keep") -> dict[str, str]:
    """Preview the exact argv a run would spawn (mirrors the Streamlit command preview)."""
    return {"command": " ".join(automation_runner.build_command(store, dry_run, cart_mode))}


@app.get("/api/automation/status")
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


@app.get("/api/automation/events")
async def automation_events():
    async def event_stream():
        last_count = -1
        while True:
            status = automation_status()
            lines = status["lines"]
            if len(lines) != last_count or not status["running"]:
                last_count = len(lines)
                yield f"data: {json_dumps(status)}\n\n"
            if not status["running"]:
                break
            await asyncio.sleep(0.75)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


def json_dumps(payload: Any) -> str:
    import json

    return json.dumps(payload, ensure_ascii=False)


@app.post("/api/audio/transcribe")
async def audio_transcribe(file: UploadFile = File(...)) -> dict[str, str]:
    cfg = CONFIG["audio_audit"]
    audio_bytes = await file.read()
    if not audio_bytes:
        raise _inventory_error(400, "empty audio file")
    # whisper-server only decodes WAV/PCM and 400s on the webm/mp4 that browser
    # MediaRecorder produces — transcode first. Fall back to the raw bytes only
    # if ffmpeg is unavailable (still correct for a genuine WAV upload).
    filename, mime = "audio.wav", "audio/wav"
    try:
        audio_bytes = transcode_to_wav(audio_bytes)
    except FfmpegMissingError:
        import logging

        logging.getLogger(__name__).warning(
            "ffmpeg missing — posting raw audio to whisper (works only for WAV uploads)"
        )
        filename = file.filename or "audio.webm"
        mime = file.content_type or "audio/webm"
    except TranscriptionError as exc:
        raise _inventory_error(502, str(exc)) from exc
    try:
        transcript = transcribe(
            audio_bytes,
            whisper_url=cfg["whisper_url"],
            model=cfg["whisper_model"],
            language=cfg.get("language", "es"),
            filename=filename,
            mime=mime,
            timeout=600,
            temperature=0.0,
            prompt=WHISPER_PROMPT_ES,
        )
    except TranscriptionError as exc:
        raise _inventory_error(502, str(exc)) from exc
    return {"transcript": transcript}


@app.get("/api/audio/health")
def audio_health() -> dict[str, Any]:
    """Reachability of the local LLM hub and whisper-server, for the audio view's
    service-status banner. Mirrors the Streamlit `_service_status_banner` probe."""
    cfg = CONFIG["audio_audit"]
    return {
        "hub_ok": _is_port_open(cfg["llm_base_url"]),
        "whisper_ok": _is_port_open(cfg["whisper_url"]),
        "hub_url": cfg["llm_base_url"],
        "whisper_url": cfg["whisper_url"],
    }


@app.post("/api/audio/match")
def audio_match(payload: MatchPayload) -> dict[str, Any]:
    cfg = CONFIG["audio_audit"]
    df = _load_inventory_or_error()
    transcript = clean_transcript(payload.transcript.strip())
    if not transcript:
        raise _inventory_error(400, "transcript is empty")
    model = payload.model or cfg["llm_model"]
    try:
        result = extract(
            transcript,
            df,
            base_url=cfg["llm_base_url"],
            model=model,
            max_tokens=cfg["llm_max_tokens"],
            timeout=cfg.get("llm_timeout", 600),
        )
    except ExtractionError as exc:
        raise _inventory_error(502, str(exc)) from exc
    return {
        "items": result.items,
        "zones_mentioned": result.zones_mentioned,
        "unmatched_mentions": result.unmatched_mentions,
        "raw_text": result.raw_text,
        "model": model,
        "transcript_chars": len(transcript),
        "candidates": int(len(df)),
    }


@app.post("/api/audio/apply")
def audio_apply(payload: ApplyPayload) -> dict[str, Any]:
    df = _load_inventory_or_error()
    cleaned = {int(idx): max(0, int(value)) for idx, value in payload.updates.items()}
    old_tenemos = {idx: int(_get_row(df, idx)[COLUMNS["tenemos"]]) for idx in cleaned}
    try:
        bulk_apply_tenemos(df, cleaned, save=True)
    except SpreadsheetLockedError as exc:
        raise _inventory_error(423, str(exc)) from exc
    except InventoryFileError as exc:
        raise _inventory_error(500, str(exc)) from exc

    new_df = _load_inventory_or_error()
    cfg = CONFIG["audio_audit"]
    log_path = ""
    if cleaned:
        try:
            path = write_audit_log(
                df=new_df,
                old_tenemos=old_tenemos,
                accepted=cleaned,
                target_xlsx=str(CONFIG["data"]["xlsx_file"]),
                transcript=payload.transcript,
                model=payload.model or cfg["llm_model"],
                whisper_model=cfg["whisper_model"],
                result=payload.matches,
                audio_sha=payload.audio_sha,
                audio_bytes_len=payload.audio_bytes,
                logs_dir=REPO_ROOT / cfg["logs_dir"],
            )
            log_path = str(path)
        except OSError as exc:  # logging must never block the inventory update
            import logging

            logging.getLogger(__name__).warning("audit log write failed: %s", exc)

    result = _inventory_payload(new_df)
    result["audio_log_path"] = log_path
    return result


def _get_row(df: pd.DataFrame, item_id: int) -> pd.Series:
    if item_id not in df.index:
        raise _inventory_error(404, f"item {item_id} not found")
    return df.loc[item_id]


def _local_ip() -> str:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return "127.0.0.1"
