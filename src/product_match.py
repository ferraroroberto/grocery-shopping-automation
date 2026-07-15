"""Score how well a store's search hit matches the spoken query (issue #87).

UI-free and deterministic — the unit-tested core of the product search. This is
a **display aid**, never an auto-decision: the app orders candidate cards by
this score and labels them, but a human always picks which product fills
``buscador`` (per the "no automated decision" requirement).

The score is only meaningful for **Spanish-language** hit names (Mercadona,
whose ``display_name`` is Spanish). Ametller's SCAPI returns **Catalan** names
("Alvocat" for aguacate), so string similarity there is unreliable — the caller
preserves Ametller's own relevance ordering and treats the score as advisory.
See ``automation/product_search.py`` for how each store is queried.
"""

from __future__ import annotations

import re
import unicodedata
from difflib import SequenceMatcher

# Score bands for the display label. Purely cosmetic — nothing auto-fills.
STRONG_MATCH = 0.6
PARTIAL_MATCH = 0.3


def normalize(name: str) -> str:
    """Lowercase, strip accents/punctuation, collapse whitespace.

    Local copy of the same helper ``automation.item_matching`` uses — kept here
    so this ``src/`` module never imports up into the ``automation/`` package
    (the data layer must stay free of cart-automation dependencies).
    """
    decomposed = unicodedata.normalize("NFKD", name)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9%]+", " ", without_accents.lower())
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
