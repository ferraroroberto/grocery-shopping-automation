"""One cert lookup for the router, the tray manager and the tunnel launcher (#224)."""

from pathlib import Path

import app.routers.system as system
import app.tray.manager as manager
import scripts.run_named_tunnel as tunnel
from src.certs import cert_paths


def _write_pair(directory: Path, *, key: bool = True) -> Path:
    directory.mkdir(parents=True)
    (directory / "cert.pem").write_text("cert")
    if key:
        (directory / "key.pem").write_text("key")
    return directory


def test_webapp_certificates_win_over_repo_root(tmp_path):
    _write_pair(tmp_path / "webapp" / "certificates")
    _write_pair(tmp_path / "certificates")
    assert cert_paths(tmp_path) == (
        tmp_path / "webapp" / "certificates" / "cert.pem",
        tmp_path / "webapp" / "certificates" / "key.pem",
    )


def test_falls_back_to_repo_root_certificates(tmp_path):
    _write_pair(tmp_path / "certificates")
    assert cert_paths(tmp_path) == (tmp_path / "certificates" / "cert.pem", tmp_path / "certificates" / "key.pem")


def test_cert_without_key_is_not_https(tmp_path):
    """The router used to look at cert.pem alone and advertise https:// for a
    pair uvicorn could not serve."""
    _write_pair(tmp_path / "certificates", key=False)
    assert cert_paths(tmp_path) is None


def test_no_certificates_is_not_https(tmp_path):
    assert cert_paths(tmp_path) is None


def test_every_caller_shares_the_one_lookup():
    assert manager.cert_paths is cert_paths
    assert system.cert_paths is cert_paths
    assert tunnel.cert_paths is cert_paths


def test_access_urls_use_http_without_a_servable_pair(client, monkeypatch):
    monkeypatch.setattr(system, "cert_paths", lambda: None)
    assert client.get("/api/access").json()["local"].startswith("http://")
    monkeypatch.setattr(system, "cert_paths", lambda: (Path("cert.pem"), Path("key.pem")))
    assert client.get("/api/access").json()["local"].startswith("https://")
