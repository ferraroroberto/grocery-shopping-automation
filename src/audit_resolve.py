"""Deterministic fallback that maps an audio-audit mention back to one inventory row.

Whisper drifts on dictated Spanish product names — "garbanzos" comes back as
"Ganzos", "actimel" as "Aximell", "copos avena" as "Copos de arena". When the
LLM hands back such a phrase without resolving it to an `idx` (issue #132), this
module is the safety net: a UI-free, unit-tested fuzzy resolver that either names
one candidate row or honestly declines.

Deliberately *not* built on ``src.product_match.score`` even though it normalises
identically. That scorer puts 0.7 of its weight on **exact** token membership,
which is right for store search but scores "ganzos" against "garbanzos" at 0.24 —
precisely the intra-word drift this module exists to catch. Here the per-token
signal is itself fuzzy.

Declining is a first-class outcome: a phrase below :data:`RESOLVE_THRESHOLD`, or
one whose two best candidates sit within :data:`AMBIGUITY_MARGIN` of each other
("Chocolate negro de" against both *chocolate negro 85* and *chocolate negro 99*),
resolves to ``None`` rather than to a guess.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Iterable, List, Optional, Sequence

import pandas as pd

from src.data import COLUMNS
from src.product_match import normalize

logger = logging.getLogger(__name__)

# A phrase must clear this to resolve at all. Calibrated on the 2026-08-17 audit
# log (issue #132): the genuine whisper-drift pairs land at 0.72–1.0, while the
# nearest wrong rows for those same phrases sit at or below 0.55.
RESOLVE_THRESHOLD = 0.62

# The best candidate must beat the runner-up by more than this, or the phrase is
# treated as ambiguous and left unresolved.
AMBIGUITY_MARGIN = 0.06

# Spanish function words carry no identifying signal and drag the per-token mean
# down ("Copos de arena" → the "de" scores ~0.29 against every candidate).
_STOPWORDS = frozenset({"de", "del", "la", "el", "los", "las", "un", "una", "y", "en", "al", "lo"})

# Discourse markers the speaker drops between items. A phrase made only of these
# is narration, not a product, and must never resolve — replaying the corpus
# caught "Esto" scoring 0.889 against *pesto* purely on letter overlap.
_FILLERS = frozenset({
    "esto", "eso", "esa", "este", "esta", "aqui", "ahi", "alli", "ahora", "luego",
    "nada", "hay", "si", "no", "bueno", "vale", "vamos", "voy", "paso", "sigo",
    "abro", "cierro", "entro", "salgo", "pues", "tambien", "ya", "mas", "menos",
    "cosa", "otro", "otra", "todo", "toda", "ok",
})


@dataclass(frozen=True)
class Resolution:
    """One resolved mention: which row, how confident, and what it was called."""

    idx: int
    score: float
    comida: str
    lugar: str


def _content_tokens(text: str) -> List[str]:
    """Normalised tokens with Spanish stopwords dropped (never returns empty if
    the input had any token at all — a phrase that is *only* stopwords keeps them
    rather than scoring against nothing)."""
    tokens = normalize(text).split()
    content = [t for t in tokens if t not in _STOPWORDS]
    return content or tokens


def phrase_score(phrase: str, name: str) -> float:
    """Return a 0..1 similarity of a spoken ``phrase`` against a candidate ``name``.

    Two signals, both fuzzy so intra-word whisper drift survives: the mean
    best-match ratio of each phrase token against the name's tokens (the dominant
    signal — it is what carries "ganzos"→"garbanzos"), and a whole-string ratio as
    a tiebreak that rewards word order and length agreement.
    """
    phrase_tokens = _content_tokens(phrase)
    name_tokens = _content_tokens(name)
    if not phrase_tokens or not name_tokens:
        return 0.0

    coverage = sum(
        max(SequenceMatcher(None, token, other).ratio() for other in name_tokens)
        for token in phrase_tokens
    ) / len(phrase_tokens)
    whole = SequenceMatcher(None, normalize(phrase), normalize(name)).ratio()
    return round(0.6 * coverage + 0.4 * whole, 4)


def resolve_phrase(
    phrase: str,
    candidates_df: pd.DataFrame,
    *,
    zones: Sequence[str] = (),
    exclude: Iterable[int] = (),
    threshold: float = RESOLVE_THRESHOLD,
) -> Optional[Resolution]:
    """Best-scoring inventory row for ``phrase``, or ``None`` if it can't be pinned.

    ``zones`` (the zones the speaker actually walked) gates the candidate set to
    rows whose ``lugar`` was visited — an empty sequence means "no zone context,
    consider everything". ``exclude`` drops rows already claimed by another
    mention, which is what breaks the *chocolate negro 85 / 99* tie once 99 has
    taken its own count.
    """
    if not phrase or not phrase.strip() or candidates_df.empty:
        return None

    content = _content_tokens(phrase)
    if all(token in _FILLERS for token in content):
        logger.debug(f"unresolved {phrase!r}: narration, not a product name")
        return None

    zone_set = {normalize(z) for z in zones if str(z).strip()}
    excluded = {int(i) for i in exclude}

    scored: List[Resolution] = []
    for idx, row in candidates_df.iterrows():
        if int(idx) in excluded:
            continue
        lugar = str(row[COLUMNS["lugar"]])
        if zone_set and normalize(lugar) not in zone_set:
            continue
        comida = str(row[COLUMNS["comida"]])
        scored.append(Resolution(int(idx), phrase_score(phrase, comida), comida, lugar))

    if not scored:
        return None

    scored.sort(key=lambda r: (-r.score, r.idx))
    best = scored[0]
    if best.score < threshold:
        logger.debug(f"unresolved {phrase!r}: best {best.comida!r} at {best.score} < {threshold}")
        return None

    runner_up = scored[1].score if len(scored) > 1 else 0.0
    if best.score - runner_up <= AMBIGUITY_MARGIN:
        logger.info(
            f"⚠️ ambiguous mention {phrase!r}: {best.comida!r} ({best.score}) vs "
            f"{scored[1].comida!r} ({runner_up}) — leaving unresolved"
        )
        return None

    logger.info(f"🔎 resolved {phrase!r} → {best.comida!r} (idx {best.idx}, score {best.score})")
    return best
