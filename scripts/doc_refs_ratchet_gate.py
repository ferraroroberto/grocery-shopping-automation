r"""The gate's doc-refs-ratchet stage: run the vendored script, map its exit code.

    & .\.venv\Scripts\python.exe -m scripts.doc_refs_ratchet_gate

`scripts/doc_refs_ratchet.py` (vendored, never edited here) exits 0 on a pass
(or when `[doc_refs_ratchet]` is not enabled), 1 when `README.md`, `CLAUDE.md` or
`docs/**/*.md` names a repo path, module, relative link or loopback port that does
not exist beyond `.doc-refs-ratchet-baseline.json`, 2 on a bad table or baseline
and 3 when it could not confirm (git unusable, a file that is not UTF-8, a fenced
block or comment that never closes).

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

SCRIPT = REPO_ROOT / "scripts" / "doc_refs_ratchet.py"

# The script reads `git ls-files` and a handful of Markdown files; a minute is
# generous, and a hung child is reported as NOT CONFIRMED.
RUN_TIMEOUT_S = 120

_FAIL_LABELS = {
    EXIT_NEW: "a doc names a path, module, link or port that does not exist",
    EXIT_CONFIG: "bad [doc_refs_ratchet] table, baseline or arguments",
    EXIT_NOT_CONFIRMED: "NOT CONFIRMED -- the docs could not be scanned, so nothing was checked",
}


def classify_exit(code: int) -> tuple[bool, str]:
    """`(ok, label)` for one script exit code. Only 0 passes."""
    if code == EXIT_PASS:
        return True, "every path, module, link and port the docs name exists or is baselined"
    return False, _FAIL_LABELS.get(code, f"unexpected exit {code}")


def run(script: Path = SCRIPT) -> int:
    """Run `script`, echo its output, then the verdict. Returns the gate's exit code."""
    return report(script, "doc-refs ratchet", classify_exit, RUN_TIMEOUT_S)


if __name__ == "__main__":
    sys.exit(run())
