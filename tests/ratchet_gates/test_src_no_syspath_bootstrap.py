"""Structural check: modules in `src/` never edit `sys.path`.

Every `src/` module is reached as `import src.<module>` or `python -m src.<module>`,
and both forms already need the repo root importable, so a `sys.path.insert` bootstrap
cannot change behaviour -- it only forces `# noqa: E402` on the imports below it.
`src/gen_ssl_cert.py` is the one module the docs run as a plain script
(`python src\\gen_ssl_cert.py`), where the bootstrap is what makes `src` importable.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
ALLOWED = frozenset({"src/gen_ssl_cert.py"})
_PATH_MUTATORS = frozenset({"insert", "append", "extend"})


def find_sys_path_edits(source: str, relpath: str) -> list[str]:
    """`relpath:lineno` for every `sys.path.insert/append/extend(...)` call in `source`."""
    hits = []
    for node in ast.walk(ast.parse(source, filename=relpath)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        target = node.func.value
        if (
            node.func.attr in _PATH_MUTATORS
            and isinstance(target, ast.Attribute)
            and target.attr == "path"
            and isinstance(target.value, ast.Name)
            and target.value.id == "sys"
        ):
            hits.append(f"{relpath}:{node.lineno}")
    return hits


def test_detector_flags_each_mutating_call() -> None:
    source = "import sys\nsys.path.insert(0, 'x')\nsys.path.append('y')\nsys.path.index('z')\n"
    assert find_sys_path_edits(source, "m.py") == ["m.py:2", "m.py:3"]


def test_src_modules_do_not_edit_sys_path() -> None:
    offenses: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        relpath = path.relative_to(REPO_ROOT).as_posix()
        if relpath in ALLOWED:
            continue
        offenses += find_sys_path_edits(path.read_text(encoding="utf-8"), relpath)
    assert not offenses, f"sys.path edit in src/ (delete the bootstrap, run via `python -m`): {offenses}"
