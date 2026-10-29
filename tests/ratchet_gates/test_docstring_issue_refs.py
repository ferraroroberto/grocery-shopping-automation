"""Guards `app/`/`src/` docstrings against growing `#NNN` issue-number
narratives.

`scripts/comment_ratchet.py` only scans `#` comments — docstrings are out of
scope there by design. That leaves a gap: a docstring citing an issue number
to tell the before/after story of a decision teaches the next edit to add
another one, with nothing in the gate to stop it. This test applies the same
ratchet shape locally, scoped to `app/` and `src/` where the drift was found:
existing debt is frozen in `.docstring-issue-refs-baseline.json` (never
hidden, never blocking work as-is) and a file's count may not rise above its
entry; a file with no entry may not carry any. Comments state the
present-tense why; decision history belongs in the issue and `git log`.
"""

from __future__ import annotations

import ast
import json
import re
import subprocess
from pathlib import Path

from src.no_window import NO_WINDOW

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
BASELINE_PATH = REPO_ROOT / ".docstring-issue-refs-baseline.json"
_SCOPE_PREFIXES = ("app/", "src/")
_REF_PATTERN = re.compile(r"#\d+")
_DOC_NODE_TYPES = (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)


def _tracked_python_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
        creationflags=NO_WINDOW,
    ).stdout.splitlines()
    return [f for f in out if f.startswith(_SCOPE_PREFIXES)]


def _docstring_ref_count(path: Path) -> int:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    total = 0
    for node in ast.walk(tree):
        if isinstance(node, _DOC_NODE_TYPES):
            doc = ast.get_docstring(node, clean=False)
            if doc:
                total += len(_REF_PATTERN.findall(doc))
    return total


def test_docstring_issue_refs_do_not_grow():
    baseline = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    grown, new = [], []
    for rel in _tracked_python_files():
        count = _docstring_ref_count(REPO_ROOT / rel)
        if count == 0:
            continue
        if rel not in baseline:
            new.append(f"{rel} ({count})")
        elif count > baseline[rel]:
            grown.append(f"{rel} ({count} > baseline {baseline[rel]})")
    assert not new, (
        f"new file(s) carry #NNN docstring issue-number references, not in "
        f"{BASELINE_PATH.name}: {new}"
    )
    assert not grown, (
        f"#NNN docstring issue-number references rose above baseline in: {grown}"
    )
