"""Byte-level and launch-argument contract for the tray batch file."""

import re
from pathlib import Path

import pytest

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


@pytest.mark.parametrize("launch", ["launcher.py tray", "scripts\\launcher.py tray", "scripts/launcher.py tray"])
def test_tray_match_finds_a_tray_started_either_way(launch: str) -> None:
    """A tray still running from the old root ``launcher.py`` must be found and replaced."""
    bat = (ROOT / "tray.bat").read_text(encoding="utf-8")
    pattern = re.search(r'-TrayMatch "([^"]+)"', bat).group(1)
    assert pattern.startswith(r"(scripts[\\/]+)?launcher")
    assert re.search(pattern, f'"C:\\repo\\.venv\\Scripts\\pythonw.exe" {launch}')


def test_tray_launch_names_the_entrypoint_under_scripts() -> None:
    bat = (ROOT / "tray.bat").read_text(encoding="utf-8")
    script, command = re.search(r'set "TRAY_LAUNCH=([^"]+)"', bat).group(1).split()
    assert (script, command) == ("scripts\\launcher.py", "tray")
    assert (ROOT / "scripts" / "launcher.py").is_file()
