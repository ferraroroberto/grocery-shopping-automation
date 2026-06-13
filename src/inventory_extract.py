"""LLM client — turns a Spanish narration transcript into structured inventory
updates by matching against the candidates list.

Uses the Anthropic SDK pointed at the local hub (claude-local-calls on :8000)
with `api_key="local-dummy"`. Routes to `claude -p` against the user's
subscription when `model` starts with `claude-`, or to a local llama.cpp
backend (qwen / gemma / glm) otherwise — same hub, same shape.
"""

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd
from anthropic import Anthropic, APIError

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
    {"phrase": str, "approx_count": int, "note": str}
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

    client = Anthropic(api_key="local-dummy", base_url=base_url, timeout=timeout)
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
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_text}],
        )
    except APIError as exc:
        raise ExtractionError(f"Hub call failed: {exc}") from exc

    raw_text = "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )
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

    return ExtractionResult(
        items=cleaned_items,
        zones_mentioned=[str(z) for z in (parsed.get("zones_mentioned") or [])],
        unmatched_mentions=[
            dict(m) for m in (parsed.get("unmatched_mentions") or []) if isinstance(m, dict)
        ],
        raw_text=raw_text,
    )
