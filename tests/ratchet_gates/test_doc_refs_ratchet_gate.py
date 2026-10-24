"""The gate's doc-refs-ratchet stage maps the vendored script's exit codes.

`scripts/doc_refs_ratchet.py` exits 3 when the docs could not be scanned (git
unusable, a file that is not UTF-8, a fenced block or comment that never
closes): nothing was checked. These tests pin that 3 fails as NOT CONFIRMED --
never a pass, never a skip -- that the pre-ship gate calls the wrapper that
guarantees it rather than the script, and that the stage is enabled and
baselined so the script does not answer "not enabled". They run fake scripts in
a temp directory and never read live state.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import doc_refs_ratchet_gate as gate

from _ratchet_gate_contract import (
    assert_exit_code_contract, assert_gate_calls_wrapper, assert_run_maps_exit_code,
    assert_stage_declared,
)

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_classify_exit_follows_the_shared_ratchet_gate_contract() -> None:
    assert_exit_code_contract(gate.classify_exit)


@pytest.mark.parametrize("code", [0, 1, 2, 3])
def test_run_echoes_the_dangling_reference_and_fails_on_every_nonzero_exit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], code: int
) -> None:
    assert_run_maps_exit_code(gate, tmp_path, capsys, code=code, finding="README.md:3: path `src/gone.py` does not exist")


def test_the_pre_ship_gate_calls_the_doc_refs_wrapper() -> None:
    assert_gate_calls_wrapper(REPO_ROOT, "doc_refs")


def test_the_stage_is_enabled_baselined_and_its_script_vendored() -> None:
    assert_stage_declared(REPO_ROOT, "doc_refs")
