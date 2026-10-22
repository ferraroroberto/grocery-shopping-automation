r"""The gate's comment-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.comment_ratchet_gate

`scripts/comment_ratchet.py` (vendored, never edited here) exits 0 on a pass,
1 when a file's count of `#NNN` issue references inside comments rises above
.comment-ratchet-baseline.json (or a new file carries references, or the
baseline is missing), 2 on a bad table, baseline or arguments, and 3 when it
could not confirm (git unusable, a file could not be scanned).

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

SCRIPT = REPO_ROOT / "scripts" / "comment_ratchet.py"

# The script reads git ls-files plus the product-code text itself; a minute
# is generous, and a hung child is reported as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_NEW: "a file's #NNN comment-reference count rose, or the baseline is missing",
    EXIT_CONFIG: "bad [comment_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- a file could not be scanned, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no file's #NNN comment-reference count rose above baseline"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return report(script, "comment ratchet", classify_exit, RUN_TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(run())
