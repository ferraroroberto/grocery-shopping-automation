"""Unit tests for src.net's whisper host-hint and audio-health helpers."""

import httpx

from src.net import whisper_host_hint, whisper_route_ok


def _response(status_code: int, **kwargs) -> httpx.Response:
    # httpx.Response.raise_for_status() requires `.request` to be set (even on
    # a 2xx) — real responses from httpx.get() always carry it; a bare
    # constructed Response doesn't unless given one here.
    return httpx.Response(status_code, request=httpx.Request("GET", "http://hub:8000/admin/api/models"), **kwargs)


def test_returns_host_on_match(monkeypatch):
    def fake_get(url, timeout):
        assert url == "http://hub:8000/admin/api/models"
        return _response(200, json={"models": [
            {"id": "whisper", "display_name": "whisper-large-v3-turbo", "host": "gaming"},
        ]})

    monkeypatch.setattr("src.net.httpx.get", fake_get)
    assert whisper_host_hint("http://hub:8000", "whisper-large-v3-turbo") == "gaming"


def test_returns_none_when_no_match(monkeypatch):
    def fake_get(url, timeout):
        return _response(200, json={"models": [
            {"id": "parakeet", "display_name": "parakeet-tdt-0.6b-v3", "host": "mac-mini-m4"},
        ]})

    monkeypatch.setattr("src.net.httpx.get", fake_get)
    assert whisper_host_hint("http://hub:8000", "whisper-large-v3-turbo") is None


def test_returns_none_on_connection_error(monkeypatch):
    def fake_get(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("src.net.httpx.get", fake_get)
    assert whisper_host_hint("http://hub:8000", "whisper-large-v3-turbo") is None


def test_returns_none_on_http_error(monkeypatch):
    def fake_get(url, timeout):
        return _response(500)

    monkeypatch.setattr("src.net.httpx.get", fake_get)
    assert whisper_host_hint("http://hub:8000", "whisper-large-v3-turbo") is None


def test_returns_none_on_bad_json(monkeypatch):
    def fake_get(url, timeout):
        return _response(200, content=b"not json")

    monkeypatch.setattr("src.net.httpx.get", fake_get)
    assert whisper_host_hint("http://hub:8000", "whisper-large-v3-turbo") is None


def _route_get(status_code: int, **kwargs):
    def fake_get(url, timeout):
        assert url == "http://hub:8000/v1/audio/health"
        return _response(status_code, **kwargs)

    return fake_get


def test_route_ok_when_hub_audio_health_is_ok(monkeypatch):
    monkeypatch.setattr("src.net.httpx.get", _route_get(200, json={"status": "ok", "backends": []}))
    assert whisper_route_ok("http://hub:8000/") is True


def test_route_not_ok_when_not_the_hub_or_backend_failing(monkeypatch):
    for status in (404, 502, 503):
        monkeypatch.setattr("src.net.httpx.get", _route_get(status))
        assert whisper_route_ok("http://hub:8000") is False


def test_route_not_ok_when_health_reply_is_not_ok(monkeypatch):
    for kwargs in ({"json": {"status": "degraded"}}, {"json": ["ok"]}, {"content": b"not json"}):
        monkeypatch.setattr("src.net.httpx.get", _route_get(200, **kwargs))
        assert whisper_route_ok("http://hub:8000") is False


def test_route_not_ok_when_nothing_listens(monkeypatch):
    def fake_get(url, timeout):
        raise httpx.ConnectError("refused")

    monkeypatch.setattr("src.net.httpx.get", fake_get)
    assert whisper_route_ok("http://hub:8000") is False
