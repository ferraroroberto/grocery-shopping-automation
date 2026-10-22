"""Provision a Tailscale HTTPS certificate for this machine.

Use this instead of (or to replace) src/gen_ssl_cert.py when you want browsers
to trust https://<host>.ts.net:8502 without any manual certificate
installation on any device.

Prerequisites:
  1. Enable HTTPS in the Tailscale admin console:
     https://login.tailscale.com/admin/dns  (scroll to "HTTPS Certificates")
  2. tailscale must be running and authenticated on this machine.

Usage:
    # Provision or force-renew:
    & .venv/Scripts/python.exe scripts/gen_tailscale_cert.py
    & .venv/Scripts/python.exe scripts/gen_tailscale_cert.py <host>.ts.net

    # Check and auto-renew if expiring within 30 days (called by webapp.bat on startup):
    & .venv/Scripts/python.exe scripts/gen_tailscale_cert.py --check
"""
from __future__ import annotations

import datetime
import json
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.no_window import NO_WINDOW  # noqa: E402

CERT_DIR = PROJECT_ROOT / "certificates"
RENEW_WITHIN_DAYS = 30


class CertReadError(Exception):
    """The cert file could not be loaded or parsed — expiry/SAN is unknown,
    distinct from a cert that loaded cleanly but isn't a Tailscale cert."""


def _tailscale_hostname() -> str:
    result = subprocess.run(
        ["tailscale", "status", "--json"],
        capture_output=True,
        text=True,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        raise SystemExit("tailscale status failed. Is tailscale running?")
    data = json.loads(result.stdout)
    name = data.get("Self", {}).get("DNSName", "").rstrip(".")
    if not name:
        raise SystemExit("Could not detect Tailscale hostname from 'tailscale status'.")
    return name


def _tailscale_hostname_from_cert(cert_path: Path) -> Optional[str]:
    """Return the .ts.net DNS SAN from the cert, or None if it loads cleanly
    but isn't a Tailscale cert (no SAN extension, or no .ts.net name in it).

    Raises `CertReadError` when the file can't be loaded or parsed — that
    state must not collapse into the same None a legitimate non-Tailscale
    cert returns.
    """
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise CertReadError(f"could not load {cert_path}: {exc}") from exc
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    except x509.ExtensionNotFound:
        return None
    for name in san.value.get_values_for_type(x509.DNSName):
        if ".ts.net" in name:
            return name
    return None


def _expiring_within(cert_path: Path, days: int) -> bool:
    """Return True if the cert expires within `days` days.

    Raises `CertReadError` when the file can't be loaded or parsed — the
    caller must report that as its own state, never as "not expiring".
    """
    from cryptography import x509
    try:
        cert = x509.load_pem_x509_certificate(cert_path.read_bytes())
    except (OSError, ValueError) as exc:
        raise CertReadError(f"could not load {cert_path}: {exc}") from exc
    try:
        expiry = cert.not_valid_after_utc
    except AttributeError:  # cryptography < 42
        expiry = cert.not_valid_after.replace(tzinfo=datetime.timezone.utc)
    threshold = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(days=days)
    return expiry < threshold


def _provision(hostname: str) -> None:
    CERT_DIR.mkdir(parents=True, exist_ok=True)
    cert_path = CERT_DIR / "cert.pem"
    key_path = CERT_DIR / "key.pem"

    result = subprocess.run(
        [
            "tailscale", "cert",
            "--cert-file", str(cert_path),
            "--key-file", str(key_path),
            hostname,
        ],
        capture_output=True,
        text=True,
        creationflags=NO_WINDOW,
    )
    if result.returncode != 0:
        msg = (result.stderr or result.stdout).strip()
        print(msg)
        raise SystemExit(
            "\ntailscale cert failed.\n"
            "Make sure HTTPS certificates are enabled in the Tailscale admin console:\n"
            "  https://login.tailscale.com/admin/dns"
        )

    print(f"[OK] cert.pem -> {cert_path}")
    print(f"[OK] key.pem  -> {key_path}")


def _check_and_renew() -> None:
    """Renew the cert if it is a Tailscale cert expiring within RENEW_WITHIN_DAYS days.
    Always exits cleanly — startup must not be blocked by cert errors."""
    cert_path = CERT_DIR / "cert.pem"
    if not cert_path.exists():
        return

    try:
        hostname = _tailscale_hostname_from_cert(cert_path)
    except CertReadError as exc:
        print(f"[WARN] Tailscale cert expiry could not be read — renewal not checked: {exc}")
        return
    if hostname is None:
        return  # self-signed cert; leave it alone

    try:
        expiring = _expiring_within(cert_path, RENEW_WITHIN_DAYS)
    except CertReadError as exc:
        print(f"[WARN] Tailscale cert expiry could not be read — renewal not checked: {exc}")
        return
    if not expiring:
        return

    print(f"[INFO] Tailscale cert for {hostname} expires within {RENEW_WITHIN_DAYS} days — renewing.")
    if shutil.which("tailscale") is None:
        print("[WARN] tailscale not found on PATH; skipping cert renewal.")
        return
    try:
        _provision(hostname)
        print("[OK] Tailscale cert renewed.")
    except SystemExit as exc:
        print(f"[WARN] Cert renewal failed: {exc}")


def main() -> None:
    args = sys.argv[1:]

    if args and args[0] == "--check":
        _check_and_renew()
        return

    if shutil.which("tailscale") is None:
        raise SystemExit("tailscale not found on PATH.")

    hostname = args[0] if args else _tailscale_hostname()
    print(f"Provisioning Tailscale cert for: {hostname}")
    _provision(hostname)
    print()
    print("Restart the app (webapp.bat), then open:")
    print(f"  https://{hostname}:8502")
    print()
    print("Note: https://localhost:8502 will show a cert hostname-mismatch warning")
    print("because this cert is issued only for the Tailscale domain.")
    print("Use http://localhost:8502 for plain local desktop access.")


if __name__ == "__main__":
    main()
