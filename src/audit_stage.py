"""The legacy Streamlit voice audit's stages, declared once.

The audit walks one path: record (or paste) a transcript, review the transcript,
review the matches, done. The stage lives in ``st.session_state``; this module owns
the stage names and the only legal moves between them, so a handler that tries to
skip a stage (or reopen a finished one) fails loudly instead of rendering a screen
the earlier stages never prepared. See ``docs/audit.md``.
"""

from __future__ import annotations

from enum import Enum


class AuditStage(str, Enum):
    """Every stage the voice audit can be in. The set is closed."""

    RECORD = "record"  # waiting for audio or a pasted transcript
    TRANSCRIBED = "transcribed"  # the transcript is editable, not yet matched
    REVIEW = "review"  # matches shown, nothing applied
    DONE = "done"  # counts applied and logged


# The forward path. Starting over (RECORD) is legal from every stage and is not listed.
_NEXT = {
    AuditStage.RECORD: AuditStage.TRANSCRIBED,
    AuditStage.TRANSCRIBED: AuditStage.REVIEW,
    AuditStage.REVIEW: AuditStage.DONE,
}


def advance(current: AuditStage, target: AuditStage) -> AuditStage:
    """``target`` when the move is legal; an illegal one raises ``ValueError``."""
    if target is AuditStage.RECORD or _NEXT.get(current) is target:
        return target
    raise ValueError(f"the audit cannot go from {current.value!r} to {target.value!r}")
