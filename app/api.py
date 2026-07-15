"""FastAPI entrypoint for the PWA rebuild."""

import asyncio
import csv
import hmac
import logging
import os
import platform
import subprocess
import threading
import time
import uuid
from contextlib import asynccontextmanager
from io import StringIO
from pathlib import Path
from typing import Any

import httpx
import pandas as pd
from fastapi import FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from app import automation_runner, email_poller, product_search_runner
from app.middleware import BearerTokenMiddleware
from src.data import (
    COLUMNS,
    CONFIG,
    InventoryFileError,
    SpreadsheetLockedError,
    apply_item_edit,
    build_new_item_row,
    bulk_apply_tenemos,
    get_supermarket_stats,
    load_inventory_data,
    save_inventory_data,
    update_item_quantity,
    update_target_quantity,
)
from src.audio_audit_core import WHISPER_PROMPT_ES, clean_transcript, write_audit_log
from src.inventory_extract import ExtractionError, extract
from src.voice_command import (
    SPEECH_BUSY,
    apply_add,
    apply_set,
    build_add_speech,
    build_query_speech,
    build_set_speech,
    parse_voice_items,
)
from src.net import is_port_open, local_ip
from src.static_versioning import BuildInfo
from src.transcribe_client import FfmpegMissingError, TranscriptionError, transcode_to_wav, transcribe
from src.webapp_config import WebappConfig, load_webapp_config

STATIC_DIR = Path(__file__).resolve().parent / "static"
REPO_ROOT = Path(__file__).resolve().parent.parent

_AUTOMATION_RUN: dict[str, Any] = {}
# In-flight on-demand product search (issue #87). Single-flight: one search runs
# at a time (it drives the shared Chrome profile), tracked here like the cart run.
_SEARCH_RUN: dict[str, Any] = {}

# Build identity, computed once at import — the app restarts on every code
# edit, so a fresh process always reflects the deployed code.
BUILD_INFO = BuildInfo(STATIC_DIR, REPO_ROOT)

# Hash-stamped assets (.js / .css) get a one-year immutable cache: the
# content hash in the query string makes the URL change on every edit, so a
# stale copy can never be served. index.html itself is served no-cache (see
# the `index` route) so it always revalidates and picks up new asset hashes.
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


class CachingStaticFiles(StaticFiles):
    """``StaticFiles`` with per-file ``Cache-Control`` + JS import stamping.

    Starlette's mount serves every file with only ``ETag`` /
    ``Last-Modified``, leaving iOS Safari free to heuristic-cache. This
    subclass stamps an explicit policy keyed on the suffix, and rewrites
    the ``import './x.js'`` URLs inside every ``.js`` module with a content
    hash so a stale module can never be served — the hashed URL changes on
    every edit.

    Local port of the fleet pattern (photo-ocr / voice-transcriber); see
    ``src/static_versioning.py``.
    """

    def __init__(self, *, directory: Any, build_info: BuildInfo) -> None:
        super().__init__(directory=directory)
        self._build_info = build_info

    def file_response(self, full_path, *args, **kwargs):  # type: ignore[override]
        path = Path(full_path)
        suffix = path.suffix.lower()

        if suffix == ".js":
            # Rewrite the module graph's `import './x.js'` URLs with a
            # content hash, then long-cache — the hashed URL is the cache
            # key, so an edit invalidates it for free.
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                return super().file_response(full_path, *args, **kwargs)
            return Response(
                content=self._build_info.stamp_js(body),
                media_type="text/javascript",
                headers={"Cache-Control": _IMMUTABLE_CACHE},
            )

        response = super().file_response(full_path, *args, **kwargs)
        if suffix == ".css":
            response.headers["Cache-Control"] = _IMMUTABLE_CACHE
        return response


@asynccontextmanager
async def _lifespan(_: FastAPI):
    # Don't spawn the real email-poll thread inside pytest's TestClient
    # startups — a test run must never trigger a live Gmail check.
    if "PYTEST_CURRENT_TEST" not in os.environ:
        email_poller.start_poller()
    yield


