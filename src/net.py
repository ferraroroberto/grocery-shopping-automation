"""UI-free network helpers shared by the FastAPI PWA and the Streamlit app.

Both front ends need to discover the LAN IP of this machine and probe whether a
local service (LLM hub / whisper-server / voice-transcriber) is reachable. The
logic is UI-agnostic, so it lives here and is imported from both `app/` halves
rather than being copy-pasted between them.
"""

from __future__ import annotations

import socket
from urllib.parse import urlparse


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
    """TCP reachability probe for a service URL (hub / whisper-server)."""
    parsed = urlparse(url)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False
