"""Shared pytest fixtures for the grocery PWA test suite.

Every fixture points the data layer at a throwaway copy of the committed Excel
fixture and redirects the audio-audit logs to a temp dir, so tests never read or
write the real grocery spreadsheet or litter `audio_audit_logs/`.
"""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import src.data as data
from app.api import app
from src.inventory_extract import ExtractionResult

REPO_ROOT = Path(__file__).resolve().parent.parent
FIXTURE = REPO_ROOT / "tests" / "list_test_fixture.xlsx"


@pytest.fixture()
def temp_env(tmp_path: Path):
    """Redirect the data layer + audit logs at a throwaway temp dir."""
    xlsx = tmp_path / "inv.xlsx"
    shutil.copyfile(FIXTURE, xlsx)
    orig_xlsx = data.CONFIG["data"]["xlsx_file"]
    orig_logs = data.CONFIG["audio_audit"]["logs_dir"]
    data.CONFIG["data"]["xlsx_file"] = str(xlsx)
    data.CONFIG["audio_audit"]["logs_dir"] = str(tmp_path / "logs")
    try:
        yield tmp_path
    finally:
        data.CONFIG["data"]["xlsx_file"] = orig_xlsx
        data.CONFIG["audio_audit"]["logs_dir"] = orig_logs


@pytest.fixture()
def client(temp_env):
    """A TestClient with auth disabled, pointed at the fixture data."""
    cfg = app.state.webapp_config
    orig_token, orig_pw = cfg.auth_token, cfg.auth_password
    cfg.auth_token = ""
    cfg.auth_password = ""
    try:
        with TestClient(app) as test_client:
            yield test_client
    finally:
        cfg.auth_token = orig_token
        cfg.auth_password = orig_pw


def stub_extract_result() -> ExtractionResult:
    """A deterministic match result used to stub out the LLM hub in tests."""
    return ExtractionResult(
        items=[{"idx": 0, "count": 2, "zone": "nevera", "evidence": "dos yogures"}],
        zones_mentioned=["nevera"],
        unmatched_mentions=[{"phrase": "algo raro", "note": "ambiguous"}],
        raw_text="{}",
    )
