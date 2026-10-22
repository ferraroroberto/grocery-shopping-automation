r"""The gate's docs-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.docs_ratchet_gate

`scripts/docs_ratchet.py` (vendored, never edited here) exits 0 on a pass (or
when `[docs_ratchet]` is not enabled), 1 when a README byte budget or a doc's
history-marker count rose, 2 on a bad table or baseline and 3 when it could not
confirm (git unusable, a non-UTF-8 file, an unclosed fence or HTML comment).
Exit 3 means nothing was checked, so it must fail the gate as its own state and
never read as a pass or a skip. This wrapper is the one place that mapping
lives, so a test can pin it; `verify-before-ship.ps1` calls the wrapper, never
the script.

Exit 0 only when the script exited 0; every other outcome exits 1 with its
reason on the last line.
"""

from __future__ import annotations

import sys
from pathlib import Path

from scripts.static_ratchet_gate import (
    EXIT_CONFIG, EXIT_NEW, EXIT_NOT_CONFIRMED, EXIT_PASS, REPO_ROOT, run_script,
)

SCRIPT = REPO_ROOT / "scripts" / "docs_ratchet.py"

# The script reads only `git ls-files` and the docs themselves, so a minute is
# generous; a hung child is reported as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_NEW: "a doc gained a history marker, or the README outgrew its budget",
    EXIT_CONFIG: "bad [docs_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- the docs could not be scanned, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "no new history marker in README.md or docs/, README within budget"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    code, output = run_script(script, RUN_TIMEOUT_S)
    if output:
        sys.stdout.write(output if output.endswith("\n") else output + "\n")
    ok, label = classify_exit(code)
    print(f"{'OK' if ok else 'FAIL'}: docs ratchet: {label}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(run())
