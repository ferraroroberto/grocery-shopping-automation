"""Where benchmark runs and their ``_state/`` live — the one place that decides.

``benchmark.runs_dir`` in ``src/config.json`` relocates the whole tree (default
``<repo>/benchmark_runs``). Every reader and writer — the benchmark CLIs and the
app's store-links code — resolves it here, so ``score --promote`` writes mappings
exactly where **Import latest run** looks.
"""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = REPO_ROOT / "benchmark_runs"


def runs_dir() -> Path:
    """``benchmark.runs_dir`` from config (relative to the repo), else ``<repo>/benchmark_runs``."""
    from src.data import CONFIG  # lazy: read at call time, so a patched CONFIG is honoured

    raw = (CONFIG.get("benchmark") or {}).get("runs_dir")
    if not raw:
        return DEFAULT_RUNS_DIR
    path = Path(raw).expanduser()
    return path if path.is_absolute() else (REPO_ROOT / path).resolve()


def state_dir() -> Path:
    """``<runs_dir>/_state`` — household data shared across runs."""
    return runs_dir() / "_state"


def mappings_dir() -> Path:
    """``<runs_dir>/_state/mappings`` — last run's verified matches, one file per store."""
    return state_dir() / "mappings"
