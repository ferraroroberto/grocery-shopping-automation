"""Unit tests for the tray webapp manager's tailnet-URL derivation.

``cert_hostname()`` / ``WebappManager.public_url`` read the ``.ts.net`` DNS SAN
off whatever cert is actually served, so the tray opens/copies a URL the
served cert is valid for — no hardcoded host, mirrors
``facilitation-suite``'s ``src/certs.py::cert_hostname`` (#see CLAUDE.md "no
hardcoded hosts").
"""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.tray.manager import (
    WebappManager,
    WebappManagerConfig,
    _renew_tailscale_cert,
    cert_hostname,
    load_config,
)


def _write_self_signed_cert(cert_dir: Path, san_names: list[str]) -> None:
    cert_dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san_names[0])])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now)
        .not_valid_after(now + datetime.timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName(n) for n in san_names]), critical=False)
        .sign(key, hashes.SHA256())
    )
    (cert_dir / "cert.pem").write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    (cert_dir / "key.pem").write_bytes(
        key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.TraditionalOpenSSL,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )


def test_cert_hostname_reads_the_ts_net_san(tmp_path: Path) -> None:
    _write_self_signed_cert(tmp_path / "certificates", ["tower.tail1121fd.ts.net"])
    assert cert_hostname(tmp_path) == "tower.tail1121fd.ts.net"


def test_cert_hostname_ignores_a_non_tailnet_san(tmp_path: Path) -> None:
    _write_self_signed_cert(tmp_path / "certificates", ["localhost"])
    assert cert_hostname(tmp_path) is None


def test_cert_hostname_none_when_no_cert(tmp_path: Path) -> None:
    assert cert_hostname(tmp_path) is None


def test_public_url_uses_the_tailnet_host_when_the_cert_names_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _write_self_signed_cert(tmp_path / "certificates", ["tower.tail1121fd.ts.net"])
    monkeypatch.setattr("app.tray.manager.PROJECT_ROOT", tmp_path)
    manager = WebappManager(WebappManagerConfig(host="0.0.0.0", port=8502))
    assert manager.public_url == "https://tower.tail1121fd.ts.net:8502"


def test_public_url_falls_back_to_the_loopback_url_without_a_tailnet_cert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.tray.manager.PROJECT_ROOT", tmp_path)
    manager = WebappManager(WebappManagerConfig(host="0.0.0.0", port=8502))
    assert manager.public_url == manager.base_url == "http://127.0.0.1:8502"


def test_load_config_follows_webapp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    cfg_path = tmp_path / "webapp_config.json"
    cfg_path.write_text('{"host": "127.0.0.1", "port": 9123}', encoding="utf-8")
    monkeypatch.setattr("src.webapp_config.DEFAULT_CONFIG_PATH", cfg_path)
    cfg = load_config()
    assert (cfg.host, cfg.port) == ("127.0.0.1", 9123)
    assert WebappManager(cfg).base_url == "http://127.0.0.1:9123"


def test_load_config_defaults_without_a_webapp_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("src.webapp_config.DEFAULT_CONFIG_PATH", tmp_path / "missing.json")
    cfg = load_config()
    assert (cfg.host, cfg.port) == ("0.0.0.0", 8502)


def test_renew_tailscale_cert_logs_a_nonzero_check_exit(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Regression for audit #340: the `--check` child's output and exit code
    used to be captured and discarded, so a failing renewal left no trace."""
    import app.tray.manager as manager_module

    class _FakeResult:
        returncode = 1
        stdout = ""
        stderr = "cert expiry could not be read"

    monkeypatch.setattr(manager_module.subprocess, "run", lambda *a, **k: _FakeResult())
    with caplog.at_level("WARNING", logger=manager_module.__name__):
        _renew_tailscale_cert()
    assert "exited 1" in caplog.text
    assert "cert expiry could not be read" in caplog.text


def test_renew_tailscale_cert_logs_a_clean_check_at_info_level(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import app.tray.manager as manager_module

    class _FakeResult:
        returncode = 0
        stdout = "Tailscale cert renewed."
        stderr = ""

    monkeypatch.setattr(manager_module.subprocess, "run", lambda *a, **k: _FakeResult())
    with caplog.at_level("INFO", logger=manager_module.__name__):
        _renew_tailscale_cert()
    assert "Tailscale cert renewed." in caplog.text
