"""This repo's `.fleet.toml` `[e2e]` table: diff routing, the timing record and the budget.

The classifier's mechanism is tested where it is authored (project-scaffolding); this
pins what this repo declares, so an edit cannot silently under-route a browser surface or
leave `/e2e-audit` reading a timing file the gate no longer writes.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

from scripts.classify_e2e import classify, load_config

REPO_ROOT = Path(__file__).resolve().parents[2]


@pytest.mark.parametrize(
    ("path", "tier"),
    [
        ("app/api.py", "full"),
        ("app/webapp/static/styles.css", "full"),
        ("src/data.py", "full"),
        ("tests/e2e/_helpers.py", "full"),
        ("tests/e2e/test_home.py", "surface"),  # an edited test module runs only itself
        ("tests/conftest.py", "full"),
        ("tests/fixtures/list_test_fixture.xlsx", "full"),
        ("tests/test_api.py", "skip"),
        ("docs/testing.md", "skip"),
        ("README.md", "skip"),
        (".fleet.toml", "full"),  # unclassified: the table never judges its own change
        ("requirements.txt", "full"),
    ],
)
def test_declared_routing(path: str, tier: str) -> None:
    assert classify([path], load_config(REPO_ROOT / ".fleet.toml")).tier == tier


def test_the_gate_writes_the_declared_timing_record() -> None:
    e2e = tomllib.loads((REPO_ROOT / ".fleet.toml").read_text(encoding="utf-8"))["e2e"]
    gate = (REPO_ROOT / "scripts" / "verify-before-ship.ps1").read_text(encoding="utf-8")
    assert f"--junitxml={e2e['junit_xml']}" in gate
    assert e2e["test_budget"] > 0
