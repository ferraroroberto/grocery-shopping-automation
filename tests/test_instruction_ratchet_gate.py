"""The gate's instruction-ratchet stage maps the vendored script's exit codes.

`scripts/instruction_ratchet.py` exits 3 when the instruction files could not be
linted (git unusable, an unreadable file, an unclosed fence, an unreadable skill
description): nothing was checked. These tests pin that 3 fails as NOT CONFIRMED
-- never a pass, never a skip -- that the pre-ship gate calls the wrapper that
guarantees it rather than the script, and that the stage is enabled and
baselined so the script does not answer "not enabled". They run fake scripts in
a temp directory and never read live state.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts import instruction_ratchet_gate as gate

from _ratchet_gate_contract import (
    assert_exit_code_contract, assert_gate_calls_wrapper, fake_ratchet_script,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_classify_exit_follows_the_shared_ratchet_gate_contract() -> None:
    assert_exit_code_contract(gate.classify_exit)


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 1), (3, 1)])
def test_run_echoes_the_hit_and_fails_on_every_nonzero_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, expected: int
) -> None:
    script = fake_ratchet_script(tmp_path, code, "CLAUDE.md: R-03 x1", filename="fake_instruction.py")
    assert gate.run(script) == expected
    out = capsys.readouterr().out
    assert "CLAUDE.md: R-03 x1" in out
    assert out.rstrip().splitlines()[-1].startswith("OK" if code == 0 else "FAIL")


def test_the_pre_ship_gate_calls_the_instruction_wrapper() -> None:
    assert_gate_calls_wrapper(REPO_ROOT, "instruction")


def test_the_stage_is_enabled_baselined_and_its_script_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    # An absent or disabled table makes the script print "not enabled" and exit 0.
    assert fleet["instruction_ratchet"]["enabled"] is True
    assert (REPO_ROOT / fleet["instruction_ratchet"]["baseline"]).is_file()
    entry = fleet["vendored"]["instruction_ratchet"]
    assert entry["dest"] == "scripts/instruction_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
