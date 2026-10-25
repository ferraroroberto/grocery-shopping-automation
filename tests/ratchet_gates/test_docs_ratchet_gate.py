"""The gate's docs-ratchet stage maps the vendored script's exit codes.

`scripts/docs_ratchet.py` exits 3 when the docs could not be scanned (git
unusable, a non-UTF-8 file, an unclosed fence): nothing was checked. These tests
pin that 3 fails as NOT CONFIRMED -- never a pass, never a skip -- that the
pre-ship gate calls the wrapper that guarantees it rather than the script, and
that the stage is declared so the script does not answer "not enabled". They run
fake scripts in a temp directory and never read live state.
"""

from __future__ import annotations

import subprocess
import tomllib
from pathlib import Path

import pytest

from scripts import docs_ratchet_gate as gate

from _ratchet_gate_contract import assert_exit_code_contract, fake_ratchet_script

REPO_ROOT = Path(__file__).resolve().parents[2]


def _fake_script(tmp_path: Path, code: int, message: str) -> Path:
    return fake_ratchet_script(tmp_path, code, message, filename="fake_docs_ratchet.py")


def test_classify_exit_follows_the_shared_ratchet_gate_contract() -> None:
    assert_exit_code_contract(gate.classify_exit)


@pytest.mark.parametrize(("code", "expected"), [(0, 0), (1, 1), (2, 1), (3, 1)])
def test_run_echoes_the_finding_and_fails_on_every_nonzero_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int, expected: int
) -> None:
    got = gate.run(_fake_script(tmp_path, code, "docs/x.md: 1 marker"))
    out = capsys.readouterr().out
    assert got == expected
    assert "docs/x.md: 1 marker" in out
    assert out.rstrip().splitlines()[-1].startswith("OK" if code == 0 else "FAIL")


def test_a_scan_that_hangs_or_cannot_start_is_not_confirmed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    script = _fake_script(tmp_path, 0, "ignored")
    for failure in (
        subprocess.TimeoutExpired(cmd="x", timeout=1),
        OSError("no interpreter"),
    ):
        def boom(*_a: object, _failure: Exception = failure, **_k: object) -> None:
            raise _failure

        monkeypatch.setattr("scripts.static_ratchet_gate.subprocess.run", boom)
        assert gate.run(script) == 1
        assert "NOT CONFIRMED" in capsys.readouterr().out


def test_the_pre_ship_gate_calls_the_wrapper_not_the_script() -> None:
    gate_ps1 = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    stages = [line for line in gate_ps1.splitlines() if line.lstrip().startswith("Invoke-Stage")]
    docs = [line for line in stages if "docs_ratchet" in line]
    assert len(docs) == 1
    assert "-m scripts.docs_ratchet_gate" in docs[0]
    assert "docs_ratchet.py" not in docs[0]
    # After the static ratchet stage, so a duplicate-block failure is reported first.
    assert stages.index(docs[0]) > stages.index(
        next(line for line in stages if "static_ratchet" in line)
    )


def test_the_stage_is_enabled_baselined_and_its_script_vendored() -> None:
    fleet = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))
    # An absent or disabled table makes the script print "not enabled" and exit 0.
    assert fleet["docs_ratchet"]["enabled"] is True
    assert (REPO_ROOT / fleet["docs_ratchet"]["baseline"]).is_file()
    entry = fleet["vendored"]["docs_ratchet"]
    assert entry["dest"] == "scripts/docs_ratchet.py"
    assert (REPO_ROOT / entry["dest"]).is_file()
