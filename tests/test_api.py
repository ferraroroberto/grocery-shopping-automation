"""FastAPI endpoint tests (TestClient) against the fixture data."""

from pathlib import Path

import app.api as api
from tests.conftest import stub_extract_result


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
    assert set(body) >= {"hub_ok", "whisper_ok", "hub_url", "whisper_url"}


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
