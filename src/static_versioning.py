"""Build identity + static-asset versioning for the PWA.

Lets the mobile webapp prove which build it is running, so "did the
deploy take, or is the iPhone serving stale cached code?" stops being
answered by feel:

  * content-hash query stamps on every ``.js`` / ``.css`` asset so any
    edit changes the URL — no manual ``?v=N`` bumps, no stale iOS cache.
    ``index.html`` references its assets with plain ``/static/…`` URLs;
    :meth:`BuildInfo.stamp_html` stamps them at serve time, and
    :meth:`BuildInfo.stamp_js` stamps the relative ``import`` URLs inside
    every served JS module (including the ``_vendored/`` component graph),
  * a build identity (git SHA + build time) surfaced via :meth:`as_dict`.

The webapp is an ES-module graph (``index.html`` loads ``app.js`` which
imports the vendored components). A naive per-file hash would go stale:
if a vendored module changes but ``app.js`` does not, ``app.js``'s own
bytes — and so its hash — are unchanged, yet the module it pulls in is
now different. So we use a single **fleet hash** — one SHA-256 over the
concatenation of every hashable file's per-file hash. Any edit to any
asset rotates the fleet hash, so every ``?v=`` stamp changes and the
whole (tiny) module graph is re-fetched.

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
from typing import Dict, Iterable

logger = logging.getLogger(__name__)

_HASH_LEN = 8

# Suffixes under static/ that get hashed + ``?v=`` stamped. Everything
# else (icons, manifest) is cached conservatively by the static mount.
_HASHED_SUFFIXES = (".js", ".css")

# Subdirectories under static/ skipped entirely — third-party bundles
# carry their own version in the path and never benefit from a hash.
_SKIP_DIRS = ("vendor",)

# Static ES-module imports inside the JS graph: ``from './x.js'`` and the
# bare ``import './x.js'`` side-effect form. The path may descend into
# subdirectories (``./_vendored/nav/nav-tabs.js``) or climb out of one
# (``../icons/icons.js`` inside a vendored module). Any existing ``?v=…``
# is captured too, so re-stamping an already-stamped body is idempotent.
_JS_IMPORT_RE = re.compile(
    r"""(from\s*['"]|import\s*['"])((?:\.\./|\./)[\w\-./]+\.js)(\?v=[^'"]*)?(['"])"""
)

# ``href`` / ``src`` pointing at a hashable ``/static/`` asset in
# index.html — subdirectory paths (``_vendored/…``) included. Same
# idempotence rule as the JS import regex.
_INDEX_ASSET_RE = re.compile(
    r"""(href|src)=(['"])/static/([\w\-./]+\.(?:css|js))(\?v=[^'"]*)?(['"])"""
)


def _short_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()[:_HASH_LEN]


def _iter_hashable_files(static_dir: Path) -> Iterable[Path]:
    for path in sorted(static_dir.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(static_dir).parts[:-1]
        if any(part in _SKIP_DIRS for part in rel_parts):
            continue
        if path.suffix.lower() not in _HASHED_SUFFIXES:
            continue
        yield path


def compute_asset_hashes(static_dir: Path) -> Dict[str, str]:
    """Return ``{filename: fleet_hash}`` for every hashable static file.

    Every value is the same fleet hash (see the module docstring); the
    dict is keyed by the slash-separated path relative to static/ — a root
    file's key is its plain filename, a vendored file's key carries its
    subdirectory (``_vendored/nav/nav-tabs.css``) — so the rewriters can
    confirm a referenced file actually exists before stamping it. Falls
    back to an empty dict when the static dir or its files can't be read —
    a partial deploy then degrades to unstamped URLs rather than crashing
    the page.
    """
    if not static_dir.exists():
        return {}
    per_file: Dict[str, str] = {}
    for path in _iter_hashable_files(static_dir):
        rel = path.relative_to(static_dir).as_posix()
        try:
            per_file[rel] = _short_hash(path.read_bytes())
        except OSError as exc:
            logger.warning(f"⚠️  Could not hash {path} ({exc})")
    if not per_file:
        return {}
    fleet_input = "\n".join(
        f"{name}:{per_file[name]}" for name in sorted(per_file)
    ).encode("utf-8")
    fleet_hash = _short_hash(fleet_input)
    return {name: fleet_hash for name in per_file}


def fleet_hash_of(hashes: Dict[str, str]) -> str:
    """The single representative hash. Empty string if no assets."""
    if not hashes:
        return ""
    return next(iter(hashes.values()))


def rewrite_index_html(body: str, hashes: Dict[str, str]) -> str:
    """Stamp ``?v=<hash>`` onto every ``/static/<file>.(css|js)`` href/src.

    Unknown files pass through unchanged — robust against a new asset
    not yet in the hash map. Existing ``?v=…`` is replaced.
    """
    if not hashes:
        return body

    def _sub(match: "re.Match[str]") -> str:
        attr, quote_open, filename, _existing, quote_close = match.group(
            1, 2, 3, 4, 5
        )
        stamp = hashes.get(filename)
        if not stamp:
            return match.group(0)
        return f"{attr}={quote_open}/static/{filename}?v={stamp}{quote_close}"

    return _INDEX_ASSET_RE.sub(_sub, body)


def rewrite_js_imports(body: str, hashes: Dict[str, str]) -> str:
    """Stamp ``?v=<hash>`` onto every relative ``'…/foo.js'`` import.

    Imports with no matching entry in ``hashes`` are left alone. Existing
    ``?v=…`` is replaced, so re-rewriting a served body is idempotent.

    A ``./``-rooted import matches its static/-relative key directly. A
    ``../`` import (a vendored module reaching a sibling component) can't
    be resolved without knowing the importing file's directory, so it
    falls back to a basename lookup — safe because every entry carries
    the same fleet hash (see the module docstring).
    """
    if not hashes:
        return body
    by_basename = {name.rsplit("/", 1)[-1]: stamp for name, stamp in hashes.items()}

    def _sub(match: "re.Match[str]") -> str:
        prefix, filename, _existing, quote_close = match.group(1, 2, 3, 4)
        stamp = hashes.get(filename[2:]) if filename.startswith("./") else None
        if not stamp:
            stamp = by_basename.get(filename.rsplit("/", 1)[-1])
        if not stamp:
            return match.group(0)
        return f"{prefix}{filename}?v={stamp}{quote_close}"

    return _JS_IMPORT_RE.sub(_sub, body)


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
        self.asset_hashes: Dict[str, str] = compute_asset_hashes(static_dir)
        self.fleet_hash: str = fleet_hash_of(self.asset_hashes)
        self.git_sha: str = _git_short_sha(repo_root)
        self.built_at: str = datetime.now(timezone.utc).isoformat(
            timespec="seconds"
        )

    def stamp_html(self, html: str) -> str:
        """Stamp the asset URLs in index.html with the fleet hash."""
        return rewrite_index_html(html, self.asset_hashes)

    def stamp_js(self, body: str) -> str:
        """Stamp the relative ``import`` URLs in a served JS module."""
        return rewrite_js_imports(body, self.asset_hashes)

    def as_dict(self) -> Dict[str, str]:
        """Build-identity payload (git SHA, build time, fleet hash)."""
        return {
            "git_sha": self.git_sha,
            "built_at": self.built_at,
            "asset_hash": self.fleet_hash or "missing",
        }
