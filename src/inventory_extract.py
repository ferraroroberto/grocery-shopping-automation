"""LLM client — turns a Spanish narration transcript into structured inventory
updates by matching against the candidates list.

Uses the Anthropic SDK pointed at the local hub (local-llm-hub on :8000)
with `api_key="local-dummy"`. Routes to `claude -p` against the user's
subscription when `model` starts with `claude-`, or to a local llama.cpp
backend (qwen / gemma / glm) otherwise — same hub, same shape.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

import pandas as pd
from anthropic import APIError

from src.audit_resolve import resolve_phrase
from src.data import COLUMNS
from src.hub_client import call_hub_llm

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You are auditing a household grocery inventory.

You will receive:
- a JSON list of CANDIDATES, each with {"idx": int, "comida": str, "lugar": str}
- a TRANSCRIPT in Spanish where the speaker walks through their house, announces
  the current zone (e.g. "ahora en la nevera"), and dictates how many of each
  item they have ("tengo dos yogures", "un litro de leche", "ninguno").

Return STRICT JSON ONLY (no markdown fences, no prose) with this schema:
{
  "items": [
    {"idx": int, "count": int, "zone": str, "evidence": str}
  ],
  "zones_mentioned": [str],
  "unmatched_mentions": [
    {"phrase": str, "idx": int|null, "approx_count": int|null, "note": str}
  ]
}

Rules:
- Only include candidates that the speaker explicitly mentions. Do not invent idx values.
- The candidate's `lugar` should match the zone the speaker is currently in.
  If the speaker says "nevera" but the candidate's lugar is "garaje", do not match
  unless the speaker explicitly contradicts it.
- "ninguno" / "no tengo" / "no queda" means count=0 — include it (count=0 is a valid update).
- If a count is ambiguous ("algunos", "varios", "unos cuantos"), put the candidate
  in unmatched_mentions instead of guessing a number.
- If a phrase doesn't match any candidate, list it in unmatched_mentions.
- "evidence" is the exact 2-10 word snippet of the transcript that justified the count.
- The speaker dictates a flat "<item>, <number>." sequence. A number belongs to the
  item named immediately BEFORE it. Transcription regularly drops a number or pushes
  it past a sentence boundary onto the next item — never let a number bleed onto the
  following item. When you cannot tell which of two items a number belongs to, leave
  BOTH without a count rather than attaching it to the wrong row.
- ALWAYS fill `idx` in unmatched_mentions with the candidate the phrase refers to
  whenever you can identify one — including when no count was dictated and when the
  phrase is cut off mid-word. Use `idx` null only when the phrase matches no candidate
  at all. Never write the row number into `note` instead of `idx`.
- Transcription mangles Spanish product names; resolve them rather than giving up:
  "Aximell" is actimel, "Copos de arena" is copos avena, "Ganzos" is garbanzos.
- `approx_count` is the number you heard when you could not safely attach it to the
  item, otherwise null.
- Normalise common synonyms: frigorífico→nevera, freezer/congelador→congelador,
  pantry/despensa→despensa, garage/garaje→garaje. Use the candidate's `lugar` value.
- "zones_mentioned" lists the zone keywords the speaker explicitly named, in order.
"""


