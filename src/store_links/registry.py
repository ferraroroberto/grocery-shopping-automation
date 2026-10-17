"""Store registry, link kinds and benchmark-run discovery for :mod:`src.store_links`."""

from __future__ import annotations

import json
import logging
import re
from functools import lru_cache
from pathlib import Path
from typing import Optional

from benchmark.paths import mappings_dir, runs_dir
from src.data import REPO_ROOT

logger = logging.getLogger(__name__)


STORES_REGISTRY_PATH = REPO_ROOT / "benchmark" / "stores.json"
_RUN_DIR_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# A store-search results page rather than a product: bot-protected stores
# (Bonpreu, Alcampo) left the benchmark only their search URL.
_SEARCH_URL_RE = re.compile(r"/(search|buscar|busqueda)(?:[/?#]|$)|[?&](q|query|search)=", re.IGNORECASE)


class StoreLinkError(ValueError):
    """A request the data can't satisfy: unknown row/store, missing URL, bad value."""


class NoBenchmarkRunError(LookupError):
    """No scored benchmark run to price or recommend from."""


def _is_url(text: str) -> bool:
    return text.lower().startswith(("http://", "https://"))


def _same_url(a: str, b: str) -> bool:
    return bool(a) and bool(b) and a.strip().rstrip("/") == b.strip().rstrip("/")


def link_kind(store: str, url: str) -> str:
    """``"search"`` (a search-results page), ``"suspect"`` (an http(s) URL
    that doesn't match the store's ``product_url_pattern`` in
    ``benchmark/stores.json`` — e.g. a Carrefour slug without its
    `/R-<id>/p` product id, which redirects to the home page; a store with
    no declared pattern is never "suspect") or ``"product"``.

    The pattern is only applied to an actual http(s) URL — some stores'
    ``buscador``/``url_<store>`` cells hold a plain search term instead of a
    link, which is a pre-existing data shape, not a bad product link.
    """
    if _SEARCH_URL_RE.search(url):
        return "search"
    if not _is_url(url):
        return "product"
    pattern = _product_url_pattern(store)
    if pattern is not None and not pattern.search(url):
        return "suspect"
    return "product"


@lru_cache(maxsize=None)
def _product_url_pattern(store: str) -> Optional[re.Pattern[str]]:
    """``store``'s compiled ``product_url_pattern``, read once per process (the registry is tracked config)."""
    raw = (load_registry().get(store) or {}).get("product_url_pattern")
    return re.compile(raw) if raw else None


# ─────────────────────────────────────────────────────────────────────────────
# Registry + run discovery
# ─────────────────────────────────────────────────────────────────────────────


def load_registry() -> dict[str, dict]:
    """Store key → registry entry from ``benchmark/stores.json``."""
    return json.loads(STORES_REGISTRY_PATH.read_text(encoding="utf-8"))["stores"]


def latest_run_dir(base: Optional[Path] = None) -> Optional[Path]:
    """Newest ``YYYY-MM-DD`` run that has been scored, or None.

    A run counts once it has both ``basket.json`` and ``scenarios.json``: a
    run still being researched has a basket but partial store files, and
    pricing from it would silently drop stores. The run date is the
    directory name.
    """
    base = base or runs_dir()
    if not base.is_dir():
        return None
    runs = sorted(
        p for p in base.iterdir()
        if p.is_dir() and _RUN_DIR_RE.match(p.name)
        and (p / "basket.json").is_file() and (p / "scenarios.json").is_file()
    )
    return runs[-1] if runs else None


def _require_run(run_dir: Optional[Path]) -> Path:
    run = run_dir or latest_run_dir()
    if run is None:
        raise NoBenchmarkRunError(f"No scored benchmark run found in {runs_dir()}")
    return run


def load_mappings(base: Optional[Path] = None) -> dict[str, dict[str, dict]]:
    """Store → {item key → mapping} from ``_state/mappings/*.json``."""
    mdir = base / "_state" / "mappings" if base else mappings_dir()
    out: dict[str, dict[str, dict]] = {}
    for path in sorted(mdir.glob("*.json")) if mdir.is_dir() else []:
        try:
            out[path.stem] = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as err:
            logger.warning("⚠️ Skipping unreadable mapping %s: %s", path.name, err)
    return out
