r"""The gate's dead-code-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.dead_code_ratchet_gate

`scripts/dead_code_ratchet.py` (vendored, never edited here) exits 0 on a pass
(or when `[dead_code_ratchet]` is not enabled), 1 when a Python function, class or
method that nothing references is not in `.dead-code-ratchet-baseline.json`, 2 on
a bad table or baseline and 3 when it could not confirm (git unusable, a tracked
Python file that cannot be read or does not parse).

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

SCRIPT = REPO_ROOT / "scripts" / "dead_code_ratchet.py"

# The script parses every tracked Python file with `ast`; a minute is generous,
# and a hung child is reported as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_NEW: "a function, class or method nothing references is not in the baseline",
    EXIT_CONFIG: "bad [dead_code_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- the Python tree could not be parsed, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "every function, class and method is referenced or baselined"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return report(script, "dead-code ratchet", classify_exit, RUN_TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(run())
