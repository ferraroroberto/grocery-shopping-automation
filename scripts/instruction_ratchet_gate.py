r"""The gate's instruction-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.instruction_ratchet_gate

`scripts/instruction_ratchet.py` (vendored, never edited here) exits 0 on a pass
(or when `[instruction_ratchet]` is not enabled), 1 when a rule count rose above
`.instruction-ratchet-baseline.json` or an instruction file passed its size cap,
2 on a bad table or baseline and 3 when it could not confirm (git unusable, an
unreadable or non-UTF-8 file, a fenced block that never closes, a skill whose
description cannot be read).

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

SCRIPT = REPO_ROOT / "scripts" / "instruction_ratchet.py"

# The script reads `git ls-files` and lints a handful of instruction files; a
# minute is generous, and a hung child is reported as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_NEW: "an instruction rule count rose above its baseline, or a file passed its size cap",
    EXIT_CONFIG: "bad [instruction_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- the instruction files could not be linted, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no instruction rule count above baseline, no file over its size cap"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return report(script, "instruction ratchet", classify_exit, RUN_TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(run())
