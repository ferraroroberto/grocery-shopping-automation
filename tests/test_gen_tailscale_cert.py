"""Regression for audit #340: a cert that can't be loaded or parsed must
report that as its own state, never fold into "not expiring" / "not a
Tailscale cert"."""

from __future__ import annotations

import datetime
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

import scripts.gen_tailscale_cert as cert_script


def _write_cert(cert_dir: Path, *san_names: str) -> Path:
    cert_dir.mkdir(parents=True, exist_ok=True)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = issuer = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, san_names[0])])
    not_before = datetime.datetime.now(datetime.timezone.utc)
    not_after = not_before + datetime.timedelta(days=90)
    builder = x509.CertificateBuilder().subject_name(subject).issuer_name(issuer)
    builder = builder.public_key(key.public_key()).serial_number(x509.random_serial_number())
    builder = builder.not_valid_before(not_before).not_valid_after(not_after)
    builder = builder.add_extension(
        x509.SubjectAlternativeName([x509.DNSName(n) for n in san_names]), critical=False,
    )
    cert_path = cert_dir / "cert.pem"
    cert_path.write_bytes(builder.sign(key, hashes.SHA256()).public_bytes(serialization.Encoding.PEM))
    return cert_path


def test_hostname_from_cert_reads_the_ts_net_san(tmp_path: Path) -> None:
    cert_path = _write_cert(tmp_path, "tower.tail1121fd.ts.net")
    assert cert_script._tailscale_hostname_from_cert(cert_path) == "tower.tail1121fd.ts.net"


def test_hostname_from_cert_none_for_a_non_tailnet_cert(tmp_path: Path) -> None:
    cert_path = _write_cert(tmp_path, "localhost")
    assert cert_script._tailscale_hostname_from_cert(cert_path) is None


def test_hostname_from_cert_raises_on_an_unparseable_file(tmp_path: Path) -> None:
    """Before the fix, `except Exception: pass` folded this into the same
    None a legitimate non-Tailscale cert returns."""
    cert_path = tmp_path / "cert.pem"
    cert_path.write_bytes(b"not a certificate")
    with pytest.raises(cert_script.CertReadError):
        cert_script._tailscale_hostname_from_cert(cert_path)


def test_expiring_within_raises_on_an_unparseable_file(tmp_path: Path) -> None:
    """Before the fix, `except Exception: return False` reported an unreadable
    cert as "not expiring", silently skipping renewal."""
    cert_path = tmp_path / "cert.pem"
    cert_path.write_bytes(b"not a certificate")
    with pytest.raises(cert_script.CertReadError):
        cert_script._expiring_within(cert_path, 30)


def test_check_and_renew_reports_an_unreadable_cert_instead_of_going_silent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(cert_script, "CERT_DIR", tmp_path)
    (tmp_path / "cert.pem").write_bytes(b"not a certificate")
    cert_script._check_and_renew()
    out = capsys.readouterr().out
    assert "could not be read" in out
    assert "renewal not checked" in out
