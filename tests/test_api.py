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


def test_index_shell_is_no_cache_and_hash_stamped(client):
    """The entry document must always revalidate, and its app.js URL must
    carry the build content hash so iOS Safari can't serve a stale module."""
    resp = client.get("/")
    assert resp.status_code == 200
    assert "Inventory Helper" in resp.text
    assert "no-cache" in resp.headers.get("cache-control", "")
    # Every asset URL is stamped with the fleet hash at serve time —
    # app.js and the vendored component CSS alike.
    expected = api.BUILD_INFO.asset_hashes["app.js"]
    assert f"/static/app.js?v={expected}" in resp.text
    assert f"/static/_vendored/nav/nav-tabs.css?v={expected}" in resp.text


def test_version_reports_build_identity(client):
    """/api/version feeds the PWA footer readout + stale-shell reload guard."""
    body = client.get("/api/version").json()
    assert set(body) == {"git_sha", "built_at", "asset_hash"}
    assert body["asset_hash"] == api.BUILD_INFO.fleet_hash


def test_static_js_is_long_cached(client):
    """Served .js assets carry an explicit immutable Cache-Control instead of
    being left to the browser's heuristic cache."""
    resp = client.get("/static/app.js")
    assert resp.status_code == 200
    cache_control = resp.headers.get("cache-control", "")
    assert "max-age=31536000" in cache_control
    assert "immutable" in cache_control


def test_auth_required_when_token_set(client):
    cfg = api.app.state.webapp_config
    cfg.auth_token = "secret"
    try:
        assert client.get("/api/inventory").status_code == 401
        ok = client.get("/api/inventory", headers={"Authorization": "Bearer secret"})
        assert ok.status_code == 200
    finally:
        cfg.auth_token = ""


def test_tunnel_traffic_still_requires_token_despite_loopback_client(temp_env):
    """Regression for #65: scripts/run_named_tunnel.py binds uvicorn to
    127.0.0.1 so cloudflared can reverse-proxy to it, which makes
    request.client.host "127.0.0.1" for every tunnel request regardless of
    the real internet client's IP. A request carrying the cf-ray header
    Cloudflare's edge stamps on tunnel traffic must not be waved through by
    the loopback exemption — it must still need a valid bearer token."""
    from fastapi.testclient import TestClient

    cfg = api.app.state.webapp_config
    orig_token = cfg.auth_token
    cfg.auth_token = "secret"
    try:
        with TestClient(api.app, client=("127.0.0.1", 51234)) as tunnel_client:
            no_token = tunnel_client.get("/api/inventory", headers={"cf-ray": "abc123-LHR"})
            assert no_token.status_code == 401

            wrong_token = tunnel_client.get(
                "/api/inventory",
                headers={"cf-ray": "abc123-LHR", "Authorization": "Bearer wrong"},
            )
            assert wrong_token.status_code == 401

            with_token = tunnel_client.get(
                "/api/inventory",
                headers={"cf-ray": "abc123-LHR", "Authorization": "Bearer secret"},
            )
            assert with_token.status_code == 200

            # A genuine same-machine loopback caller (no Cloudflare headers)
            # is still exempt — this only tightens tunnel traffic.
            genuine_loopback = tunnel_client.get("/api/inventory")
            assert genuine_loopback.status_code == 200
    finally:
        cfg.auth_token = orig_token


# --------------------------------------------------------------------------- #
# Routes formerly only exercised by the standalone tests/smoke_fastapi_pwa.py
# script (issue #95: folded into pytest so they share the client/temp_env
# fixtures instead of hand-rolling their own config-swap dance).
# --------------------------------------------------------------------------- #
def test_manifest_route(client):
    resp = client.get("/manifest.json")
    assert resp.status_code == 200
    assert resp.json()["display"] == "standalone"


def test_access_metadata_route(client):
    body = client.get("/api/access").json()
    assert body["local"].startswith("http")
    assert body["lan"].startswith("http")


def test_csv_export_route(client):
    payload = client.get("/api/inventory").json()
    resp = client.get("/api/export.csv")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    assert payload["columns"]["comida"] in resp.text


def test_item_quantity_delta_and_set_routes(client):
    payload = client.get("/api/inventory").json()
    cols = payload["columns"]
    first = payload["items"][0]
    item_id = first["id"]

    current_delta = client.post(f"/api/items/{item_id}/current-delta", json={"delta": 1})
    assert current_delta.status_code == 200
    changed = next(item for item in current_delta.json()["items"] if item["id"] == item_id)
    assert changed[cols["tenemos"]] == first[cols["tenemos"]] + 1

    target_delta = client.post(f"/api/items/{item_id}/target-delta", json={"delta": 1})
    assert target_delta.status_code == 200
    changed = next(item for item in target_delta.json()["items"] if item["id"] == item_id)
    assert changed[cols["cantidad"]] == first[cols["cantidad"]] + 1

    set_current = client.post(f"/api/items/{item_id}/current", json={"value": 0})
    assert set_current.status_code == 200
    changed = next(item for item in set_current.json()["items"] if item["id"] == item_id)
    assert changed[cols["tenemos"]] == 0


def test_item_edit_route(client):
    payload = client.get("/api/inventory").json()
    cols = payload["columns"]
    first = payload["items"][0]
    item_id = first["id"]
    edited_payload = {
        "super": first[cols["super"]],
        "lugar": first[cols["lugar"]],
        "comida": f"{first[cols['comida']]} smoke",
        "cantidad": first[cols["cantidad"]],
        "tenemos": first[cols["tenemos"]],
        "buscador": first[cols["buscador"]] or "",
    }
    resp = client.put(f"/api/items/{item_id}", json=edited_payload)
    assert resp.status_code == 200
    changed = next(item for item in resp.json()["items"] if item["id"] == item_id)
    assert changed[cols["comida"]].endswith(" smoke")


def test_item_add_and_delete_routes(client):
    payload = client.get("/api/inventory").json()
    cols = payload["columns"]
    first = payload["items"][0]
    new_payload = {
        "super": first[cols["super"]],
        "lugar": first[cols["lugar"]],
        "comida": "smoke test item",
        "cantidad": 2,
        "tenemos": 1,
        "buscador": "smoke test search",
    }
    added = client.post("/api/items", json=new_payload)
    assert added.status_code == 200
    added_item = next(item for item in added.json()["items"] if item[cols["comida"]] == "smoke test item")
    deleted = client.delete(f"/api/items/{added_item['id']}")
    assert deleted.status_code == 200
    assert all(item[cols["comida"]] != "smoke test item" for item in deleted.json()["items"])


def test_automation_status_route(client):
    resp = client.get("/api/automation/status")
    assert resp.status_code == 200
    assert resp.json()["running"] is False


def test_password_and_query_token_auth(client):
    cfg = api.app.state.webapp_config
    orig_token, orig_password = cfg.auth_token, cfg.auth_password
    cfg.auth_token = "test-token"
    cfg.auth_password = "test-password"
    try:
        locked = client.get("/api/inventory")
        assert locked.status_code == 401
        query_auth = client.get("/api/inventory?token=test-token")
        assert query_auth.status_code == 200
        login = client.post("/api/login", json={"password": "test-password"})
        assert login.status_code == 200
        assert login.json() == {"token": "test-token"}
    finally:
        cfg.auth_token = orig_token
        cfg.auth_password = orig_password
