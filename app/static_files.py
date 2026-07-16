"""Static-asset serving: per-file cache policy + JS import hash stamping."""

from pathlib import Path
from typing import Any

from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles

from app.api_common import REPO_ROOT, STATIC_DIR
from src.static_versioning import BuildInfo

# Build identity, computed once at import — the app restarts on every code
# edit, so a fresh process always reflects the deployed code.
BUILD_INFO = BuildInfo(STATIC_DIR, REPO_ROOT)

# Hash-stamped assets (.js / .css) get a one-year immutable cache: the
# content hash in the query string makes the URL change on every edit, so a
# stale copy can never be served. index.html itself is served no-cache (see
# the `index` route) so it always revalidates and picks up new asset hashes.
_IMMUTABLE_CACHE = "public, max-age=31536000, immutable"


class CachingStaticFiles(StaticFiles):
    """``StaticFiles`` with per-file ``Cache-Control`` + JS import stamping.

    Starlette's mount serves every file with only ``ETag`` /
    ``Last-Modified``, leaving iOS Safari free to heuristic-cache. This
    subclass stamps an explicit policy keyed on the suffix, and rewrites
    the ``import './x.js'`` URLs inside every ``.js`` module with a content
    hash so a stale module can never be served — the hashed URL changes on
    every edit.

    Local port of the fleet pattern (photo-ocr / voice-transcriber); see
    ``src/static_versioning.py``.
    """

    def __init__(self, *, directory: Any, build_info: BuildInfo) -> None:
        super().__init__(directory=directory)
        self._build_info = build_info

    def file_response(self, full_path, *args, **kwargs):  # type: ignore[override]
        path = Path(full_path)
        suffix = path.suffix.lower()

        if suffix == ".js":
            # Rewrite the module graph's `import './x.js'` URLs with a
            # content hash, then long-cache — the hashed URL is the cache
            # key, so an edit invalidates it for free.
            try:
                body = path.read_text(encoding="utf-8")
            except OSError:
                return super().file_response(full_path, *args, **kwargs)
            return Response(
                content=self._build_info.stamp_js(body),
                media_type="text/javascript",
                headers={"Cache-Control": _IMMUTABLE_CACHE},
            )

        response = super().file_response(full_path, *args, **kwargs)
        if suffix == ".css":
            response.headers["Cache-Control"] = _IMMUTABLE_CACHE
        return response
