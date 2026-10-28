"""The app serves text through the vendored gzip middleware and keeps its streams live."""

from __future__ import annotations

import asyncio

import httpx

import app.audio_hub as audio_hub
import app.routers.automation as automation_router
from app.api import app

FIRST_CHUNK_WAIT_S = 3.0


def test_shell_and_script_are_gzipped_for_a_gzip_client(client):
    for path in ("/", "/static/app.js"):
        resp = client.get(path, headers={"Accept-Encoding": "gzip"})
        assert resp.status_code == 200, path
        assert resp.headers.get("content-encoding") == "gzip", path
        assert "accept-encoding" in resp.headers.get("vary", "").lower(), path


def test_identity_client_gets_plain_bytes(client):
    resp = client.get("/", headers={"Accept-Encoding": "identity"})
    assert resp.status_code == 200
    assert "content-encoding" not in resp.headers


async def _first_body_while_stream_is_open(path: str) -> tuple[dict, bytes]:
    """Drive the real ASGI app and return the first body chunk.

    The producer never finishes, so the chunk can only arrive if nothing in the
    stack (the gzip middleware included) holds it back until the stream ends.
    """
    scope = {
        "type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1", "method": "GET",
        "scheme": "http", "path": path, "raw_path": path.encode(), "query_string": b"",
        "root_path": "", "server": ("testserver", 80), "client": ("127.0.0.1", 5000),
        "headers": [(b"host", b"testserver"), (b"accept-encoding", b"gzip")],
    }
    disconnect = asyncio.Event()
    start: dict = {}
    first_body: asyncio.Future = asyncio.get_running_loop().create_future()

    async def receive():
        await disconnect.wait()
        return {"type": "http.disconnect"}

    async def send(message):
        if message["type"] == "http.response.start":
            start.update(message)
        elif message["type"] == "http.response.body" and message.get("body") and not first_body.done():
            first_body.set_result(message["body"])

    task = asyncio.create_task(app(scope, receive, send))
    try:
        body = await asyncio.wait_for(first_body, FIRST_CHUNK_WAIT_S)
    finally:
        disconnect.set()
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)
    return dict(start["headers"]), body


def test_audio_events_stream_delivers_first_chunk_at_once(temp_env, monkeypatch):
    async def never_ending():
        yield b"event: transcript\ndata: {}\n\n"
        await asyncio.sleep(3600)

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=never_ending())

    def factory(read_timeout: float | None = 600.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(base_url=audio_hub.voice_url(), transport=httpx.MockTransport(handler))

    monkeypatch.setattr(audio_hub, "vt_client", factory)
    headers, body = asyncio.run(_first_body_while_stream_is_open("/api/audio/session/s1/events"))
    assert body == b"event: transcript\ndata: {}\n\n"
    assert b"content-encoding" not in headers


def test_automation_events_stream_delivers_first_chunk_at_once(temp_env, monkeypatch):
    monkeypatch.setattr(
        automation_router, "automation_status", lambda: {"running": True, "lines": ["step 1"]},
    )
    headers, body = asyncio.run(_first_body_while_stream_is_open("/api/automation/events"))
    assert body.startswith(b"data: ") and b"step 1" in body
    assert b"content-encoding" not in headers
