"""The gate's comment-ratchet stage maps the vendored script's exit codes
(audit #340, claude-md-drift).

`scripts/comment_ratchet.py` exits 3 when a file could not be scanned: nothing
was checked. These tests pin that 3 fails as NOT CONFIRMED -- never a pass,
never a skip -- that the pre-ship gate calls the wrapper that guarantees it
rather than the script, and that the stage is declared so the script does not
answer "not enabled". They run fake scripts in a temp directory and never
read live state.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts import comment_ratchet_gate as gate
from scripts.static_ratchet_gate import subprocess as ratchet_subprocess

from _ratchet_gate_contract import assert_exit_code_contract, fake_ratchet_script

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_classify_exit_follows_the_shared_ratchet_gate_contract() -> None:
    assert_exit_code_contract(gate.classify_exit)


@pytest.mark.parametrize(("code", "expected_exit"), [(0, 0), (1, 1), (2, 1), (3, 1)])
def test_run_maps_every_script_exit_onto_the_gate_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, expected_exit: int
) -> None:
    finding = "scripts/x.py: 3 reference(s)"
    script = fake_ratchet_script(tmp_path, code, finding, filename="fake_comment_ratchet.py")
    assert gate.run(script) == expected_exit
    report_lines = capsys.readouterr().out.rstrip().splitlines()
    assert finding in report_lines[0]
    assert report_lines[-1].startswith("OK") == (code == 0)


def test_an_unstartable_or_hanging_script_is_not_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    quiet_script = fake_ratchet_script(tmp_path, 0, "unused", filename="fake_comment_ratchet.py")
    bad_calls = (
        lambda *_a, **_k: (_ for _ in ()).throw(ratchet_subprocess.TimeoutExpired(cmd="x", timeout=1)),
        lambda *_a, **_k: (_ for _ in ()).throw(OSError("no interpreter")),
    )
    for broken_run in bad_calls:
        monkeypatch.setattr(ratchet_subprocess, "run", broken_run)
        gate_exit = gate.run(quiet_script)
        assert gate_exit == 1
        assert "NOT CONFIRMED" in capsys.readouterr().out


def test_the_pre_ship_gate_calls_the_wrapper_not_the_script() -> None:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    stages = [line for line in gate_ps1.splitlines() if line.lstrip().startswith("Invoke-Stage")]
    comment = [line for line in stages if "comment_ratchet" in line]
    assert len(comment) == 1
    assert "-m scripts.comment_ratchet_gate" in comment[0]
    assert "comment_ratchet.py" not in comment[0]


def test_the_stage_is_enabled_baselined_and_its_script_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    assert fleet["comment_ratchet"]["enabled"] is True
    assert (REPO_ROOT / fleet["comment_ratchet"]["baseline"]).is_file()
    entry = fleet["vendored"]["comment_ratchet"]
    assert entry["dest"] == "scripts/comment_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
