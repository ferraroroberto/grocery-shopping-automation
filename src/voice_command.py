"""Voice-command parsing + apply + speech building (issue #86).

The HA Voice PE pipeline relays free Spanish text here (home-automation#315):
the hub LLM parses items/quantities against the inventory candidates — same
client pattern as `inventory_extract.py` — and pure Python applies the change
and builds the short spoken reply. The LLM never picks the operation (HA's
deterministic sentence match does) and never actuates.
"""

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from anthropic import APIError

from src.data import COLUMNS, build_new_item_row
from src.hub_client import call_hub_llm
from src.inventory_extract import ExtractionError, _parse_strict_json

logger = logging.getLogger(__name__)


SYSTEM_PROMPT = """You manage a household grocery shopping list by voice.

You will receive:
- a JSON list of CANDIDATES, each {"idx": int, "comida": str} — the existing inventory items
- a COMMAND: a short Spanish voice fragment naming grocery items, usually
  comma- or "y"-separated ("leche, dos huevos y pan"), possibly with amounts
  ("pon el objetivo de leche a cuatro", "tenemos dos botellas de aceite").

Return STRICT JSON ONLY (no markdown fences, no prose) with this schema:
{
  "items": [
    {"idx": int or null, "name": str, "qty": int or null}
  ],
  "ambiguous": [
    {"phrase": str, "note": str}
  ]
}

Rules:
- Match each mentioned item against CANDIDATES by meaning, tolerating
  singular/plural, diminutives and common synonyms. Use the candidate's idx;
  use idx null ONLY when no candidate plausibly matches — then "name" is the
  new item name exactly as spoken (lowercase, no filler words).
- For a matched item, "name" is the candidate's comida verbatim.
- "qty" is the number spoken for that item ("dos huevos" → 2, "cuatro" → 4,
  "media docena" → 6). No number spoken → qty null.
- Zero-phrases mean qty 0 for that item: "no queda", "no quedan", "se acabó",
  "no hay", "ninguno" → qty 0 (0 is a valid, meaningful value).
- NEVER guess an ambiguous amount ("algunos", "varios", "unos cuantos") —
  put the phrase in "ambiguous" instead of inventing a number.
- Ignore politeness/filler words. Do not invent items that were not mentioned.
"""


@dataclass(frozen=True)
class VoiceItem:
    """One parsed mention: a candidate idx (or ``None`` for a new item),
    the display name, and the spoken quantity (``None`` when unspoken)."""

    idx: Optional[int]
    name: str
    qty: Optional[int]


@dataclass
class VoiceParseResult:
    items: List[VoiceItem] = field(default_factory=list)
    ambiguous: List[Dict[str, Any]] = field(default_factory=list)
    raw_text: str = ""


@dataclass
class AddOutcome:
    """What `apply_add` did: existing items bumped vs new rows created."""

    bumped: List[Tuple[str, int]] = field(default_factory=list)
    created: List[Tuple[str, int]] = field(default_factory=list)


@dataclass
class SetOutcome:
    """What `apply_set` did, plus what it could not action."""

    set_items: List[Tuple[str, int]] = field(default_factory=list)
    not_found: List[str] = field(default_factory=list)
    no_value: List[str] = field(default_factory=list)


def _clean_qty(value: Any) -> Optional[int]:
    if isinstance(value, bool) or value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def clean_parsed(parsed: Dict[str, Any], valid_idxs: set) -> Tuple[List[VoiceItem], List[Dict[str, Any]]]:
    """Coerce the LLM's JSON into validated ``VoiceItem``s.

    An invented idx is demoted to ``None`` (the name survives, so the item
    is still reportable/creatable) rather than dropped — the LLM must never
    be able to redirect a mutation to an arbitrary row.
    """
    items: List[VoiceItem] = []
    for entry in parsed.get("items") or []:
        if not isinstance(entry, dict):
            continue
        idx = entry.get("idx")
        if idx is not None and (isinstance(idx, bool) or not isinstance(idx, int) or idx not in valid_idxs):
            logger.warning(f"⚠️ demoting item with invalid idx to new-item: {entry}")
            idx = None
        name = str(entry.get("name") or "").strip()
        if not name and idx is None:
            logger.warning(f"⚠️ dropping unnamed item: {entry}")
            continue
        items.append(VoiceItem(idx=idx, name=name, qty=_clean_qty(entry.get("qty"))))
    ambiguous = [dict(m) for m in (parsed.get("ambiguous") or []) if isinstance(m, dict)]
    return items, ambiguous


def parse_voice_items(
    text: str,
    candidates_df: pd.DataFrame,
    *,
    base_url: str,
    model: str,
    max_tokens: int = 4096,
    timeout: float = 90,
) -> VoiceParseResult:
    """Send the spoken fragment + candidates to the hub LLM, return validated items."""
    if not text.strip():
        raise ExtractionError("command text is empty")

    candidates = [
        {"idx": int(idx), "comida": str(row[COLUMNS["comida"]])}
        for idx, row in candidates_df.iterrows()
    ]
    user_text = (
        f"CANDIDATES (JSON):\n{json.dumps(candidates, ensure_ascii=False)}\n\n"
        f"COMMAND (Spanish):\n{text}\n\n"
        f"Return JSON only."
    )

    logger.info(f"📡 voice parse hub={base_url} model={model} candidates={len(candidates)}")
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

    parsed = _parse_strict_json(raw_text)
    items, ambiguous = clean_parsed(parsed, set(candidates_df.index.tolist()))
    return VoiceParseResult(items=items, ambiguous=ambiguous, raw_text=raw_text)


