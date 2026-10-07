"""Where the HTTPS cert pair for the webapp lives.

One lookup shared by the API router (which URL scheme to advertise), the tray
manager and ``scripts/run_named_tunnel.py`` (whether to start uvicorn with TLS),
so they can't disagree about whether HTTPS is on. Mirrors ``webapp.bat``:
``webapp/certificates`` first, falling back to the repo-root ``certificates/``.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def cert_paths(project_root: Optional[Path] = None) -> Optional[tuple[Path, Path]]:
    """The ``(cert.pem, key.pem)`` pair uvicorn should serve, or ``None``.

    A directory counts only when it holds both files: a cert without its key
    can't be served, so it must not switch the app to HTTPS.
    """
    root = project_root or PROJECT_ROOT
    cert_dir = root / "webapp" / "certificates"
    cert, key = cert_dir / "cert.pem", cert_dir / "key.pem"
    if not cert.exists():
        cert_dir = root / "certificates"
        cert, key = cert_dir / "cert.pem", cert_dir / "key.pem"
    if cert.exists() and key.exists():
        return cert, key
    return None
