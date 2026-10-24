"""Shared test fixtures for the `*_ratchet_gate` wrapper test files (static,
docs, comment). Factored out once a third gate-wrapper test file needed the
identical fake-script builder and exit-code contract check -- the ratchet
itself rejected the copy-paste.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable


def assert_exit_code_contract(classify_exit: Callable[[int], tuple[bool, str]]) -> None:
    """Every wrapper's `classify_exit` must agree: only 0 passes, and 3 is
    NOT CONFIRMED -- never a pass, never a skip."""
    assert classify_exit(0)[0] is True
    for code in (1, 2, 3, 4, -1):
        assert classify_exit(code)[0] is False, code
    ok, label = classify_exit(3)
    assert not ok
    assert "NOT CONFIRMED" in label
    assert "skip" not in label.lower()


def fake_ratchet_script(tmp_path: Path, code: int, message: str, *, filename: str) -> Path:
    """A throwaway script that just prints `message` and exits `code` --
    stands in for the vendored ratchet script without touching the real
    tree, git, npx or ruff."""
    script = tmp_path / filename
    script.write_text(f"import sys\nprint({message!r})\nsys.exit({code})\n", encoding="utf-8")
    return script


def assert_gate_calls_wrapper(repo_root: Path, name: str) -> None:
    """The pre-ship gate runs `<name>_ratchet` once, through its `-m` wrapper
    and never the vendored script directly (the script alone would let exit 3
    through)."""
    gate_ps1 = (repo_root / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    stages = [line for line in gate_ps1.splitlines() if line.lstrip().startswith("Invoke-Stage")]
    matching = [line for line in stages if f"{name}_ratchet" in line]
    assert len(matching) == 1
    assert f"-m scripts.{name}_ratchet_gate" in matching[0]
    assert f"{name}_ratchet.py" not in matching[0]
