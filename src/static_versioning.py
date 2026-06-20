"""Build identity + static-asset versioning for the PWA.

Lets the mobile webapp prove which build it is running, so "did the
deploy take, or is the iPhone serving stale cached code?" stops being
answered by feel:

  * content-hash query stamps on every ``.js`` / ``.css`` asset so any
    edit changes the URL — no manual ``?v=N`` bumps, no stale iOS cache.
    ``index.html`` carries a ``?v=__NAME__`` placeholder for the assets
    it references directly (``app.js``); every other module is a
    transitive ``import`` from ``app.js`` and gets its ``?v=`` stamped by
    :meth:`BuildInfo.rewrite_js_imports` at serve time,
  * a build identity (git SHA + build time) surfaced via :meth:`as_dict`.

Every value is computed once when :class:`BuildInfo` is constructed at
webapp startup — the app restarts on every code edit per project
convention, so there is no watcher and no per-request work.

Local port of the fleet-wide pattern (photo-ocr / voice-transcriber
``src/static_versioning.py``); kept close to the originals so swapping to
the canonical scaffold helper (ferraroroberto/project-scaffolding#78) once
it lands is a near-mechanical change.
"""

from __future__ import annotations

# Standard library imports
import hashlib
import logging
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

logger = logging.getLogger(__name__)

# Suffixes hashed + content-stamped. Everything else under static/
# (icons, manifest) is cached more conservatively by the static mount.
STAMPED_SUFFIXES = (".js", ".css")

# Assets that ``index.html`` references directly via a ``?v=__NAME__``
# placeholder. The placeholder for each is the uppercased name with dots
# turned to underscores, e.g. ``app.js`` -> ``__APP_JS__``. Every other
# ``.js`` module is reached only through an ``import`` inside ``app.js``.
HTML_STAMPED_ASSETS = ("app.js", "styles.css")

# Static ES-module imports inside the JS graph: ``from './x.js'`` and the
# bare ``import './x.js'`` side-effect form. The optional ``?v=`` group
# makes re-stamping an already-stamped body idempotent.
_JS_IMPORT_RE = re.compile(
    r"""(from\s*['"]|import\s*['"])\./([\w\-.]+\.js)(\?v=[^'"]*)?(['"])"""
)


def _placeholder(asset_name: str) -> str:
    """The index.html token a content hash replaces, e.g. ``__APP_JS__``."""
    return "__" + asset_name.replace(".", "_").upper() + "__"


def asset_hash(path: Path) -> str:
    """Return the first 8 hex chars of the file's SHA-256.

    Falls back to ``"missing"`` when the file can't be read so a partial
    deployment degrades to a stable (if uninformative) stamp instead of
    crashing the page.
    """
    try:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
    except OSError as exc:
        logger.warning(f"⚠️  Could not hash {path} ({exc})")
        return "missing"
    return digest[:8]


def _git_short_sha(repo_root: Path) -> str:
    """Short git SHA of ``HEAD``.

    Returns ``"unknown"`` when git isn't available — e.g. the project was
    deployed from a tarball rather than a clone.
    """
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(repo_root),
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        logger.warning(f"⚠️  git SHA unavailable ({exc})")
        return "unknown"
    if result.returncode != 0:
        return "unknown"
    return result.stdout.strip() or "unknown"


class BuildInfo:
    """Immutable build identity, computed once at webapp startup."""

    def __init__(self, static_dir: Path, repo_root: Path) -> None:
        # Hash every .js/.css file in static/ — the whole ES-module graph,
        # not just the assets index.html names directly.
        self.asset_hashes: Dict[str, str] = {
            path.name: asset_hash(path)
            for path in sorted(static_dir.glob("*"))
            if path.is_file() and path.suffix.lower() in STAMPED_SUFFIXES
        }
        self.git_sha: str = _git_short_sha(repo_root)
        self.built_at: str = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )

    def stamp_html(self, html: str) -> str:
        """Replace the ``?v=__NAME__`` placeholders in index.html with the
        content hash of each directly-referenced asset."""
        for name in HTML_STAMPED_ASSETS:
            digest = self.asset_hashes.get(name)
            if digest:
                html = html.replace(_placeholder(name), digest)
        return html

    def rewrite_js_imports(self, body: str) -> str:
        """Stamp ``?v=<hash>`` onto every ``from './foo.js'`` import.

        Imports with no matching entry in :attr:`asset_hashes` are left
        untouched — robust against a dynamic import or a file added but
        not yet hashed. Any existing ``?v=…`` is replaced so re-rewriting
        an already-stamped body is idempotent.
        """
        if not self.asset_hashes:
            return body

        def _sub(match: re.Match) -> str:
            prefix, filename, _existing, quote_close = match.group(1, 2, 3, 4)
            digest = self.asset_hashes.get(filename)
            if not digest:
                return match.group(0)
            return f"{prefix}./{filename}?v={digest}{quote_close}"

        return _JS_IMPORT_RE.sub(_sub, body)

    def as_dict(self) -> Dict[str, str]:
        """Build-identity payload (git SHA, build time, app.js hash)."""
        return {
            "git_sha": self.git_sha,
            "built_at": self.built_at,
            "asset_hash": self.asset_hashes.get("app.js", "missing"),
        }
