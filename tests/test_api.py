"""FastAPI endpoint tests (TestClient) against the fixture data."""

import json
from pathlib import Path

import httpx

import app.api as api
from tests.conftest import stub_extract_result


def _mock_vt(handler):
    """Return a drop-in for api._vt_client that routes through a MockTransport,
    so the voice-transcriber proxy can be exercised with no real VT host."""

    def factory(read_timeout: float | None = 600.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=api._voice_url(), transport=httpx.MockTransport(handler))

    return factory


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_inventory_audio_block(client):
    payload = client.get("/api/inventory").json()
    audio = payload["audio"]
    assert audio["default_model"] == "gemini_pro"
    assert "gemini_pro" in audio["models"]
    assert isinstance(audio["clamp"], int)


def test_audio_health_shape(client):
    body = client.get("/api/audio/health").json()
    assert set(body) >= {"hub_ok", "whisper_ok", "voice_ok", "hub_url", "whisper_url", "voice_url"}


def test_audio_session_create_requests_incognito(client, monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"session_id": "sess-123", "folder": "x"})

    monkeypatch.setattr(api, "_vt_client", _mock_vt(handler))
    resp = client.post("/api/audio/session")
    assert resp.status_code == 200
    assert resp.json() == {"session_id": "sess-123"}
    assert seen["path"] == "/api/sessions"
    assert seen["json"] == {"language": "es", "incognito": True}


def test_audio_session_chunk_forwards_bytes(client, monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content"] = request.content
        seen["ctype"] = request.headers.get("content-type")
        return httpx.Response(200, json={"session_id": "s1", "raw_bytes": 5})

    monkeypatch.setattr(api, "_vt_client", _mock_vt(handler))
    resp = client.post(
        "/api/audio/session/s1/chunk",
        content=b"\x00\x01\x02\x03\x04",
        headers={"Content-Type": "audio/webm"},
    )
    assert resp.status_code == 200
    assert resp.json()["raw_bytes"] == 5
    # The raw bytes and their content-type are forwarded verbatim.
    assert seen["content"] == b"\x00\x01\x02\x03\x04"
    assert seen["ctype"] == "audio/webm"


def test_audio_session_finish_returns_transcript(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/finish")
        assert request.url.params.get("language") == "es"
        assert request.url.params.get("translate") == "false"
        return httpx.Response(200, json={"transcript": "dos yogures", "language": "es"})

    monkeypatch.setattr(api, "_vt_client", _mock_vt(handler))
    resp = client.post("/api/audio/session/s1/finish")
    assert resp.status_code == 200
    assert resp.json()["transcript"] == "dos yogures"


def test_audio_session_retranscribe_returns_transcript(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/retranscribe")
        return httpx.Response(200, json={"transcript": "tres salmones", "language": "es"})

    monkeypatch.setattr(api, "_vt_client", _mock_vt(handler))
    resp = client.post("/api/audio/session/s1/retranscribe")
    assert resp.status_code == 200
    assert resp.json()["transcript"] == "tres salmones"


def test_audio_session_unreachable_maps_to_502(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(api, "_vt_client", _mock_vt(handler))
    resp = client.post("/api/audio/session")
    assert resp.status_code == 502


def test_audio_session_vt_404_propagates(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "unknown session"})

    monkeypatch.setattr(api, "_vt_client", _mock_vt(handler))
    resp = client.post("/api/audio/session/nope/finish")
    assert resp.status_code == 404


def test_audio_session_events_proxies_sse(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/events")

        async def stream():
            # An async iterator body makes the mock response streamable, so the
            # proxy's aiter_raw() consumes it exactly as it would a real VT SSE.
            yield b'event: partial\ndata: {"transcript":"hola"}\n\n'

        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=stream(),
        )

    monkeypatch.setattr(api, "_vt_client", _mock_vt(handler))
    with client.stream("GET", "/api/audio/session/s1/events") as resp:
        assert resp.status_code == 200
        assert "text/event-stream" in resp.headers["content-type"]
        body = b"".join(resp.iter_bytes())
    assert b"partial" in body and b"hola" in body


def test_audio_match_uses_stub_and_returns_context(client, monkeypatch):
    captured = {}

    def fake_extract(transcript, df, *, base_url, model, max_tokens, timeout):
        captured["model"] = model
        captured["timeout"] = timeout
        return stub_extract_result()

    monkeypatch.setattr(api, "extract", fake_extract)
    resp = client.post("/api/audio/match", json={"transcript": "dos yogures", "model": "claude_sonnet"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["model"] == "claude_sonnet"
    assert body["candidates"] > 0
    assert body["transcript_chars"] > 0
    # the generous timeout is actually passed through (regression guard)
    assert captured["timeout"] == 600
    assert captured["model"] == "claude_sonnet"


def test_audio_match_empty_transcript_400(client):
    assert client.post("/api/audio/match", json={"transcript": "   "}).status_code == 400


def test_audio_apply_writes_log(client, temp_env):
    payload = client.get("/api/inventory").json()
    item_id = payload["items"][0]["id"]
    resp = client.post(
        "/api/audio/apply",
        json={
            "updates": {str(item_id): 1},
            "transcript": "una unidad",
            "model": "gemini_pro",
            "matches": {"items": [{"idx": item_id, "count": 1}], "zones_mentioned": [], "unmatched_mentions": []},
        },
    )
    assert resp.status_code == 200
    log_path = resp.json()["audio_log_path"]
    assert log_path and Path(log_path).exists()


def test_automation_command_preview(client):
    body = client.get("/api/automation/command?store=mercadona&dry_run=false&cart_mode=clean").json()
    assert "run_automation" in body["command"]
    assert "--store mercadona" in body["command"]
    assert "--cart-mode clean" in body["command"]
    assert "--dry-run" not in body["command"]


def test_auth_required_when_token_set(client):
    cfg = api.app.state.webapp_config
    cfg.auth_token = "secret"
    try:
        assert client.get("/api/inventory").status_code == 401
        ok = client.get("/api/inventory", headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200
    finally:
        cfg.auth_token = ""
