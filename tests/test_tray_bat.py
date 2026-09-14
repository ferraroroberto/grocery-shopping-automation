"""Byte-level contract for the tray launcher batch file."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_tray_bat_is_ascii_only() -> None:
    """A non-ASCII byte anywhere in tray.bat can corrupt a non-interactive run (#141).

    The file opens with `chcp 65001`, switching the console codepage mid-file.
    cmd.exe read-ahead-buffers a chunk of the batch file at the codepage active
    when the buffer was filled, so a multi-byte UTF-8 character in that window
    (REM comments included) gets misparsed once the switch lands, throwing
    spurious `'X' is not recognized as an internal or external command` errors
    on unrelated lines. Mirrors project-scaffolding's
    `test_tray_template_is_ascii_only` (project-scaffolding#183).
    """
    raw = (ROOT / "tray.bat").read_bytes()
    offenders = [offset for offset, byte in enumerate(raw) if byte > 0x7F]
    assert not offenders, (
        f"tray.bat must be ASCII-only (#141); non-ASCII bytes at offsets {offenders}"
    )