app = FastAPI(
    title=CONFIG["app"]["title"],
    version=CONFIG["app"]["version"],
    description=CONFIG["app"]["description"],
    lifespan=_lifespan,
)
app.state.webapp_config = load_webapp_config()
app.add_middleware(
    BearerTokenMiddleware,
    get_token=lambda: getattr(app.state.webapp_config, "auth_token", ""),
)
app.mount("/static", CachingStaticFiles(directory=STATIC_DIR, build_info=BUILD_INFO), name="static")


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
    _mutate_or_error(save_inventory_data, df)


def _mutate_or_error(fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Call a data-layer mutator, translating its lock/file errors to HTTP.

    Shared by every endpoint that calls into `src/data.py`'s mutators —
    `SpreadsheetLockedError` -> 423, `InventoryFileError` -> 500 — so that
    translation lives in one place instead of being re-typed per call site.
    """
    try:
        return fn(*args, **kwargs)
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


# --------------------------------------------------------------------------- #
# voice-transcriber session-API proxy
#
# The sibling voice-transcriber webapp owns hardened recording: 1 s chunks are
# streamed and archived to disk the moment they arrive (recoverable if the phone
# dies), whisper runs rolling partials over SSE, /finish returns the canonical
# transcript, and /retranscribe recovers a saved take. Rather than duplicate any
# of that, grocery proxies the recording lifecycle to it on loopback. The VT
# middleware bypasses loopback callers, so no token is needed; verify=False
# because it serves a self-signed cert. The phone only ever talks to grocery's
# own origin — no CORS, no second tunnel.
# --------------------------------------------------------------------------- #


def _voice_url() -> str:
    return CONFIG["audio_audit"].get("voice_transcriber_url", "https://127.0.0.1:8443").rstrip("/")


def _vt_client(read_timeout: float | None = 600.0) -> httpx.AsyncClient:
    """AsyncClient for the voice-transcriber proxy. Factored out so tests can
    inject an httpx.MockTransport. read_timeout is None for the long-lived SSE
    stream (a walk can run >10 min) and bounded for unary calls."""
    return httpx.AsyncClient(
        base_url=_voice_url(),
        verify=False,
        timeout=httpx.Timeout(connect=5.0, read=read_timeout, write=600.0, pool=5.0),
    )


async def _vt_request(method: str, path: str, **kwargs: Any) -> httpx.Response:
    """Forward one unary request to the voice-transcriber webapp.

    Connection failures map to 502 (recorder down → the audio view shows the
    banner); a VT 4xx (unknown session, no chunks) propagates as-is so the
    client gets the meaningful detail; a VT 5xx collapses to 502.
    """
    try:
        async with _vt_client() as client:
            resp = await client.request(method, path, **kwargs)
    except httpx.RequestError as exc:
        raise _inventory_error(502, f"voice-transcriber unreachable at {_voice_url()}: {exc}") from exc
    if resp.status_code >= 400:
        status = resp.status_code if resp.status_code < 500 else 502
        raise _inventory_error(status, f"voice-transcriber error {resp.status_code}: {resp.text[:300]}")
    return resp


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


class VoiceCommandPayload(BaseModel):
    # intent is decided by HA's deterministic sentence match (home-automation
    # #315) — the LLM only ever parses items/quantities, never the operation.
    intent: str = Field(..., pattern="^(add|target|stock|query)$")
    text: str = ""
    model: str | None = None


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


class ProductSearchStartPayload(BaseModel):
    # Free Spanish text (spoken transcript or typed) — parsed into item term(s)
    # via the same LLM path as the HA voice bridge, then searched (issue #87).
    text: str = ""
    model: str | None = None
    limit: int = 6


class ProductSearchSelectPayload(BaseModel):
    # Which product the user validated for which item. `inventory_idx` is the
    # existing row to fill (or null → create a new row named `term`).
    term: str
    store: str
    product_url: str
    name: str = ""
    inventory_idx: int | None = None


@app.get("/", include_in_schema=False)
def index() -> HTMLResponse:
    """Serve the PWA shell.

    Stamp the directly-referenced asset URLs with their content hash and
    force the entry document to revalidate (``no-cache``), so an app restart
    after an edit is always picked up — no stale iOS PWA cache.
    """
    html = BUILD_INFO.stamp_html((STATIC_DIR / "index.html").read_text(encoding="utf-8"))
    return HTMLResponse(html, headers={"Cache-Control": "no-cache, must-revalidate"})


@app.get("/api/version")
def version() -> JSONResponse:
    """Build identity (git SHA, build time, fleet asset hash).

    Feeds the PWA's footer build readout + stale-shell reload guard — the
    same contract as home-automation's ``/api/version``.
    """
    return JSONResponse(BUILD_INFO.as_dict())


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
            "background_color": "#ffffff",
            "theme_color": "#ffffff",
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
    lan = f"{scheme}://{local_ip()}:{cfg.port}"
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
    _mutate_or_error(update_item_quantity, df, item_id, payload.delta)
    return _inventory_payload(_load_inventory_or_error())


@app.post("/api/items/{item_id}/target-delta")
def target_delta(item_id: int, payload: DeltaPayload) -> dict[str, Any]:
    df = _load_inventory_or_error()
    _get_row(df, item_id)
    _mutate_or_error(update_target_quantity, df, item_id, payload.delta)
    return _inventory_payload(_load_inventory_or_error())


@app.post("/api/items/{item_id}/current")
def set_current(item_id: int, payload: QuantityPayload) -> dict[str, Any]:
    df = _load_inventory_or_error()
    _get_row(df, item_id)
    _mutate_or_error(bulk_apply_tenemos, df, {item_id: payload.value}, save=True)
    return _inventory_payload(_load_inventory_or_error())


@app.put("/api/items/{item_id}")
def update_item(item_id: int, payload: ItemPayload) -> dict[str, Any]:
    df = _load_inventory_or_error()
    snap = _get_row(df, item_id).copy()
    apply_item_edit(
        df,
        item_id,
        super_value=payload.super_value,
        lugar=payload.lugar,
        comida=payload.comida,
        cantidad=payload.cantidad,
        tenemos=payload.tenemos,
        buscador=payload.buscador,
    )
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
    new_row = build_new_item_row(
        super_value=payload.super_value,
        lugar=payload.lugar,
        comida=payload.comida,
        cantidad=payload.cantidad,
        tenemos=payload.tenemos,
        buscador=payload.buscador,
    )
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


@app.post("/api/automation/reset")
def automation_reset() -> dict[str, Any]:
    """Dismiss a finished run — clears server state so the panel can start fresh."""
    if not automation_runner.is_running(_AUTOMATION_RUN.get("process")):
        _AUTOMATION_RUN.clear()
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


# ─────────────────────────────────────────────────────────────────────────────
# On-demand product search (issue #87) — speak/type an item, search both stores,
# validate a candidate card to fill its `buscador`. No automated decision.
# ─────────────────────────────────────────────────────────────────────────────
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


@app.post("/api/product-search/transcribe")
async def product_search_transcribe(file: UploadFile = File(...)) -> dict[str, str]:
    """Transcribe a short spoken product query — whisper with language auto-detect.

    Unlike the audio-audit transcribe, this passes no forced language and no
    Spanish audit prompt: the user speaks a product name (usually Spanish, but
    auto-detect is friendlier) and we want the bare term.
    """
    cfg = CONFIG["audio_audit"]
    audio_bytes = await file.read()
    if not audio_bytes:
        raise _inventory_error(400, "empty audio file")
    filename, mime = "audio.wav", "audio/wav"
    try:
        audio_bytes = transcode_to_wav(audio_bytes)
    except FfmpegMissingError:
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
            # Force Spanish: with no language, whisper-large-v3-turbo sometimes
            # *translates* a Spanish clip to English. The stores are Spanish, so
            # we always want the Spanish term. (Same language the audio-audit uses.)
            language=cfg.get("language", "es"),
            filename=filename,
            mime=mime,
            timeout=600,
            temperature=0.0,
        )
    except TranscriptionError as exc:
        raise _inventory_error(502, str(exc)) from exc
    return {"transcript": transcript}


@app.post("/api/product-search/start")
def product_search_start(payload: ProductSearchStartPayload) -> dict[str, Any]:
    """Parse the spoken/typed text into item term(s) and start the store search."""
    if product_search_runner.is_running(_SEARCH_RUN.get("process")):
        raise _inventory_error(409, "a product search is already running")
    text = payload.text.strip()
    if not text:
        raise _inventory_error(400, "text is required")

    df = _load_inventory_or_error()
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
        raise _inventory_error(422, "could not find an item to search in what you said")

    terms = [m["term"] for m in items_meta]
    process, chunks, reader = product_search_runner.start(terms, max(1, payload.limit))
    _SEARCH_RUN.clear()
    _SEARCH_RUN.update({
        "id": str(uuid.uuid4()), "process": process, "chunks": chunks,
        "reader": reader, "items": items_meta, "started_at": time.time(),
    })
    return _search_status()


@app.get("/api/product-search/status")
def product_search_status() -> dict[str, Any]:
    return _search_status()


@app.post("/api/product-search/cancel")
def product_search_cancel() -> dict[str, Any]:
    product_search_runner.stop(_SEARCH_RUN.get("process"))
    return _search_status()


@app.post("/api/product-search/select")
def product_search_select(payload: ProductSearchSelectPayload) -> dict[str, Any]:
    """Fill `buscador` (+ `super`) from the validated card — the human's pick.

    Updates the existing row when `inventory_idx` is given, else creates a new
    row named `term` (target 1, so it lands on the shopping list).
    """
    store = payload.store.strip().lower()
    url = payload.product_url.strip()
    if not url:
        raise _inventory_error(400, "product_url is required")

    df = _load_inventory_or_error()
    idx = payload.inventory_idx
    if idx is not None and idx in df.index:
        row = df.loc[idx]
        # The chosen card's store is authoritative (the user picked *that*
        # product). Ensure the item is actually on a shopping list: a target of
        # 0 leaves it unbuyable, so raise it to 1 — never lower an existing one.
        df = apply_item_edit(
            df, idx,
            super_value=store or str(row[COLUMNS["super"]] or ""),
            lugar=str(row[COLUMNS["lugar"]] if pd.notna(row[COLUMNS["lugar"]]) else ""),
            comida=str(row[COLUMNS["comida"]] or ""),
            cantidad=max(int(row[COLUMNS["cantidad"]]), 1),
            tenemos=int(row[COLUMNS["tenemos"]]),
            buscador=url,
        )
    else:
        term = payload.term.strip() or payload.name.strip()
        if not term:
            raise _inventory_error(400, "term or name is required to create an item")
        new_row = build_new_item_row(
            super_value=store, lugar="", comida=term, cantidad=1, tenemos=0, buscador=url,
        )
        df = pd.concat([df, pd.DataFrame([new_row])], ignore_index=True)

    _save_or_error(df)
    return _inventory_payload(_load_inventory_or_error())


@app.get("/api/email-monitor/status")
def email_monitor_status() -> dict[str, Any]:
    """Config + last-check log for the Auto tab's Email Watch card."""
    return email_poller.status()


@app.put("/api/email-monitor/config")
def email_monitor_config(payload: EmailMonitorConfigPayload) -> dict[str, Any]:
    email_poller.update_config(
        enabled=payload.enabled,
        interval_minutes=payload.interval_minutes,
        sender_flags={s.address: s.enabled for s in payload.senders},
    )
    return email_poller.status()


@app.post("/api/email-monitor/check")
def email_monitor_check(payload: EmailCheckPayload) -> dict[str, Any]:
    """Run one check now (sync — Gmail fetch takes a few seconds)."""
    email_poller.run_checks(force=payload.force, trigger="manual")
    return email_poller.status()


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
    voice_url = _voice_url()
    return {
        "hub_ok": is_port_open(cfg["llm_base_url"]),
        "whisper_ok": is_port_open(cfg["whisper_url"]),
        "voice_ok": is_port_open(voice_url),
        "hub_url": cfg["llm_base_url"],
        "whisper_url": cfg["whisper_url"],
        "voice_url": voice_url,
    }


@app.post("/api/audio/session")
async def audio_session_create() -> dict[str, Any]:
    """Open a voice-transcriber session for a hardened, streamed recording.

    incognito=True so audit takes don't pile into the voice app's History — they
    live only long enough to drive this audit (and a Redo/retranscribe)."""
    cfg = CONFIG["audio_audit"]
    resp = await _vt_request(
        "POST", "/api/sessions",
        json={"language": cfg.get("language", "es"), "incognito": True},
    )
    return {"session_id": resp.json()["session_id"]}


@app.post("/api/audio/session/{session_id}/chunk")
async def audio_session_chunk(session_id: str, request: Request) -> dict[str, Any]:
    """Stream one recording chunk straight to the PC (archived on arrival)."""
    body = await request.body()
    headers = {}
    ctype = request.headers.get("content-type")
    if ctype:
        headers["Content-Type"] = ctype
    resp = await _vt_request(
        "POST", f"/api/sessions/{session_id}/chunk", content=body, headers=headers,
    )
    return resp.json()


@app.get("/api/audio/session/{session_id}/events")
async def audio_session_events(session_id: str) -> StreamingResponse:
    """Proxy the voice-transcriber rolling-transcription SSE through grocery's
    own origin so EventSource on the phone never needs a second tunnel."""

    async def proxy():
        try:
            async with _vt_client(read_timeout=None) as client:
                async with client.stream("GET", f"/api/sessions/{session_id}/events") as resp:
                    async for chunk in resp.aiter_raw():
                        yield chunk
        except httpx.RequestError:
            yield b'event: error\ndata: {"detail":"voice-transcriber unreachable"}\n\n'

    return StreamingResponse(
        proxy(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no", "Connection": "keep-alive"},
    )


@app.post("/api/audio/session/{session_id}/finish")
async def audio_session_finish(
    session_id: str, request: Request, language: str | None = None, translate: bool = False,
) -> dict[str, Any]:
    """Close the chunked take and return the canonical transcript."""
    cfg = CONFIG["audio_audit"]
    lang = language or cfg.get("language", "es")
    body = await request.body()
    resp = await _vt_request(
        "POST", f"/api/sessions/{session_id}/finish",
        params={"language": lang, "translate": str(translate).lower()},
        content=body or b"{}",
        headers={"Content-Type": "application/json"},
    )
    data = resp.json()
    return {
        "transcript": data.get("transcript", ""),
        "language": data.get("language", lang),
        "silent": bool(data.get("silent", False)),
    }


@app.post("/api/audio/session/{session_id}/retranscribe")
async def audio_session_retranscribe(
    session_id: str, language: str | None = None, translate: bool = False,
) -> dict[str, Any]:
    """Re-run whisper on the saved audio (Redo / crash recovery)."""
    cfg = CONFIG["audio_audit"]
    lang = language or cfg.get("language", "es")
    resp = await _vt_request(
        "POST", f"/api/sessions/{session_id}/retranscribe",
        params={"language": lang, "translate": str(translate).lower()},
    )
    data = resp.json()
    return {
        "transcript": data.get("transcript", ""),
        "language": data.get("language", lang),
        "silent": bool(data.get("silent", False)),
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
    _mutate_or_error(bulk_apply_tenemos, df, cleaned, save=True)

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


@app.post("/api/voice/command")
def voice_command(payload: VoiceCommandPayload) -> dict[str, Any]:
    """Voice bridge for the HA Voice PE pucks (home-automation#315, #86).

    Receives free Spanish text relayed by an HA ``rest_command`` and returns a
    short ready-to-speak ``speech`` string the HA intent template relays
    verbatim — the same contract as home-automation's ``/api/wake-alarms/voice``.
    A locked spreadsheet answers 200 with a spoken "busy" line, so the puck
    says something useful instead of the generic failure branch.
    """
    try:
        df = _load_inventory_or_error()
    except HTTPException as exc:
        if exc.status_code == 423:
            return {"speech": SPEECH_BUSY, "applied": [], "unmatched": []}
        raise

    if payload.intent == "query":
        return {"speech": build_query_speech(df), "applied": [], "unmatched": []}

    text = payload.text.strip()
    if not text:
        raise _inventory_error(400, "text is required for this intent")

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
        raise _inventory_error(502, str(exc)) from exc

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
            raise _inventory_error(500, str(exc)) from exc

    return {"speech": speech, "applied": applied, "unmatched": unmatched, "model": model}


def _get_row(df: pd.DataFrame, item_id: int) -> pd.Series:
    if item_id not in df.index:
        raise _inventory_error(404, f"item {item_id} not found")
    return df.loc[item_id]