@dataclass
class ExtractionResult:
    items: List[Dict[str, Any]] = field(default_factory=list)
    zones_mentioned: List[str] = field(default_factory=list)
    unmatched_mentions: List[Dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""


class ExtractionError(RuntimeError):
    """Raised when the hub call fails or returns unparseable JSON."""


def _candidates_payload(candidates_df: pd.DataFrame) -> List[Dict[str, Any]]:
    """Project the inventory DataFrame to the {idx, comida, lugar} list the LLM expects."""
    rows = []
    for idx, row in candidates_df.iterrows():
        rows.append(
            {
                "idx": int(idx),
                "comida": str(row["comida"]),
                "lugar": str(row["lugar"]),
            }
        )
    return rows


def _parse_strict_json(text: str) -> Dict[str, Any]:
    """Parse a JSON object from `text`. One repair pass — strip a markdown fence
    or extract the first {...} block — before giving up."""
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
    if fence:
        try:
            return json.loads(fence.group(1).strip())
        except json.JSONDecodeError:
            pass

    brace = re.search(r"\{.*\}", text, re.DOTALL)
    if brace:
        try:
            return json.loads(brace.group(0))
        except json.JSONDecodeError as exc:
            raise ExtractionError(f"Could not parse JSON from LLM response: {exc}")

    raise ExtractionError(f"No JSON object found in LLM response: {text[:300]}")


def _coerce_optional_int(value: Any) -> Optional[int]:
    """Best-effort int, or None — the LLM sends nulls, strings and floats here."""
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_unmatched(
    raw_mentions: List[Any],
    candidates_df: pd.DataFrame,
    *,
    zones: List[str],
    claimed: Set[int],
) -> List[Dict[str, Any]]:
    """Validate the LLM's unmatched mentions and resolve the ones it left open.

    A mention that names an inventory row is not really *unmatched* — it is a row
    whose count went missing, and the caller has to be able to tell the two apart
    (issue #132: mentions with a resolved row were being offered for zeroing, which
    zeroed items the speaker had named out loud). So each entry comes back with a
    validated `idx` (or None) plus `resolved_by` saying who resolved it.

    Two passes on purpose: every idx the LLM supplied is banked first, so the
    deterministic fallback in pass two can never steal a row the LLM already
    claimed — and each resolution narrows the candidate pool for the next.
    """
    valid_idxs = set(candidates_df.index.tolist())
    taken = set(claimed)
    cleaned: List[Dict[str, Any]] = []

    for mention in raw_mentions:
        if not isinstance(mention, dict):
            continue
        idx = _coerce_optional_int(mention.get("idx"))
        if idx is not None and (idx not in valid_idxs or idx in taken):
            logger.warning(f"dropping unusable idx {idx} on mention {mention.get('phrase')!r}")
            idx = None
        if idx is not None:
            taken.add(idx)
        cleaned.append(
            {
                "phrase": str(mention.get("phrase", "")),
                "idx": idx,
                "approx_count": _coerce_optional_int(mention.get("approx_count")),
                "note": str(mention.get("note", "")),
                "comida": str(candidates_df.at[idx, COLUMNS["comida"]]) if idx is not None else "",
                "resolved_by": "llm" if idx is not None else "",
                "match_score": None,
            }
        )

    for entry in cleaned:
        if entry["idx"] is not None:
            continue
        resolution = resolve_phrase(
            entry["phrase"], candidates_df, zones=zones, exclude=taken
        )
        if resolution is None:
            continue
        taken.add(resolution.idx)
        entry.update(
            idx=resolution.idx,
            comida=resolution.comida,
            resolved_by="fuzzy",
            match_score=resolution.score,
        )

    resolved = sum(1 for e in cleaned if e["idx"] is not None)
    if cleaned:
        logger.info(f"🔎 unmatched mentions: {resolved}/{len(cleaned)} resolved to a row")
    return cleaned


def extract(
    transcript: str,
    candidates_df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    max_tokens: int = 4096,
    timeout: float = 90,
) -> ExtractionResult:
    """Send transcript + candidates to the hub LLM, return structured matches."""
    if not transcript.strip():
        raise ExtractionError("transcript is empty")

    candidates = _candidates_payload(candidates_df)

    user_text = (
        f"CANDIDATES (JSON):\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
        f"TRANSCRIPT (Spanish):\n{transcript}\n\n"
        f"Return JSON only."
    )

    logger.info(
        f"📡 hub={base_url} model={model} candidates={len(candidates)} "
        f"transcript_chars={len(transcript)}"
    )
    try:
        raw_text = call_hub_llm(
            base_url=base_url,
            model=model,
            system_prompt=SYSTEM_PROMPT,
            user_text=user_text,
            max_tokens=max_tokens,
            timeout=timeout,
        )
    except APIError as exc:
        raise ExtractionError(f"Hub call failed: {exc}") from exc

    logger.debug(f"raw LLM text ({len(raw_text)} chars): {raw_text[:300]}…")

    parsed = _parse_strict_json(raw_text)

    items = parsed.get("items", []) or []
    valid_idxs = set(candidates_df.index.tolist())
    cleaned_items = []
    for entry in items:
        idx = entry.get("idx")
        if not isinstance(idx, int) or idx not in valid_idxs:
            logger.warning(f"dropping item with invalid idx: {entry}")
            continue
        try:
            count = int(entry.get("count", 0))
        except (TypeError, ValueError):
            logger.warning(f"dropping item with non-int count: {entry}")
            continue
        cleaned_items.append(
            {
                "idx": idx,
                "count": max(0, count),
                "zone": str(entry.get("zone", "")),
                "evidence": str(entry.get("evidence", "")),
            }
        )

    zones = [str(z) for z in (parsed.get("zones_mentioned") or [])]
    return ExtractionResult(
        items=cleaned_items,
        zones_mentioned=zones,
        unmatched_mentions=_clean_unmatched(
            parsed.get("unmatched_mentions") or [],
            candidates_df,
            zones=zones,
            claimed={entry["idx"] for entry in cleaned_items},
        ),
        raw_text=raw_text,
    )
