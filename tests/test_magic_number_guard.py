"""Structural check for issue #341's finding class: a bare numeric literal
feeding a timeout/window/limit-shaped keyword argument in unattended code
(tray start, background pollers) with no name or reason attached to it.
Guards `app/tray/` and `automation/` -- the two surfaces the audit named.

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
    "automation/ametller.py:183:timeout",
    "automation/ametller.py:488:timeout",
    "automation/bootstrap_session.py:128:timeout",
    "automation/product_search.py:157:timeout",
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
