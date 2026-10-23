"""The gate's structure-ratchet stage maps the vendored script's exit codes.

`scripts/structure_ratchet.py` exits 3 when the tree could not be measured (git
unusable, an unreadable file, an unparseable Python file, an unknown `shape`):
nothing was checked. These tests pin that 3 fails as NOT CONFIRMED -- never a
pass, never a skip -- that the pre-ship gate calls the wrapper that guarantees
it rather than the script, and that the stage is declared with a shape so the
script neither answers "not enabled" nor skips the layout check. They run fake
scripts in a temp directory and never read live state.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts import structure_ratchet_gate as gate

from _ratchet_gate_contract import assert_exit_code_contract, fake_ratchet_script

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_classify_exit_follows_the_shared_ratchet_gate_contract() -> None:
    assert_exit_code_contract(gate.classify_exit)


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 1), (3, 1)])
def test_run_echoes_the_finding_and_fails_on_every_nonzero_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, expected: int
) -> None:
    script = fake_ratchet_script(tmp_path, code, "size:x.py: 1001", filename="fake_structure.py")
    got = gate.run(script)
    out = capsys.readouterr().out
    assert got == expected
    assert "size:x.py: 1001" in out
    assert out.rstrip().splitlines()[-1].startswith("OK" if code == 0 else "FAIL")


def test_the_pre_ship_gate_calls_the_wrapper_not_the_script() -> None:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    stages = [line for line in gate_ps1.splitlines() if line.lstrip().startswith("Invoke-Stage")]
    structure = [line for line in stages if "structure_ratchet" in line]
    assert len(structure) == 1
    assert "-m scripts.structure_ratchet_gate" in structure[0]
    assert "structure_ratchet.py" not in structure[0]


def test_the_stage_is_enabled_shaped_baselined_and_its_script_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    # An absent or disabled table makes the script print "not enabled" and exit 0.
    assert fleet["structure_ratchet"]["enabled"] is True
    assert (REPO_ROOT / fleet["structure_ratchet"]["baseline"]).is_file()
    # Without a shape the layout check is skipped, which is not a pass.
    assert fleet["shape"] == "webapp-tray"
    entry = fleet["vendored"]["structure_ratchet"]
    assert entry["dest"] == "scripts/structure_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
