"""Score how well a store's search hit matches the spoken query (issue #87).

UI-free and deterministic — the unit-tested core of the product search. This is
a **display aid**, never an auto-decision: the app orders candidate cards by
this score and labels them, but a human always picks which product fills
``buscador`` (per the "no automated decision" requirement).

The score assumes **Spanish-language** hit names. Both stores now supply them:
Mercadona's ``display_name`` is Spanish, and Ametller's SCAPI is queried with
``locale=es`` (issue #111 — before that it returned Catalan, which made the
score meaningless there). The caller still presents Ametller hits in the
store's own relevance order rather than re-sorting by this score; see
``automation/product_search.py`` for how each store is queried and why.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Score bands for the display label. Purely cosmetic — nothing auto-fills.
STRONG_MATCH = 0.6
PARTIAL_MATCH = 0.3


def normalize(name: str, *, keep: str = "%") -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace.

    `keep` lists extra characters (besides a-z0-9) to retain before the
    final whitespace collapse — item names keep the default `%` (pack-size
    suffixes like "150g" vs "10%"); callers matching plain email subjects
    (`email_check.subject_matches`) pass `keep=""`. The one copy, shared with
    ``automation.item_matching``, which already imports from ``src/``.
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(rf"[^a-z0-9{re.escape(keep)}]+", " ", without_accents.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def score(query: str, name: str) -> float:
    """Return a 0..1 relevance score of ``name`` against the spoken ``query``.

    Combines two signals: how many of the query's words appear in the hit name
    (coverage — the dominant signal, so "sandia" scores high against "Sandía
    baja en semillas") and a whole-string similarity ratio (a light tiebreak).
    Returns 0.0 for an empty query or name.
    """
    q_norm = normalize(query)
    n_norm = normalize(name)
    if not q_norm or not n_norm:
        return 0.0

    q_tokens = q_norm.split()
    n_tokens = set(n_norm.split())
    coverage = sum(1 for t in q_tokens if t in n_tokens) / len(q_tokens)
    ratio = SequenceMatcher(None, q_norm, n_norm).ratio()
    return round(0.7 * coverage + 0.3 * ratio, 4)


def label(match_score: float) -> str:
    """Map a score to a coarse display band: ``strong`` / ``partial`` / ``weak``."""
    if match_score >= STRONG_MATCH:
        return "strong"
    if match_score >= PARTIAL_MATCH:
        return "partial"
    return "weak"
