r"""The gate's structure-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.structure_ratchet_gate

`scripts/structure_ratchet.py` (vendored, never edited here) exits 0 on a pass
(or when `[structure_ratchet]` is not enabled), 1 when a file, function or
directory rose above `.structure-ratchet-baseline.json`, a `src` module imports
`app`, or the root layout breaks the declared shape, 2 on a bad table or
baseline and 3 when it could not confirm (git unusable, an unreadable file, a
Python file that does not parse, an unknown `shape`).

Shares its exit-3-is-NOT-CONFIRMED / exit-0-only-passes contract and the
echo-then-verdict shape with every other `*_ratchet_gate` wrapper via
`static_ratchet_gate.report()` -- see that module's docstring for the
full rationale.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts.static_ratchet_gate import (
    EXIT_CONFIG, EXIT_NEW, EXIT_NOT_CONFIRMED, EXIT_PASS, REPO_ROOT, report,
)

SCRIPT = REPO_ROOT / "scripts" / "structure_ratchet.py"

# The script reads `git ls-files` and parses every tracked source file; a
# minute is generous, and a hung child is reported as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_NEW: "a file, function or directory outgrew its baseline, or the layout broke its shape",
    EXIT_CONFIG: "bad [structure_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- the tree could not be measured, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no file, function or directory above baseline, layout matches its shape"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return report(script, "structure ratchet", classify_exit, RUN_TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(run())
