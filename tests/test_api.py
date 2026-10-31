"""FastAPI endpoint tests (TestClient) against the fixture data."""

import json
from pathlib import Path

import httpx

import app.api as api
import app.audio_hub as audio_hub
import app.routers.audio as audio_router
from tests.conftest import stub_extract_result


def _mock_vt(handler):
    """Return a drop-in for audio_hub.vt_client that routes through a
    MockTransport, so the voice-transcriber proxy can be exercised with no real
    VT host."""

    def factory(read_timeout: float | None = 600.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=audio_hub.voice_url(), transport=httpx.MockTransport(handler))

    return factory


def test_health(client):
    assert client.get("/api/health").json() == {"status": "ok"}


def test_inventory_audio_block(client):
    payload = client.get("/api/inventory").json()
    audio = payload["audio"]
    assert audio["default_model"] == "gemini_pro"
    assert "gemini_pro" in audio["models"]
    assert isinstance(audio["clamp"], int)


def test_audio_health_shape(client, monkeypatch):
    # The host hint asks the live hub where whisper runs (only when the hub is up and whisper is not);
    # stub it so the result never depends on the machine's hub.
    monkeypatch.setattr(audio_router, "whisper_host_hint", lambda *args, **kwargs: None)
    monkeypatch.setattr(audio_router, "whisper_route_ok", lambda *args, **kwargs: False)
    body = client.get("/api/audio/health").json()
    assert set(body) >= {
        "hub_ok", "whisper_ok", "voice_ok", "hub_url", "whisper_url", "voice_url", "whisper_host",
    }
    assert body["whisper_host"] is None


def test_whisper_url_defaults_to_the_hub():
    # The hub owns where whisper runs; a fresh install seeded from the example
    # config must transcribe through it with no edit.
    example = json.loads((Path(__file__).resolve().parent.parent / "src" / "config.example.json").read_text(encoding="utf-8"))
    cfg = example["audio_audit"]
    assert cfg["whisper_url"] == cfg["llm_base_url"] == "http://127.0.0.1:8000"
    assert "transcribe_url" not in cfg


def test_audio_health_whisper_ok_when_hub_audio_health_is_ok(client, monkeypatch):
    seen = []

    def fake_get(url, timeout):
        seen.append(url)
        return httpx.Response(200, json={"status": "ok", "backends": []}, request=httpx.Request("GET", url))

    monkeypatch.setattr("src.net.httpx.get", fake_get)
    monkeypatch.setattr(audio_router, "is_port_open", lambda *args, **kwargs: True)
    body = client.get("/api/audio/health").json()
    assert body["whisper_ok"] is True
    assert body["whisper_host"] is None
    assert seen == [body["whisper_url"].rstrip("/") + "/v1/audio/health"]


def test_audio_health_whisper_not_ok_when_not_the_hub(client, monkeypatch):
    monkeypatch.setattr(
        "src.net.httpx.get", lambda url, timeout: httpx.Response(404, request=httpx.Request("GET", url))
    )
    monkeypatch.setattr(audio_router, "is_port_open", lambda *args, **kwargs: True)
    monkeypatch.setattr(audio_router, "whisper_host_hint", lambda *args, **kwargs: "gaming")
    body = client.get("/api/audio/health").json()
    assert body["whisper_ok"] is False
    assert body["whisper_host"] == "gaming"


def test_audio_session_create_requests_incognito(client, monkeypatch):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["path"] = request.url.path
        seen["json"] = json.loads(request.content)
        return httpx.Response(200, json={"session_id": "sess-123", "folder": "x"})

    monkeypatch.setattr(audio_hub, "vt_client", _mock_vt(handler))
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

    monkeypatch.setattr(audio_hub, "vt_client", _mock_vt(handler))
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

    monkeypatch.setattr(audio_hub, "vt_client", _mock_vt(handler))
    resp = client.post("/api/audio/session/s1/finish")
    assert resp.status_code == 200
    assert resp.json()["transcript"] == "dos yogures"


