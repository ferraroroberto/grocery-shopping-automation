"""Generate a local CA + server certificate for HTTPS access.

Run once (or again whenever your PC's LAN IP changes):
    python gen_ssl_cert.py

Writes to certificates/:
  ca.pem   -- local CA certificate (installed into Windows trust store)
  cert.pem -- server certificate signed by the CA  (used by Streamlit)
  key.pem  -- server private key                   (used by Streamlit)

Chrome/Edge on this PC will show no security warning.
Mobile: tap 'Advanced -> Proceed' once per device.
"""
from __future__ import annotations

import datetime
import ipaddress
import platform
import socket
import subprocess
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

_NOW = lambda: datetime.datetime.now(datetime.timezone.utc)  # noqa: E731


def _local_ips() -> list[str]:
    """Return all non-loopback IPv4 addresses assigned to this machine."""
    ips: list[str] = []
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith("127."):
                ips.append(ip)
    except OSError:
        pass
    return ips or ["127.0.0.1"]


def generate(out_dir: Path) -> tuple[Path, Path, Path]:
    """Generate CA cert and server cert/key. Returns (ca_cert, server_cert, server_key)."""
    out_dir.mkdir(parents=True, exist_ok=True)

    local_ips = _local_ips()
    print(f"Detected IPs: {', '.join(local_ips)}")

    # --- Local CA ---
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Grocery App Local CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_NOW())
        .not_valid_after(_NOW() + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=False,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(ca_key, hashes.SHA256())
    )

    # --- Server cert signed by the CA ---
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "grocery-app-local")])

    san_entries: list[x509.GeneralName] = [
        x509.DNSName("localhost"),
        x509.IPAddress(ipaddress.IPv4Address("127.0.0.1")),
    ]
    for ip in local_ips:
        san_entries.append(x509.IPAddress(ipaddress.IPv4Address(ip)))

    server_cert = (
        x509.CertificateBuilder()
        .subject_name(server_name)
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(_NOW())
        .not_valid_after(_NOW() + datetime.timedelta(days=3650))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.SubjectAlternativeName(san_entries), critical=False)
        .sign(ca_key, hashes.SHA256())
    )

    ca_path = out_dir / "ca.pem"
    cert_path = out_dir / "cert.pem"
    key_path = out_dir / "key.pem"

    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(
        server_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.TraditionalOpenSSL,
            serialization.NoEncryption(),
        )
    )
    return ca_path, cert_path, key_path


def trust_on_windows(ca_path: Path) -> None:
    """Install CA cert into CurrentUser\\Root so Chrome/Edge trust it (no admin needed)."""
    print("Installing CA certificate into Windows trusted root store (CurrentUser)...")
    result = subprocess.run(
        [
            "powershell", "-NoProfile", "-Command",
            f'Import-Certificate -FilePath "{ca_path}" -CertStoreLocation Cert:\\CurrentUser\\Root',
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode == 0:
        print("[OK] CA trusted -- Chrome/Edge will show no security warning.")
    else:
        print(f"[WARN] Auto-install failed: {result.stderr.strip()}")
        print("       Manual fallback: double-click ca.pem -> Install Certificate")
        print("       -> Current User -> Place in: Trusted Root Certification Authorities")


if __name__ == "__main__":
    root = Path(__file__).resolve().parent.parent
    ca, cert, key = generate(root / "certificates")
    print(f"[OK] ca.pem   -> {ca}")
    print(f"[OK] cert.pem -> {cert}")
    print(f"[OK] key.pem  -> {key}")
    print()
    if platform.system() == "Windows":
        trust_on_windows(ca)
        print()
    print("Restart the app (launch_app.bat) -- it will now serve HTTPS.")
    print("Mobile: on first visit tap 'Advanced -> Proceed' once per device.")
