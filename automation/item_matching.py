"""Match a store's confirmation-email item names against the purchase log.

The email calls a product by its full catalogue name (e.g. "American Burger
Ametller Origen 150g - 2uds."); the purchase log records the short internal
name a human chose (`CartItem.comida`, e.g. "burguer ternera") — the two
rarely share enough characters for string similarity alone. A persisted
correspondence table (`config/item_name_aliases.json`, keyed by store) is
the primary match path; a fuzzy fallback only covers minor future drift in
an *already-aliased* website name (e.g. a wording or emoji tweak), not the
first-time internal-name gap, per issue #72.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from pathlib import Path
from typing import Optional

from automation.models import CartItem

logger = logging.getLogger("item_matching")

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ALIASES_PATH = _REPO_ROOT / "config" / "item_name_aliases.json"

FUZZY_THRESHOLD = 0.9


@dataclass(frozen=True)
class MatchedItem:
    """One confirmed email item resolved to a purchase-log entry."""

    website_name: str
    comida: str
    confidence: float
    method: str  # "alias" | "exact" | "fuzzy"


@dataclass
class MatchResult:
    matched: list[MatchedItem] = field(default_factory=list)
    unmatched_website_names: list[str] = field(default_factory=list)
    dropped_comida: list[str] = field(default_factory=list)


def normalize(name: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace."""
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    lowered = without_accents.lower()
    cleaned = re.sub(r"[^a-z0-9%]+", " ", lowered)
    return re.sub(r"\s+", " ", cleaned).strip()


def load_alias_table(store: str, path: Optional[Path] = None) -> dict[str, str]:
    """Return ``{normalized website name: comida}`` for `store`, or ``{}``."""
    target = Path(path) if path is not None else DEFAULT_ALIASES_PATH
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ Could not read %s (%s); treating aliases as empty", target, exc)
        return {}
    entries = raw.get(store) or []
    return {
        normalize(str(entry["website_name"])): str(entry["comida"])
        for entry in entries
        if isinstance(entry, dict) and entry.get("website_name") and entry.get("comida")
    }


def load_latest_purchase_log(store: str, logs_dir: Path) -> list[CartItem]:
    """Return the items from the newest ``purchase_logs/*_{store}.json``.

    Files are named ``YYYY-MM-DD_{store}.json`` (see
    :func:`automation.purchase_log.write_purchase_logs`), so a plain
    lexicographic sort by filename orders them chronologically. Returns an
    empty list when no log exists for `store`.
    """
    candidates = sorted(logs_dir.glob(f"*_{store}.json"))
    if not candidates:
        return []
    payload = json.loads(candidates[-1].read_text(encoding="utf-8"))
    return [
        CartItem(
            super_name=store,
            comida=str(item["comida"]),
            comprar=int(item["comprar"]),
            buscador=str(item.get("buscador") or ""),
        )
        for item in payload.get("items", [])
    ]


def match_items(
    website_names: list[str],
    catalog: list[CartItem],
    *,
    aliases: dict[str, str],
    fuzzy_threshold: float = FUZZY_THRESHOLD,
) -> MatchResult:
    """Match confirmed email items against the purchase-log catalog.

    Match order per website name: alias-table lookup, then exact normalized
    match against `comida`, then fuzzy fallback (``difflib`` ratio) against
    both alias-table keys and `comida` values. Whatever `comida` values are
    never matched are returned as `dropped_comida` — the signal #73 alerts
    on.
    """
    result = MatchResult()
    comida_by_norm = {normalize(item.comida): item.comida for item in catalog}
    matched_comida: set[str] = set()

    for website_name in website_names:
        norm = normalize(website_name)

        if norm in aliases:
            comida = aliases[norm]
            result.matched.append(MatchedItem(website_name, comida, 1.0, "alias"))
            matched_comida.add(comida)
            continue

        if norm in comida_by_norm:
            comida = comida_by_norm[norm]
            result.matched.append(MatchedItem(website_name, comida, 1.0, "exact"))
            matched_comida.add(comida)
            continue

        best_comida, best_ratio = _best_fuzzy_match(norm, aliases, comida_by_norm)
        if best_comida is not None and best_ratio >= fuzzy_threshold:
            result.matched.append(MatchedItem(website_name, best_comida, best_ratio, "fuzzy"))
            matched_comida.add(best_comida)
            continue

        result.unmatched_website_names.append(website_name)

    result.dropped_comida = [
        item.comida for item in catalog if item.comida not in matched_comida
    ]
    return result


def _best_fuzzy_match(
    norm_website_name: str,
    aliases: dict[str, str],
    comida_by_norm: dict[str, str],
) -> tuple[Optional[str], float]:
    candidates: dict[str, str] = {**aliases, **comida_by_norm}
    best_comida: Optional[str] = None
    best_ratio = 0.0
    for candidate_norm, comida in candidates.items():
        ratio = SequenceMatcher(None, norm_website_name, candidate_norm).ratio()
        if ratio > best_ratio:
            best_ratio = ratio
            best_comida = comida
    return best_comida, best_ratio