def test_audio_session_retranscribe_returns_transcript(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/retranscribe")
        return httpx.Response(200, json={"transcript": "tres salmones", "language": "es"})

    monkeypatch.setattr(audio_hub, "vt_client", _mock_vt(handler))
    resp = client.post("/api/audio/session/s1/retranscribe")
    assert resp.status_code == 200
    assert resp.json()["transcript"] == "tres salmones"


def test_audio_session_unreachable_maps_to_502(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(audio_hub, "vt_client", _mock_vt(handler))
    resp = client.post("/api/audio/session")
    assert resp.status_code == 502


def test_audio_session_vt_404_propagates(client, monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "unknown session"})

    monkeypatch.setattr(audio_hub, "vt_client", _mock_vt(handler))
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

    monkeypatch.setattr(audio_hub, "vt_client", _mock_vt(handler))
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

    monkeypatch.setattr(audio_router, "extract", fake_extract)
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


def test_audio_last_reads_the_log_apply_wrote(client):
    """The Audit glance (#259): none before the first audit, then the one apply wrote."""
    assert client.get("/api/audio/last").json() == {"last": None}
    item_id = client.get("/api/inventory").json()["items"][0]["id"]
    client.post("/api/audio/apply", json={"updates": {str(item_id): 97}, "matches": {
        "items": [], "zones_mentioned": [], "unmatched_mentions": [{"phrase": "x", "idx": None}]}})
    last = client.get("/api/audio/last").json()["last"]
    assert last["changed"] == 1 and last["unmatched"] == 1 and last["timestamp"]


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


def test_tunnel_traffic_with_no_token_configured(temp_env):
    """With no auth_token set, a request that arrived through the Cloudflare
    tunnel gets only the exempt app shell; the API answers 401. Same-machine
    and LAN callers keep the open, token-less behaviour."""
    from fastapi.testclient import TestClient

    cfg = api.app.state.webapp_config
    orig_token = cfg.auth_token
    cfg.auth_token = ""
    try:
        with TestClient(api.app, client=("127.0.0.1", 51234)) as tunnel_client:
            for headers in ({"cf-ray": "abc123-LHR"}, {"cf-connecting-ip": "203.0.113.7"}):
                refused = tunnel_client.get("/api/inventory", headers=headers)
                assert refused.status_code == 401
                assert "no auth_token is configured" in refused.json()["detail"]
                assert tunnel_client.get("/api/automation/status", headers=headers).status_code == 401
                assert tunnel_client.get("/api/health", headers=headers).status_code == 200

            assert tunnel_client.get("/api/inventory").status_code == 200

        with TestClient(api.app, client=("192.168.1.20", 51234)) as lan_client:
            assert lan_client.get("/api/inventory").status_code == 200
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
    body = resp.json()
    assert body["display"] == "standalone"
    assert body["icons"] == [
        {
            "src": "/static/icon-192.png",
            "sizes": "192x192",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/static/icon-512.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "any",
        },
        {
            "src": "/static/icon-512-maskable.png",
            "sizes": "512x512",
            "type": "image/png",
            "purpose": "maskable",
        },
    ]


def test_index_references_generated_icon_family(client):
    resp = client.get("/")
    assert resp.status_code == 200
    assert 'rel="apple-touch-icon" href="/static/icon-180.png' in resp.text
    assert 'rel="icon" href="/static/favicon.ico" sizes="any"' in resp.text
    assert 'rel="icon" type="image/png" href="/static/icon-512.png"' in resp.text
    assert 'rel="manifest" href="/manifest.json"' in resp.text


def test_legacy_app_icon_route_is_removed(client):
    assert client.get("/app-icon.svg").status_code == 404


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


def test_stale_client_write_after_a_delete_is_refused(client):
    # Ids are row positions (#293): client B still holds the list from before
    # client A deleted row 2, so B's id 3 now points at what was row 4.
    snapshot = client.get("/api/inventory").json()
    cols = snapshot["columns"]
    stale = next(item for item in snapshot["items"] if item["id"] == 3)
    deleted = client.delete("/api/items/2", params={"name": snapshot["items"][2][cols["comida"]]})
    assert deleted.status_code == 200
    now = next(item for item in deleted.json()["items"] if item["id"] == 3)
    assert now[cols["comida"]] != stale[cols["comida"]]

    name = {"name": stale[cols["comida"]]}
    edit = {
        "super": stale[cols["super"]], "lugar": stale[cols["lugar"]], "comida": "overwritten",
        "cantidad": 9, "tenemos": 9, "buscador": "",
    }
    responses = [
        client.post("/api/items/3/current-delta", json={"delta": 1}, params=name),
        client.put("/api/items/3", json=edit, params=name),
        client.delete("/api/items/3", params=name),
    ]
    for resp in responses:
        assert resp.status_code == 409
        assert "reload" in resp.json()["detail"]
        assert resp.headers["x-inventory-stale"] == "1"
    after = next(item for item in client.get("/api/inventory").json()["items"] if item["id"] == 3)
    assert after == now


def test_item_write_with_a_matching_name_goes_through(client):
    payload = client.get("/api/inventory").json()
    first = payload["items"][0]
    name = first[payload["columns"]["comida"]]
    # The name key, not the exact text: case and spacing don't make a row stale.
    resp = client.post(f"/api/items/{first['id']}/current-delta", json={"delta": 1}, params={"name": f"  {name.upper()} "})
    assert resp.status_code == 200
    # Past the end is the same stale list, not a plain 404.
    gone = client.post(f"/api/items/{len(payload['items'])}/current-delta", json={"delta": 1}, params={"name": name})
    assert gone.status_code == 409


def _stale_batch(client, ids):
    """Client B's view of the list before client A deletes row 0 (#313): ids shift
    by one, so every id in `ids` now points at the next row down."""
    snapshot = client.get("/api/inventory").json()
    comida = snapshot["columns"]["comida"]
    names = {str(i): snapshot["items"][i][comida] for i in ids}
    deleted = client.delete("/api/items/0", params={"name": snapshot["items"][0][comida]})
    assert deleted.status_code == 200
    return names, deleted.json()


def test_stale_audio_apply_is_refused_whole(client):
    names, after_delete = _stale_batch(client, [2, 3])
    resp = client.post("/api/audio/apply", json={"updates": {"2": 7, "3": 7}, "names": names})
    assert resp.status_code == 409
    assert "reload" in resp.json()["detail"]
    assert resp.headers["x-inventory-stale"] == "1"
    # Nothing written: not the rows that moved, and no audit log either.
    assert client.get("/api/inventory").json()["items"] == after_delete["items"]
    assert client.get("/api/audio/last").json() == {"last": None}


def test_audio_apply_refuses_the_batch_when_only_one_row_moved(client):
    snapshot = client.get("/api/inventory").json()
    comida = snapshot["columns"]["comida"]
    names = {"1": snapshot["items"][1][comida], "2": "somebody else"}
    resp = client.post("/api/audio/apply", json={"updates": {"1": 7, "2": 7}, "names": names})
    assert resp.status_code == 409
    assert client.get("/api/inventory").json()["items"] == snapshot["items"]


def test_audio_apply_with_matching_names_goes_through(client):
    snapshot = client.get("/api/inventory").json()
    comida = snapshot["columns"]["comida"]
    # The item key, as for per-item writes: case and spacing don't make a row stale.
    names = {"1": f"  {snapshot['items'][1][comida].upper()} "}
    resp = client.post("/api/audio/apply", json={"updates": {"1": 7}, "names": names})
    assert resp.status_code == 200
    assert next(i for i in resp.json()["items"] if i["id"] == 1)[snapshot["columns"]["tenemos"]] == 7


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


def test_non_ascii_password_and_token_never_500(client):
    """Regression for #293: hmac.compare_digest raises TypeError on non-ASCII
    str operands, which turned a valid accented password (or a non-ASCII
    ?token=) into a 500 instead of a 200 / 401."""
    cfg = api.app.state.webapp_config
    orig_token, orig_password = cfg.auth_token, cfg.auth_password
    cfg.auth_token = "tokén"
    cfg.auth_password = "contraseña"
    try:
        good = client.post("/api/login", json={"password": "contraseña"})
        assert good.status_code == 200
        assert good.json() == {"token": "tokén"}

        bad = client.post("/api/login", json={"password": "contraseñb"})
        assert bad.status_code == 401

        wrong_query = client.get("/api/inventory", params={"token": "tóken"})
        assert wrong_query.status_code == 401

        right_query = client.get("/api/inventory", params={"token": "tokén"})
        assert right_query.status_code == 200
    finally:
        cfg.auth_token = orig_token
        cfg.auth_password = orig_password


def test_check_logins_route(client, monkeypatch):
    import app.login_check_runner as login_check_runner

    states = {"carrefour": {"state": "logged_in", "detail": ""}}
    monkeypatch.setattr(login_check_runner, "run", lambda: states)
    resp = client.post("/api/actions/check-logins")
    assert resp.status_code == 200
    assert resp.json() == {"stores": states}


def test_check_logins_route_reports_a_failed_check(client, monkeypatch):
    import app.login_check_runner as login_check_runner

    def boom():
        raise login_check_runner.LoginCheckError("login check exited 1 without a result")

    monkeypatch.setattr(login_check_runner, "run", boom)
    resp = client.post("/api/actions/check-logins")
    assert resp.status_code == 502
    assert "without a result" in resp.json()["detail"]


def test_automation_fills_names_stores_and_the_last_fill(client, temp_env):
    """#258: the carts glance gets display names + which stores Fill carts can
    fill, and no last fill until a live run has written a record."""
    body = client.get("/api/automation/fills").json()
    assert body["last_fill"] is None
    by_key = {s["key"]: s for s in body["stores"]}
    assert by_key["mercadona"]["name"] == "Mercadona" and by_key["mercadona"]["has_handler"] is True

    runs = temp_env / "purchase_logs" / "runs"
    runs.mkdir(parents=True)
    (runs / "2026-10-06T191701_mercadona.json").write_text(json.dumps({
        "run": "2026-10-06T191701", "store": "mercadona", "cart_mode": "keep",
        "items": [{"status": "added_verified"}, {"status": "out_of_stock"}],
    }), encoding="utf-8")
    last = client.get("/api/automation/fills").json()["last_fill"]
    assert last == {"run": "2026-10-06T191701", "date": "2026-10-06",
                    "stores": {"mercadona": {"in_cart": 1, "unconfirmed": 0, "missing": 1, "cart_mode": "keep"}}}


def _write_run(runs, stamp, store, statuses):
    (runs / f"{stamp}_{store}.json").write_text(json.dumps({
        "run": stamp, "store": store, "cart_mode": "keep",
        "items": [{"comida": f"item {n}", "status": status} for n, status in enumerate(statuses)],
    }), encoding="utf-8")


def test_automation_fill_history_and_mark_done(client, temp_env):
    """#323: History lists the fill days; Mark done records the latest fill's
    stamp, changes no run record, and a newer fill is not covered by it."""
    runs = temp_env / "purchase_logs" / "runs"
    runs.mkdir(parents=True)
    assert client.get("/api/automation/fills/history").json() == {"days": []}
    assert client.post("/api/automation/fills/clear", json={"run": "2026-10-06T191701"}).status_code == 404

    _write_run(runs, "2026-09-29T190000", "mercadona", ["added_verified"])
    _write_run(runs, "2026-10-06T191701", "mercadona", ["added_verified", "out_of_stock"])
    before = {p.name: p.read_bytes() for p in runs.iterdir()}

    fills = client.get("/api/automation/fills").json()
    assert fills["cleared"] is False and fills["last_fill"]["run"] == "2026-10-06T191701"
    days = client.get("/api/automation/fills/history").json()["days"]
    assert [(d["date"], d["cleared"]) for d in days] == [("2026-10-06", False), ("2026-09-29", False)]
    assert days[0]["stores"]["mercadona"]["problems"] == [{"item": "item 1", "status": "out_of_stock"}]

    assert client.post("/api/automation/fills/clear", json={"run": "2026-09-29T190000"}).status_code == 409
    for _ in range(2):  # the repeat is idempotent
        resp = client.post("/api/automation/fills/clear", json={"run": "2026-10-06T191701"})
        assert resp.status_code == 200 and resp.json() == {"cleared": True, "run": "2026-10-06T191701"}
    assert client.get("/api/automation/fills").json()["cleared"] is True
    days = client.get("/api/automation/fills/history").json()["days"]
    assert [(d["date"], d["cleared"]) for d in days] == [("2026-10-06", True), ("2026-09-29", False)]
    assert days[0]["stores"]["mercadona"]["missing"] == 1  # history still shows the cleared fill
    assert {p.name: p.read_bytes() for p in runs.iterdir()} == before

    # A newer fill un-clears the card; the old fill keeps its Done mark in History.
    _write_run(runs, "2026-10-13T180000", "mercadona", ["out_of_stock"])
    assert client.get("/api/automation/fills").json()["cleared"] is False
    days = client.get("/api/automation/fills/history").json()["days"]
    assert [(d["date"], d["cleared"]) for d in days][:2] == [("2026-10-13", False), ("2026-10-06", True)]
    assert client.post("/api/automation/fills/clear", json={"run": "2026-10-06T191701"}).status_code == 409


def test_entry_document_answers_a_repeat_with_304(client):
    """A phone that already holds the shell revalidates with its ETag and gets
    a body-less 304 instead of the whole document again."""
    first = client.get("/")
    etag = first.headers.get("etag", "")
    assert first.status_code == 200
    assert etag.startswith('W/"')
    assert "no-cache" in first.headers.get("cache-control", "")

    for validator in (etag, etag[2:], f'"other", {etag}', "*"):
        repeat = client.get("/", headers={"If-None-Match": validator})
        assert repeat.status_code == 304, validator
        assert repeat.content == b""
        assert repeat.headers.get("etag") == etag
        assert "no-cache" in repeat.headers.get("cache-control", "")

    stale = client.get("/", headers={"If-None-Match": 'W/"not-this-build"'})
    assert stale.status_code == 200


def test_entry_document_etag_follows_the_build(client, monkeypatch):
    """The stamped HTML names only direct assets, so the validator also keys on
    the commit and the fleet asset hash: a changed transitive module or a new
    commit must not be answered 304 against an old build."""
    base = client.get("/").headers["etag"]
    monkeypatch.setattr(api.BUILD_INFO, "git_sha", "feedbee")
    assert client.get("/").headers["etag"] != base
    monkeypatch.undo()
    monkeypatch.setattr(api.BUILD_INFO, "fleet_hash", "0" * 16)
    assert client.get("/").headers["etag"] != base
