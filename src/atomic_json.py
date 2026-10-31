"""Atomic JSON file writes.

A crash or a concurrent reader mid-write must never see a truncated state or
config file, so the document goes to a temp file in the same directory (same
volume, so ``os.replace`` is atomic) and is swapped in only once complete.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def write_json_atomic(path: Path, doc: Any) -> None:
    """Write ``doc`` as indented UTF-8 JSON to ``path``, creating parent folders.

    The temp file is removed on any failure, so a failed write leaves neither a
    partial target nor a stray ``.tmp`` beside it.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(doc, fh, ensure_ascii=False, indent=2)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
