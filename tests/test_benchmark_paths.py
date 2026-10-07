"""``benchmark.runs_dir`` is resolved in one place, honoured by every benchmark reader/writer."""

from __future__ import annotations

from pathlib import Path

import pytest

from benchmark import paths
from src import data, store_links


def test_default_is_benchmark_runs_in_the_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(data.CONFIG, "benchmark", {})
    assert paths.runs_dir() == paths.REPO_ROOT / "benchmark_runs"
    assert paths.state_dir() == paths.runs_dir() / "_state"


def test_absolute_and_relative_overrides(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(data.CONFIG, "benchmark", {"runs_dir": str(tmp_path)})
    assert paths.runs_dir() == tmp_path
    assert paths.mappings_dir() == tmp_path / "_state" / "mappings"
    monkeypatch.setitem(data.CONFIG, "benchmark", {"runs_dir": "elsewhere"})
    assert paths.runs_dir() == (paths.REPO_ROOT / "elsewhere").resolve()


def test_store_links_and_the_cli_modules_agree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from benchmark import history

    monkeypatch.setitem(data.CONFIG, "benchmark", {"runs_dir": str(tmp_path)})
    assert store_links.runs_dir() == paths.runs_dir() == tmp_path
    assert history.history_path().parent == paths.state_dir()
