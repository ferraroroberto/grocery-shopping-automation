"""Structural checks for two recurring audit classes. First, issue #341's finding class: a bare numeric literal
feeding a timeout/window/limit-shaped keyword argument in unattended code
(tray start, background pollers) with no name or reason attached to it.
Guards `app/tray/` and `automation/` -- the two surfaces the audit named.
Second, library code in `src/` configuring root logging at import time.

`BASELINE` holds pre-existing bare literals outside that scope (cart-
automation Playwright timeouts); it only stops *new* debt, same shrink-with-
`--update-baseline`-in-spirit convention as the vendored ratchets, minus the
CLI since this repo has exactly one baseline to carry.
"""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
UNATTENDED_DIRS = ("app/tray", "automation")
RISK_KEYWORD_SUBSTRINGS = ("timeout", "lookback", "limit", "interval", "retries", "backoff", "ttl")

# Pre-existing bare literals in cart-automation Playwright calls -- a
# different surface (synchronous, operator-watched runs) than the two
# nobody's-watching entrypoints issue #341 flagged. Shrink this set in the
# PR that names each one.
BASELINE = frozenset({
    "automation/bootstrap_session.py:128:timeout",
})


def _is_risk_keyword(name: str) -> bool:
    lowered = name.lower()
    return any(token in lowered for token in RISK_KEYWORD_SUBSTRINGS)


def find_bare_literal_offenses(source: str, relpath: str) -> list[str]:
    """Return `relpath:lineno:keyword` for every call keyword argument whose
    name looks like a timeout/window/limit/retry knob and whose value is a
    bare numeric literal rather than a named reference."""
    tree = ast.parse(source, filename=relpath)
    offenses = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if (
                kw.arg
                and _is_risk_keyword(kw.arg)
                and isinstance(kw.value, ast.Constant)
                and isinstance(kw.value.value, (int, float))
                and not isinstance(kw.value.value, bool)
            ):
                offenses.append(f"{relpath}:{kw.value.lineno}:{kw.arg}")
    return offenses


def scan_unattended_dirs() -> list[str]:
    offenses: list[str] = []
    for rel_dir in UNATTENDED_DIRS:
        for path in sorted((REPO_ROOT / rel_dir).rglob("*.py")):
            relpath = path.relative_to(REPO_ROOT).as_posix()
            offenses.extend(find_bare_literal_offenses(path.read_text(encoding="utf-8"), relpath))
    return offenses


def test_detector_flags_a_bare_timeout_literal():
    """Proves the detector itself would have caught issue #341's pre-fix
    `timeout=60` line -- a synthetic fixture of the exact pattern, not the
    live (now-fixed) file."""
    source = "subprocess.run(cmd, timeout=60)\n"
    assert find_bare_literal_offenses(source, "fixture.py") == ["fixture.py:1:timeout"]


def test_detector_flags_a_bare_lookback_days_literal():
    """Same proof for issue #341's other pre-fix line, `lookback_days=30`."""
    source = "mailbox.resolve_sources(senders=senders, lookback_days=30)\n"
    assert find_bare_literal_offenses(source, "fixture.py") == ["fixture.py:1:lookback_days"]


def test_detector_accepts_a_named_constant_reference():
    """The fix shape (a module constant in place of the literal) clears it."""
    source = "subprocess.run(cmd, timeout=_CERT_RENEW_TIMEOUT_S)\n"
    assert find_bare_literal_offenses(source, "fixture.py") == []


def test_no_new_bare_literal_timeouts_in_unattended_surfaces():
    """`app/tray/` (tray start) and `automation/`'s background-poller modules
    run with nobody watching; a new bare timeout/window/limit/retry literal
    there needs a named constant plus a one-clause reason instead."""
    offenses = set(scan_unattended_dirs())
    new = offenses - BASELINE
    assert not new, f"new unexplained literal(s) in unattended code: {sorted(new)}"


def find_root_logging_config(source: str, relpath: str) -> list[str]:
    """Return `relpath:lineno` for every `logging.basicConfig(...)` call."""
    tree = ast.parse(source, filename=relpath)
    return [
        f"{relpath}:{node.lineno}"
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "basicConfig"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logging"
    ]


def test_detector_flags_import_time_logging_config():
    source = "import logging\nlogging.basicConfig(level=10)\n"
    assert find_root_logging_config(source, "fixture.py") == ["fixture.py:2"]


def test_library_modules_never_configure_root_logging():
    """`src/` is imported by scripts and CLIs that set up their own logging; a
    `basicConfig` there wins the race at import and turns theirs into a no-op.
    Only entry points call `src.log_setup.configure_logging()`."""
    offenses: list[str] = []
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        relpath = path.relative_to(REPO_ROOT).as_posix()
        if relpath == "src/log_setup.py":
            continue  # the one place an entry point's setup call lands
        offenses.extend(find_root_logging_config(path.read_text(encoding="utf-8"), relpath))
    assert not offenses, f"root logging configured in library code: {offenses}"


BLOCKING_CALLS = frozenset(
    {("subprocess", name) for name in ("run", "call", "check_call", "check_output", "Popen")}
    | {("requests", name) for name in ("get", "post", "put", "patch", "delete", "request")}
    | {("time", "sleep")}
)


def find_blocking_calls_in_async(source: str, relpath: str) -> list[str]:
    """Return `relpath:lineno:call` for each blocking call made directly in an
    `async def` body. Nested `def`s and lambdas are skipped: those are the
    callables handed to `asyncio.to_thread`."""
    offenses: list[str] = []

    def visit(node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda)):
                continue
            if (
                isinstance(child, ast.Call)
                and isinstance(child.func, ast.Attribute)
                and isinstance(child.func.value, ast.Name)
                and (child.func.value.id, child.func.attr) in BLOCKING_CALLS
            ):
                offenses.append(f"{relpath}:{child.lineno}:{child.func.value.id}.{child.func.attr}")
            visit(child)

    for node in ast.walk(ast.parse(source, filename=relpath)):
        if isinstance(node, ast.AsyncFunctionDef):
            visit(node)
    return offenses


def test_detector_flags_a_blocking_call_in_an_async_def():
    source = "async def route():\n    subprocess.run(cmd)\n    await asyncio.to_thread(subprocess.run, cmd)\n"
    assert find_blocking_calls_in_async(source, "fixture.py") == ["fixture.py:2:subprocess.run"]


def test_detector_skips_a_nested_sync_def_handed_to_a_thread():
    source = "async def route():\n    def work():\n        time.sleep(1)\n    await asyncio.to_thread(work)\n"
    assert find_blocking_calls_in_async(source, "fixture.py") == []


def test_no_blocking_calls_directly_in_async_handlers():
    """A blocking call in an `async def` stalls every other request, SSE and
    `/healthz` included. This catches the direct form only; a blocking helper
    called from an async def (what the transcription route did) is covered by
    `tests/test_api.py::test_transcription_does_not_block_the_event_loop`."""
    offenses: list[str] = []
    for path in sorted((REPO_ROOT / "app").rglob("*.py")):
        relpath = path.relative_to(REPO_ROOT).as_posix()
        offenses.extend(find_blocking_calls_in_async(path.read_text(encoding="utf-8"), relpath))
    assert not offenses, f"blocking call(s) on the event loop: {offenses}"