# --------------------------------------------------------------------------- #
# Apply — in-memory mutations; the caller saves once (apply_item_edit pattern)
# --------------------------------------------------------------------------- #
def _display_name(df: pd.DataFrame, idx: int) -> str:
    return str(df.at[idx, COLUMNS["comida"]]).strip().lower()


def _recompute_comprar(df: pd.DataFrame, idx: int) -> None:
    df.at[idx, COLUMNS["comprar"]] = max(
        0, int(df.at[idx, COLUMNS["cantidad"]]) - int(df.at[idx, COLUMNS["tenemos"]])
    )


def apply_add(df: pd.DataFrame, items: List[VoiceItem]) -> Tuple[pd.DataFrame, AddOutcome]:
    """Raise matched items' target by the spoken qty (default 1) so they show
    up on the shopping list; unmatched names become new rows (target=qty,
    have=0, empty super/lugar/buscador — the app UI or the product search
    fills those later). Mutates in memory; the caller persists."""
    outcome = AddOutcome()
    new_rows: List[Dict[str, object]] = []
    for item in items:
        qty = max(1, item.qty or 1)
        if item.idx is not None:
            df.at[item.idx, COLUMNS["cantidad"]] = int(df.at[item.idx, COLUMNS["cantidad"]]) + qty
            _recompute_comprar(df, item.idx)
            outcome.bumped.append((_display_name(df, item.idx), qty))
        else:
            new_rows.append(
                build_new_item_row(
                    super_value="", lugar="", comida=item.name,
                    cantidad=qty, tenemos=0, buscador="",
                )
            )
            outcome.created.append((item.name.lower(), qty))
    if new_rows:
        df = pd.concat([df, pd.DataFrame(new_rows)], ignore_index=True)
    return df, outcome


def apply_set(
    df: pd.DataFrame, items: List[VoiceItem], column_key: str
) -> Tuple[pd.DataFrame, SetOutcome]:
    """Set ``cantidad`` (target) or ``tenemos`` (stock) to the spoken value.

    Unlike ``apply_add``, an unmatched name is an error to report, not a row
    to create — you can't set the target of an item that doesn't exist.
    """
    outcome = SetOutcome()
    for item in items:
        if item.idx is None:
            outcome.not_found.append(item.name.lower())
            continue
        if item.qty is None:
            outcome.no_value.append(_display_name(df, item.idx))
            continue
        df.at[item.idx, COLUMNS[column_key]] = item.qty
        _recompute_comprar(df, item.idx)
        outcome.set_items.append((_display_name(df, item.idx), item.qty))
    return df, outcome


# --------------------------------------------------------------------------- #
# Speech — one short SPANISH sentence (issue #89): the voice loop runs on a
# dedicated Spanish pipeline with an es_ES TTS voice (home-automation#315's
# redesign), so replies are Spanish end-to-end.
# --------------------------------------------------------------------------- #
SPEECH_BUSY = "La lista está abierta en otro sitio; inténtalo en un momento."
_QUERY_NAME_CAP = 8


def _speak_list(parts: List[str]) -> str:
    if len(parts) <= 1:
        return "".join(parts)
    return ", ".join(parts[:-1]) + " y " + parts[-1]


def _named_qty(name: str, qty: int) -> str:
    return f"{qty} {name}" if qty > 1 else name


def build_add_speech(outcome: AddOutcome, ambiguous: List[Dict[str, Any]]) -> str:
    parts: List[str] = []
    if outcome.bumped:
        parts.append(
            f"He añadido {_speak_list([_named_qty(n, q) for n, q in outcome.bumped])} a la lista"
        )
    if outcome.created:
        parts.append(
            f"he creado {_speak_list([_named_qty(n, q) for n, q in outcome.created])} como nuevo"
        )
    if not parts:
        return "No he entendido qué añadir — inténtalo otra vez."
    speech = "; ".join(parts)
    speech = speech[0].upper() + speech[1:] + "."
    if ambiguous:
        phrases = [str(m.get("phrase", "")).strip() for m in ambiguous if m.get("phrase")]
        if phrases:
            speech += f" No he entendido la cantidad de {_speak_list(phrases)}."
    return speech


def build_set_speech(outcome: SetOutcome, kind: str) -> str:
    kind_word = "El objetivo" if kind == "target" else "El stock"
    parts: List[str] = []
    if outcome.set_items:
        parts.append(
            _speak_list([f"{kind_word.lower()} de {n} es ahora {q}" for n, q in outcome.set_items])
        )
        parts[0] = parts[0][0].upper() + parts[0][1:]
    if outcome.no_value:
        parts.append(f"Necesito un número para {_speak_list(outcome.no_value)}")
    if outcome.not_found:
        parts.append(f"No encuentro {_speak_list(outcome.not_found)} en la lista")
    if not parts:
        return "No he entendido nada — inténtalo otra vez."
    return ". ".join(parts) + "."


def build_query_speech(df: pd.DataFrame) -> str:
    shopping = df[df[COLUMNS["comprar"]] > 0]
    if shopping.empty:
        return "La lista está vacía — no hay nada que comprar."
    per_store = shopping[COLUMNS["super"]].fillna("").replace("", "sin súper").value_counts()
    stores = _speak_list([f"{count} en {store}" for store, count in per_store.items()])
    names = [str(n).strip().lower() for n in shopping[COLUMNS["comida"]].tolist()]
    named = _speak_list(names[:_QUERY_NAME_CAP])
    more = len(names) - _QUERY_NAME_CAP
    tail = f", y {more} más" if more > 0 else ""
    return f"{len(shopping)} cosas por comprar — {stores}: {named}{tail}."
