"""UI-free network helpers shared by the FastAPI PWA and the Streamlit app.

Both front ends need to discover the LAN IP of this machine and probe whether a
local service (LLM hub / voice-transcriber) is reachable. The
logic is UI-agnostic, so it lives here and is imported from both `app/` halves
rather than being copy-pasted between them.
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse

import httpx


def local_ip(fallback: str = "127.0.0.1") -> str:
    """Return this machine's LAN IP via the Google-DNS UDP trick.

    Opens a UDP socket toward ``8.8.8.8:80`` (no packets are actually sent) and
    reads back the local address the OS picked for that route. Returns
    ``fallback`` if the lookup fails (e.g. no network).
    """
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.connect(("8.8.8.8", 80))
        ip = sock.getsockname()[0]
        sock.close()
        return ip
    except OSError:
        return fallback


def is_port_open(url: str, timeout: float = 1.5) -> bool:
    """TCP reachability probe for a service URL (hub / voice-transcriber)."""
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def whisper_route_ok(whisper_url: str, timeout: float = 2.0) -> bool:
    """Whether the hub's audio route answers at ``whisper_url``.

    An open port only proves something listens: the hub stays up when its
    whisper placement is down, and a stale per-machine whisper port can still pass a
    TCP check on some other process. The transcription route is POST-only,
    so a bare GET gets 405 when the route is live and 404 when it is not; a
    connection error or a 5xx is a failure too. Nothing is uploaded.
    """
    try:
        resp = httpx.get(f"{whisper_url.rstrip('/')}/v1/audio/transcriptions", timeout=timeout)
    except httpx.HTTPError:
        return False
    return resp.status_code != 404 and resp.status_code < 500


def whisper_host_hint(hub_base_url: str, whisper_model: str, timeout: float = 2.0) -> str | None:
    """Which host local-llm-hub currently has ``whisper_model`` running on, if any.

    Called only after a direct probe against whisper's own static port has
    already reported "unreachable": local-llm-hub can fail whisper over to a
    different physical host (e.g. a "gaming" satellite machine), and a bare
    port check only sees whichever host ``whisper_url`` happens to be
    configured for. This turns a bare "unreachable" into an actionable hint
    ("it's running on X instead") pointing at the hub's placement UI, without
    changing what gets probed or how transcription itself is routed (#107,
    #138) — best-effort only, so any failure (hub down, bad JSON, no match)
    returns ``None`` rather than raising.
    """
    try:
        resp = httpx.get(f"{hub_base_url.rstrip('/')}/admin/api/models", timeout=timeout)
        resp.raise_for_status()
        rows = resp.json().get("models", [])
        for row in rows:
            if row.get("display_name") == whisper_model:
                host = row.get("host")
                return host if isinstance(host, str) and host else None
        return None
    except Exception:  # noqa: BLE001 — a diagnostic hint must never break the health check
        return None
