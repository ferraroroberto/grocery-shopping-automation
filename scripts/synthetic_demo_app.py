"""The webapp the design-review demo instance serves (#251): the real app with its side effects stubbed.

``uvicorn scripts.synthetic_demo_app:app`` runs from the instance's code copy
(``scripts/design_review_synthetic.py``), whose ``src/config.json`` already
points at a temp workbook and temp benchmark runs. This module does two more
things before the first request:

1. installs ``synthetic_demo_guard`` (refuses process launches, real-service
   connections, real-data opens and writes outside the temp tree), and
2. replaces every seam that would act on the outside world with an invented
   answer: the cart automation and product search subprocesses, the store
   login check and its Chrome window, "open the spreadsheet", the email
   poller (Gmail), the voice-transcriber proxy, whisper, and the LLM hub.

A stub never succeeds at anything real; the guard exists for the one a stub
forgot. Import order matters: the guard goes in before ``app.api`` loads.
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from typing import Any, Callable, List

from scripts import synthetic_demo_guard

synthetic_demo_guard.install_from_env()

import httpx  # noqa: E402

from src import static_versioning  # noqa: E402

# The build identity runs `git rev-parse` at import; the copy has no history and the guard refuses a process.
static_versioning._git_short_sha = lambda _repo_root: "demo"

from app import audio_hub, email_poller, login_check_runner, subprocess_plumbing  # noqa: E402
from app.api import app  # noqa: E402  (re-exported: uvicorn serves this)
from app.routers import audio, automation, product_search, system, voice  # noqa: E402
from src.inventory_extract import ExtractionError, ExtractionResult  # noqa: E402

DEMO_TRANSCRIPT = "in the fridge there are two butter blocks and one apple juice, in the freezer three salmon portions"
DEMO_LAN_IP = "192.0.2.10"        # TEST-NET-1: documentation range, never routable
STEP_DELAY_S = 0.15
AUTO_DISMISS_S = 8.0           # a finished demo cart run clears itself, so every fresh page load starts idle


# ── subprocess-backed runs (cart automation, product search) ────────────────

class _FakeProcess:
    """Just enough of ``subprocess.Popen`` for ``subprocess_plumbing``: it 'runs' while a thread emits lines."""

    def __init__(self) -> None:
        self.returncode: int | None = None
        self.pid = 0
        self._stopped = threading.Event()

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self._stopped.set()
        self.returncode = -15

    kill = terminate

    def wait(self, timeout: float | None = None) -> int | None:
        return self.returncode

    def finish(self, code: int = 0) -> None:
        if self.returncode is None:
            self.returncode = code


def _automation_lines(cmd: List[str]) -> List[str]:
    dry = "--dry-run" in cmd
    store = cmd[cmd.index("--store") + 1] if "--store" in cmd else "all stores"
    return [
        f"🛒 [demo] cart automation for {store} ({'dry run' if dry else 'live run'})",
        "ℹ️ [demo] this instance never opens a browser or touches a cart; the lines below are invented",
        "ℹ️ [demo] read 12 shopping-list items from the demo workbook",
        "✅ [demo] meadow butter x1 would be added",
        "✅ [demo] orchard apple juice x3 would be added",
        "⚠️ [demo] summit pasta: no product link, would be skipped",
        "✅ [demo] done: 2 added, 1 skipped",
    ]


def _search_lines(cmd: List[str]) -> List[str]:
    queries = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--query"]
    lines: List[str] = []
    results = []
    for query in queries:
        errors = {"carrefour": "demo: this store is not reachable from the demo instance"}
        candidates = []
        for rank, (store, price) in enumerate((("mercadona", 2.45), ("ametller", 3.1))):
            candidates.append({
                "store": store, "name": f"Demo {query} ({store})", "product_url": f"https://demo.invalid/{store}/{rank}/p",
                "price_text": f"{price:.2f} €".replace(".", ","), "price_eur": price, "thumbnail": "",
                "native_rank": rank, "score": 0.9 - rank * 0.2, "match": "strong" if rank == 0 else "partial",
            })
        lines.append(json.dumps({"event": "progress", "message": f"searching {query!r} (demo)"}))
        for store in ("mercadona", "ametller"):
            lines.append(json.dumps({"event": "store", "query": query, "store": store, "state": "done",
                                     "candidates": [c for c in candidates if c["store"] == store],
                                     "error": None, "reason": None, "elapsed_s": 0.1}))
        lines.append(json.dumps({"event": "store", "query": query, "store": "carrefour", "state": "failed",
                                 "candidates": [], "error": errors["carrefour"], "reason": "error", "elapsed_s": 0.1}))
        results.append({"query": query, "candidates": candidates, "errors": errors})
    lines.append(json.dumps({"event": "result", "result": {"results": results}}))
    return lines


def _spawn_and_drain(cmd: List[str], *, cwd: str, stderr: int, on_line: Callable[[str], None], bufsize: int = -1):
    process = _FakeProcess()
    lines = _search_lines(cmd) if "automation.product_search" in cmd else _automation_lines(cmd)

    def dismiss() -> None:
        if automation._AUTOMATION_RUN.get("process") is process:
            automation._AUTOMATION_RUN.clear()

    def emit() -> None:
        for line in lines:
            if process._stopped.wait(STEP_DELAY_S):
                return
            on_line(line + "\n")
        process.finish(0)
        if "automation.run_automation" in cmd:
            threading.Timer(AUTO_DISMISS_S, dismiss).start()

    thread = threading.Thread(target=emit, daemon=True)
    thread.start()
    return process, thread


# ── the other seams ─────────────────────────────────────────────────────────

def _login_check() -> dict:
    return {
        "mercadona": {"state": "logged_in", "detail": ""},
        "ametller": {"state": "logged_out", "detail": "demo: sign-in page shown"},
        "carrefour": {"state": "unknown", "detail": "demo: not checked"},
    }


def _run_email_checks(*, force: bool = False, trigger: str = "manual") -> list:
    entry = {
        "ts": datetime.now().isoformat(timespec="seconds"), "store": "mercadona", "trigger": trigger,
        "ok": True, "outcome": "Demo check: no mailbox is read in this instance", "notified": False,
    }
    email_poller._append_log([entry])
    email_poller._last_run_at = datetime.now()
    return [entry]


class _EventStream(httpx.AsyncByteStream):
    """A response body httpx has not already read, so the events route can stream it."""

    async def __aiter__(self):
        yield b'event: partial\ndata: {"text": "in the fridge there are two butter blocks"}\n\n'


def _voice_transport(request: httpx.Request) -> httpx.Response:
    path = request.url.path
    if path.endswith("/events"):
        return httpx.Response(200, stream=_EventStream(), headers={"content-type": "text/event-stream"})
    if path == "/api/sessions":
        return httpx.Response(200, json={"session_id": "demo-session"})
    if path.endswith(("/finish", "/retranscribe")):
        return httpx.Response(200, json={"transcript": DEMO_TRANSCRIPT, "language": "en", "silent": False})
    return httpx.Response(200, json={"ok": True})


def _vt_client(read_timeout: float | None = 600.0) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url="http://voice.demo.invalid", transport=httpx.MockTransport(_voice_transport))


async def _transcribe(file: Any, *, prompt: str | None = None) -> str:
    await file.read()
    return DEMO_TRANSCRIPT


def _extract(transcript: str, df: Any, **_: Any) -> ExtractionResult:
    """A fixed match over the first rows of the demo workbook — no LLM is called."""
    rows = list(df.index[:3])
    items = [{"idx": int(i), "count": 2 + n, "zone": str(df.loc[i, "lugar"]), "evidence": f"demo evidence {n + 1}"}
             for n, i in enumerate(rows)]
    return ExtractionResult(
        items=items, zones_mentioned=sorted({it["zone"] for it in items}),
        unmatched_mentions=[{"phrase": "something unclear", "idx": None, "approx_count": None, "note": "ambiguous",
                             "comida": "", "resolved_by": "", "match_score": None}],
        raw_text="{}",
    )


def _no_llm(*_: Any, **__: Any):
    raise ExtractionError("demo: the LLM hub is not used in this instance")


def install_stubs() -> None:
    subprocess_plumbing.spawn_and_drain = _spawn_and_drain
    login_check_runner.run = _login_check
    email_poller.run_checks = _run_email_checks
    email_poller.start_poller = lambda: None
    audio_hub.vt_client = _vt_client
    audio_hub.transcode_and_transcribe = _transcribe
    audio.extract = _extract
    audio.is_port_open = lambda *a, **k: True
    audio.whisper_host_hint = lambda *a, **k: None
    product_search.parse_voice_items = _no_llm
    voice.parse_voice_items = _no_llm
    system.launch_bootstrap_chrome = lambda: None
    system.open_spreadsheet_file = lambda: None
    system.local_ip = lambda *a, **k: DEMO_LAN_IP


install_stubs()
