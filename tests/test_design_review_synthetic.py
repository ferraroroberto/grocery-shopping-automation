"""The design-review demo instance (#251): invented data, no side effect, the real spreadsheet untouched.

Boots ``scripts/design_review_synthetic.py`` as the design-review walk does (a
subprocess that prints ``URL=`` and exits when its stdin closes) and drives every
route that could reach the outside world. A canary file stands in for the real
spreadsheet: it is declared protected, so a read *or* write of it is refused and
logged by the guard, and its bytes and mtime must come out unchanged. The guard
log (``<root>/guard.log``) must be empty: a stub that missed a side effect shows
up there as a refused process launch, connection or open.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import time
import tomllib
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Callable, Optional

import pytest

from scripts import synthetic_demo_data as demo_data
from scripts import synthetic_demo_guard as guard

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "scripts" / "design_review_synthetic.py"
# Household product names from the real list that must never appear in the demo (checked as substrings).
REAL_LIST_MARKERS = ("burguer", "ternera", "ametllerorigen", "hacendado", "mercadona.es", "carrefour.es", "niños")


def _call(base: str, method: str, path: str, body: Any = None, raw: Optional[bytes] = None, ctype: str = "") -> Any:
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    headers = {"Content-Type": ctype or "application/json"} if data is not None else {}
    req = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=60) as res:
            text = res.read().decode("utf-8")
            status = res.status
    except urllib.error.HTTPError as err:
        text, status = err.read().decode("utf-8"), err.code
    try:
        return status, json.loads(text)
    except ValueError:
        return status, text


def _wait_until(base: str, status_path: str, done: Callable[[dict], bool]) -> dict:
    for _ in range(100):
        _, body = _call(base, "GET", status_path)
        if done(body):
            return body
        time.sleep(0.1)
    raise AssertionError(f"{status_path} never finished")


def _digest(path: Path) -> tuple[str, float]:
    return hashlib.sha256(path.read_bytes()).hexdigest(), path.stat().st_mtime


@pytest.fixture(scope="module")
def instance(tmp_path_factory):
    canary = tmp_path_factory.mktemp("real") / "real-household-list.xlsx"
    canary.write_bytes(b"PK canary: stands in for the household's real spreadsheet " + uuid.uuid4().bytes)
    before = _digest(canary)
    env = {**os.environ, "PYTHONUTF8": "1", "SYNTHETIC_PROTECT_EXTRA": str(canary)}
    proc = subprocess.Popen([sys.executable, str(SCRIPT)], cwd=REPO_ROOT, env=env, stdin=subprocess.PIPE,
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    values: dict[str, str] = {}
    for line in proc.stdout:  # type: ignore[union-attr]
        key, _, value = line.strip().partition("=")
        values[key] = value
        if key == "URL":
            break
    else:
        proc.kill()
        raise AssertionError("no URL= line: " + proc.stderr.read())  # type: ignore[union-attr]
    root = instance_root = Path(values["ROOT"])
    yield SimpleNamespace(base=values["URL"], root=root, canary=canary, before=before, proc=proc)
    proc.stdin.close()  # type: ignore[union-attr]
    assert proc.wait(timeout=90) == 0
    assert not instance_root.exists(), "the launcher left its temp tree behind"


def _guard_log(instance) -> list[dict]:
    return list(guard.read_log(instance.root / "guard.log"))


def test_serves_the_invented_workbook(instance):
    status, body = _call(instance.base, "GET", "/api/inventory")
    assert status == 200
    names = [item["comida"] for item in body["items"]]
    assert names == [row[0] for row in demo_data.ROWS]
    assert len(set(item["lugar"] for item in body["items"])) >= 5          # every zone has content
    assert any(item["cantidad"] == 0 for item in body["items"])             # a zero-target row
    assert any(item["comprar"] == 0 for item in body["items"])              # a nothing-to-buy row
    assert body["summary"]["shopping_items"] > 0
    assert not [m for m in REAL_LIST_MARKERS if m in json.dumps(body, ensure_ascii=False).lower()]
    assert (instance.root / "data" / "list.xlsx").is_file()                 # the workbook it serves is in the temp tree
    assert _call(instance.base, "GET", "/")[0] == 200


def test_stores_tab_has_a_scored_run_and_baseline(instance):
    _, stores = _call(instance.base, "GET", "/api/stores")
    assert stores["run_date"] == demo_data.RUN_DATES[-1]
    status, recommended = _call(instance.base, "GET", "/api/stores/recommended")
    assert status == 200 and recommended["picks"]
    status, checks = _call(instance.base, "GET", "/api/stores/checks")
    assert status == 200
    status, detail = _call(instance.base, "GET", "/api/items/0/store-detail")
    assert status == 200 and not any(m in json.dumps(detail).lower() for m in REAL_LIST_MARKERS)
    status, sim = _call(instance.base, "POST", "/api/stores/simulate", {"picks": {}})
    assert status == 200


def test_every_side_effect_route_is_stubbed(instance):
    base = instance.base
    # Cart automation: dry and live, plus stop/reset. Output is the demo's, no process is spawned.
    for dry in (True, False):
        status, _ = _call(base, "POST", "/api/automation/start", {"store": "all", "dry_run": dry, "cart_mode": "keep"})
        assert status == 200
        done = _wait_until(base, "/api/automation/status", lambda b: not b["running"])
        assert done["returncode"] == 0 and all("[demo]" in line for line in done["lines"][:2])
        _call(base, "POST", "/api/automation/reset")
    _call(base, "POST", "/api/automation/stop")
    assert _call(base, "GET", "/api/automation/command")[0] == 200

    # Product search: the LLM parse is refused, the raw text is searched, the stores answer with invented cards.
    status, started = _call(base, "POST", "/api/product-search/start", {"text": "demo oat crackers"})
    assert status == 200
    done = _wait_until(base, "/api/product-search/status", lambda b: b["state"] != "running")
    assert done["state"] == "done" and done["items"][0]["candidates"]
    assert all(c["product_url"].startswith("https://demo.invalid/") for c in done["items"][0]["candidates"])
    assert _call(base, "POST", "/api/product-search/cancel")[0] == 200
    audio = b"RIFF" + b"\0" * 64
    boundary = "demo"
    multipart = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"a.wav\"\r\n"
                 f"Content-Type: audio/wav\r\n\r\n").encode() + audio + f"\r\n--{boundary}--\r\n".encode()
    ctype = f"multipart/form-data; boundary={boundary}"
    status, body = _call(base, "POST", "/api/product-search/transcribe", raw=multipart, ctype=ctype)
    assert status == 200 and body["transcript"]

    # Store logins, Chrome, the spreadsheet app, email.
    status, logins = _call(base, "POST", "/api/actions/check-logins")
    assert status == 200 and set(logins["stores"]) == {"mercadona", "ametller", "carrefour"}
    assert _call(base, "POST", "/api/actions/bootstrap-session")[0] == 200
    assert _call(base, "POST", "/api/actions/open-spreadsheet")[0] == 200
    assert _call(base, "GET", "/api/access")[0] == 200
    status, email = _call(base, "GET", "/api/email-monitor/status")
    assert status == 200 and email["senders"] and email["checks"]
    status, email = _call(base, "POST", "/api/email-monitor/check", {"force": True})
    assert status == 200 and "Demo check" in email["checks"][0]["outcome"]
    assert _call(base, "PUT", "/api/email-monitor/config",
                 {"enabled": False, "interval_minutes": 60, "senders": []})[0] == 200

    # Audio audit: the voice-transcriber proxy, whisper and the LLM hub are stubbed.
    status, health = _call(base, "GET", "/api/audio/health")
    assert status == 200 and health["hub_ok"] and health["whisper_ok"]
    status, session = _call(base, "POST", "/api/audio/session")
    assert status == 200 and session["session_id"] == "demo-session"
    sid = session["session_id"]
    assert _call(base, "POST", f"/api/audio/session/{sid}/chunk", raw=b"\0" * 16, ctype="audio/webm")[0] == 200
    status, events = _call(base, "GET", f"/api/audio/session/{sid}/events")
    assert status == 200 and "partial" in events
    status, finished = _call(base, "POST", f"/api/audio/session/{sid}/finish")
    assert status == 200 and finished["transcript"]
    assert _call(base, "POST", f"/api/audio/session/{sid}/retranscribe")[0] == 200
    assert _call(base, "POST", "/api/audio/transcribe", raw=multipart, ctype=ctype)[1]["transcript"]
    status, match = _call(base, "POST", "/api/audio/match", {"transcript": finished["transcript"]})
    assert status == 200 and match["items"]
    updates = {str(m["idx"]): m["count"] for m in match["items"]}
    assert _call(base, "POST", "/api/audio/apply", {"updates": updates})[0] == 200
    # The voice bridge's LLM parse is refused (a clean error), the query intent needs no LLM.
    assert _call(base, "POST", "/api/voice/command", {"intent": "add", "text": "two butter"})[0] == 502
    assert _call(base, "POST", "/api/voice/command", {"intent": "query"})[0] == 200

    # Edits land in the temp workbook, never the real one.
    assert _call(base, "POST", "/api/items/0/current-delta", {"delta": 1})[0] == 200
    assert _call(base, "POST", "/api/items", {"super": "mercadona", "lugar": "pantry", "comida": "test new item",
                                               "cantidad": 1, "tenemos": 0})[0] in (200, 201)

    assert _guard_log(instance) == [], "a side effect reached the guard: a stub is missing"


def test_the_real_spreadsheet_is_never_read_or_written(instance):
    assert _digest(instance.canary) == instance.before
    assert _guard_log(instance) == []
    xlsx = instance.root / "data" / "list.xlsx"
    assert xlsx.is_file() and xlsx != instance.canary


def _in_guarded_child(snippet: str, tmp_path: Path, protected: Path) -> subprocess.CompletedProcess:
    """Run ``snippet`` in a fresh interpreter with the guard installed (hooks cannot be removed in-process)."""
    root = tmp_path / "root"
    root.mkdir(exist_ok=True)
    env = {**os.environ, guard.ENV_ROOT: str(root), guard.ENV_PROTECT: str(protected),
           guard.ENV_LOG: str(root / "guard.log"), "PYTHONPATH": str(REPO_ROOT)}
    code = ("from scripts import synthetic_demo_guard as g\ng.install_from_env()\n" + snippet)
    return subprocess.run([sys.executable, "-c", code], env=env, capture_output=True, text=True, timeout=60)


@pytest.mark.parametrize("snippet", [
    "import subprocess; subprocess.Popen([r'cmd', '/c', 'echo', 'hi'])",
    "import os; os.startfile(r'.')" if sys.platform == "win32" else "import os; os.system('true')",
    "import socket; socket.create_connection(('203.0.113.9', 80), timeout=1)",
    "import socket; socket.create_connection(('127.0.0.1', 8000), timeout=1)",
    "import socket; socket.getaddrinfo('example.com', 80)",
    "import webbrowser; webbrowser.open('https://example.com')",
    "open(PROTECTED, 'rb')",
    "open(PROTECTED, 'wb')",
    "open(OUTSIDE, 'w')",
])
def test_guard_refuses(tmp_path, snippet):
    protected = tmp_path / "real.xlsx"
    protected.write_bytes(b"real")
    outside = tmp_path / "outside.txt"
    body = snippet.replace("PROTECTED", repr(str(protected))).replace("OUTSIDE", repr(str(outside)))
    result = _in_guarded_child(body, tmp_path, protected)
    assert result.returncode != 0 and "synthetic demo guard refused" in result.stderr, result.stderr
    assert protected.read_bytes() == b"real" and not outside.exists()
    assert len(list(guard.read_log(tmp_path / "root" / "guard.log"))) == 1


def test_guard_lets_the_instance_work_inside_its_tree(tmp_path):
    protected = tmp_path / "real.xlsx"
    snippet = ("import socket, pathlib\n"
               f"p = pathlib.Path({str(tmp_path / 'root' / 'ok.txt')!r}); p.write_text('x'); assert p.read_text() == 'x'\n"
               "s = socket.socket(); s.bind(('127.0.0.1', 0)); s.listen(); "
               "socket.create_connection(s.getsockname(), timeout=2).close()\n")
    result = _in_guarded_child(snippet, tmp_path, protected)
    assert result.returncode == 0, result.stderr


def test_decide_is_pure():
    policy = guard.Policy(root=Path("/tmp/demo-root").resolve(), protected=(Path("/real/data").resolve(),))
    assert guard.decide("subprocess.Popen", (), policy)
    assert guard.decide("socket.connect", (None, ("10.0.0.1", 80)), policy)
    assert guard.decide("socket.connect", (None, ("127.0.0.1", 8502)), policy)
    assert guard.decide("socket.connect", (None, ("127.0.0.1", 51234)), policy) is None
    assert guard.decide("socket.getaddrinfo", ("127.0.0.1", 80, 0, 0, 0), policy) is None
    assert guard.decide("open", (str(Path("/real/data/list.xlsx")), "rb", 0), policy)
    assert guard.decide("open", (str(Path("/elsewhere/x.txt")), "r", 0), policy) is None
    assert guard.decide("open", (str(Path("/elsewhere/x.txt")), "w", 0), policy)
    assert guard.decide("open", (str(Path("/tmp/demo-root/x.txt")), "w", 0), policy) is None


def test_fleet_toml_declares_the_instance_and_keeps_the_live_walk():
    config = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    synthetic = config["design"]["review"]["synthetic"]
    assert (REPO_ROOT / synthetic["command"][0]).is_file()
    live = config["design"]["review"]
    assert "#record-toggle" in live["no_go"] and "#automation-start" in live["no_go"]   # the live walk is unchanged
    steps = live["extra_steps"]
    assert any(s.get("synthetic") for s in steps) and any(not s.get("synthetic") for s in steps)
