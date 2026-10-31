"""The overrides and baseline JSON stores under ``<runs_dir>/_state/`` (formats in the package docstring)."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from benchmark.paths import runs_dir
from src.atomic_json import write_json_atomic

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Overrides store — format in the package docstring
# ─────────────────────────────────────────────────────────────────────────────


def overrides_path(base: Optional[Path] = None) -> Path:
    """``<runs_dir>/_state/overrides.json``."""
    return (base or runs_dir()) / "_state" / "overrides.json"


def _read_overrides(path: Path) -> tuple[dict[str, dict], bool]:
    """``(document, readable)``; a missing file is readable and empty."""
    empty: dict[str, dict] = {"items": {}, "checked": {}}
    if not path.is_file():
        return empty, True
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict):
            raise ValueError("not a JSON object")
    except (OSError, ValueError) as err:
        logger.warning("⚠️ Ignoring unreadable overrides file %s: %s", path, err)
        return empty, False
    return {f: doc[f] if isinstance(doc.get(f), dict) else {} for f in ("items", "checked")}, True


def load_overrides(base: Optional[Path] = None) -> dict[str, dict]:
    """``{"items", "checked"}`` from the overrides file; missing or unreadable → empty."""
    return _read_overrides(overrides_path(base))[0]


def _update_overrides(base: Optional[Path], change: Callable[[dict[str, dict]], None]) -> dict[str, dict]:
    """Load, apply ``change(doc)`` and write the overrides file atomically.

    An unreadable file is renamed aside (``overrides.corrupt-<time>.json``)
    rather than overwritten, so a hand-edit gone wrong loses nothing.
    """
    path = overrides_path(base)
    doc, readable = _read_overrides(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not readable:
        aside = path.with_name(f"overrides.corrupt-{datetime.now():%Y%m%d-%H%M%S}.json")
        os.replace(path, aside)
        logger.warning("⚠️ Moved unreadable overrides file aside to %s", aside.name)
    change(doc)
    write_json_atomic(path, doc)
    return doc


# ─────────────────────────────────────────────────────────────────────────────
# Monthly cost what-if baseline — format in the package docstring
# ─────────────────────────────────────────────────────────────────────────────


def baseline_path(base: Optional[Path] = None) -> Path:
    """``<runs_dir>/_state/baseline.json``."""
    return (base or runs_dir()) / "_state" / "baseline.json"


def _read_baseline(path: Path) -> tuple[Optional[dict], bool]:
    """``(document, readable)``; a missing file is readable and empty (None)."""
    if not path.is_file():
        return None, True
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(doc, dict) or not isinstance(doc.get("items"), dict):
            raise ValueError("not a baseline document")
    except (OSError, ValueError) as err:
        logger.warning("⚠️ Ignoring unreadable baseline file %s: %s", path, err)
        return None, False
    return doc, True


def load_baseline(base: Optional[Path] = None) -> Optional[dict]:
    """The saved baseline document (``{"set_at", "run_date", "items"}``), or None."""
    return _read_baseline(baseline_path(base))[0]


def _write_baseline(base: Optional[Path], doc: Optional[dict]) -> None:
    """Write the baseline file atomically, or remove it when ``doc`` is None."""
    path = baseline_path(base)
    path.parent.mkdir(parents=True, exist_ok=True)
    if doc is None:
        path.unlink(missing_ok=True)
        return
    write_json_atomic(path, doc)


def _baseline_field(doc: Optional[dict], latest_run_date: Optional[str]) -> Optional[dict[str, Any]]:
    """The ``baseline`` field embedded in a simulate()/GET result: ``{"set_at", "run_date", "stale"}``."""
    if doc is None:
        return None
    run_date = doc.get("run_date")
    return {"set_at": doc.get("set_at"), "run_date": run_date, "stale": run_date != latest_run_date}
