"""Orchestration entrypoint: check the latest store confirmation email.

Connects to Gmail read-only, finds the newest whitelisted-sender email whose
subject is similar enough to the expected "order prepared" subject, parses
its item list, matches it against the latest purchase log, sends a plain
Telegram summary, and records the message as processed so a repeat call is a
no-op.

This is the seam issue #73 will call from a scheduled/web-app-integrated
poller — this module intentionally adds no scheduler and no web route, only
the pure check function below.
"""

from __future__ import annotations

import difflib
import json
import logging
import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from automation.email_parsers import ametller as ametller_parser
from automation.item_matching import (
    MatchResult,
    load_alias_table,
    load_latest_purchase_log,
    match_items,
)
from gmail_readonly import GmailReadError
from src.gmail_config import build_gmail_mailbox, load_gmail_senders
from src.notify_config import build_notify_notifier

logger = logging.getLogger("email_check")

_REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PROCESSED_STATE_PATH = _REPO_ROOT / "config" / "gmail_processed_state.json"
DEFAULT_PURCHASE_LOGS_DIR = _REPO_ROOT / "purchase_logs"

# Store key -> canonical "order prepared" subject, accents/emoji stripped.
# One entry today (Ametller); add a sibling entry once another store's
# confirmation-email format is available (issue #72 scope note).
STORE_SUBJECTS: dict[str, str] = {
    "ametller": "la comanda esta preparada",
}
# Store key -> that store's parser module, mirroring
# automation.run_automation.HANDLERS.
STORE_PARSERS = {
    "ametller": ametller_parser,
}
SUBJECT_SIMILARITY_THRESHOLD = 0.8


@dataclass
class ConfirmationCheckResult:
    store: str
    checked: bool
    message_id: Optional[str] = None
    already_processed: bool = False
    match: Optional[MatchResult] = None
    notified: bool = False
    reason: Optional[str] = None


def _normalize_subject(subject: str) -> str:
    decomposed = unicodedata.normalize("NFKD", subject)
    without_accents = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    cleaned = re.sub(r"[^a-z0-9 ]+", " ", without_accents.lower())
    return re.sub(r"\s+", " ", cleaned).strip()


def subject_matches(subject: str, canonical: str, *, threshold: float = SUBJECT_SIMILARITY_THRESHOLD) -> bool:
    """True when `subject` is similar enough to `canonical` after normalizing.

    Tolerates minor drift (an added/removed emoji, punctuation) while
    rejecting unrelated (e.g. promotional) emails from the same sender.
    """
    ratio = difflib.SequenceMatcher(
        None, _normalize_subject(subject), _normalize_subject(canonical)
    ).ratio()
    return ratio >= threshold


def _load_processed_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("⚠️ Could not read %s (%s); treating state as empty", path, exc)
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_processed_state(path: Path, state: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def has_problem(match: MatchResult) -> bool:
    """True when the confirmation dropped an ordered item or names went unmatched."""

    return bool(match.dropped_comida or match.unmatched_website_names)


def _summary_message(store: str, match: MatchResult) -> str:
    total = len(match.matched) + len(match.unmatched_website_names)
    lines = [f"✅ {store.title()} order confirmed — {len(match.matched)}/{total} items matched."]
    if match.dropped_comida:
        lines.append("⚠️ Not in the confirmation: " + ", ".join(match.dropped_comida))
    if match.unmatched_website_names:
        lines.append("❓ Unrecognized in email: " + ", ".join(match.unmatched_website_names))
    return "\n".join(lines)


def check_latest_confirmation(
    store: str = "ametller",
    *,
    processed_state_path: Optional[Path] = None,
    purchase_logs_dir: Optional[Path] = None,
    send_notification: bool = True,
    ignore_processed: bool = False,
    notify_only_on_problem: bool = False,
) -> ConfirmationCheckResult:
    """Fetch, parse, match, and (optionally) alert on the latest confirmation email.

    Idempotent across repeated calls: once a message id has been processed
    it is skipped on subsequent runs (`already_processed=True`), so the
    Auto-tab poller (#73) can call this safely without double-notifying.
    `ignore_processed=True` re-processes the latest email even if already
    seen — the Auto tab's end-to-end test path. `notify_only_on_problem=True`
    keeps a fully-matched order silent (issue #73: don't spam Telegram for a
    clean confirmation) — only a dropped/unmatched item alerts.
    """
    if store not in STORE_SUBJECTS:
        return ConfirmationCheckResult(store, checked=False, reason=f"no parser configured for store '{store}'")

    state_path = processed_state_path or DEFAULT_PROCESSED_STATE_PATH
    logs_dir = purchase_logs_dir or DEFAULT_PURCHASE_LOGS_DIR

    senders = load_gmail_senders()
    if not senders:
        return ConfirmationCheckResult(store, checked=False, reason="no Gmail sender whitelisted")

    try:
        mailbox = build_gmail_mailbox()
    except (GmailReadError, FileNotFoundError, RuntimeError) as exc:
        return ConfirmationCheckResult(store, checked=False, reason=f"Gmail connection failed: {exc}")

    try:
        sources = mailbox.resolve_sources(senders=senders, lookback_days=30)
        candidates = []
        for source in sources:
            candidates.extend(mailbox.messages(source.search, limit=50))
    except (GmailReadError, ValueError) as exc:
        return ConfirmationCheckResult(store, checked=False, reason=f"Gmail fetch failed: {exc}")
    finally:
        mailbox.close()

    canonical_subject = STORE_SUBJECTS[store]
    matching = [
        email
        for email in candidates
        if email.subject and subject_matches(email.subject, canonical_subject)
    ]
    if not matching:
        return ConfirmationCheckResult(store, checked=True, reason="no matching confirmation email found")

    latest = max(matching, key=lambda email: email.timestamp)
    state = _load_processed_state(state_path)
    if not ignore_processed and state.get(store) == latest.message_id:
        return ConfirmationCheckResult(store, checked=True, message_id=latest.message_id, already_processed=True)

    website_names = STORE_PARSERS[store].parse_confirmed_items(latest.body_text or "")
    catalog = load_latest_purchase_log(store, logs_dir)
    aliases = load_alias_table(store)
    result = match_items(website_names, catalog, aliases=aliases)

    notified = False
    if send_notification and (not notify_only_on_problem or has_problem(result)):
        notifier = build_notify_notifier()
        if notifier is not None:
            notifier.send_text(_summary_message(store, result))
            notified = True

    state[store] = latest.message_id
    _write_processed_state(state_path, state)

    return ConfirmationCheckResult(
        store, checked=True, message_id=latest.message_id, match=result, notified=notified
    )
