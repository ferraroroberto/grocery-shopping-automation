"""Copy rules from the design review: no developer terms, sentence-case labels."""

from __future__ import annotations

import pytest

from tests.e2e._helpers import (
    goto_mode,
)


_ALL_MODES = ["dashboard", "shopping", "audit", "audio", "list", "stores", "settings"]


_VISIBLE_COPY_JS = """
() => {
  const pane = [...document.querySelectorAll('main > section.pane')].find((p) => !p.hidden);
  const options = [...pane.querySelectorAll('option')].map((o) => o.textContent);
  return { text: pane.innerText, options };
}
"""
# Words a shopper would not use. The two sibling apps the user must start are
# named by their folder names, so those are masked before matching.
_DEV_TERMS = (
    r"\b(LLM|hub|whisper|tray|SSE|endpoint|argv|JSON|API|snake_case|Command)\b"
    r"|:\d{4}\b|\b[a-z]+_[a-z]+\b"
)


@pytest.mark.e2e
def test_visible_copy_has_no_developer_terms(page):
    """J-07: no internal identifier, port or developer vocabulary in what a
    shopper reads, on any view — the Audio audit's service status included
    (the e2e server has no voice recorder / hub / whisper, so its error banner
    is on screen)."""
    import re

    offenders = {}
    for mode in _ALL_MODES:
        goto_mode(page, mode)
        page.wait_for_timeout(400)
        copy = page.evaluate(_VISIBLE_COPY_JS)
        text = "\n".join([copy["text"], *copy["options"]])
        text = text.replace("local-llm-hub", "").replace("voice-transcriber", "")
        found = sorted({m.group(0) for m in re.finditer(_DEV_TERMS, text)})
        if found:
            offenders[mode] = found
    assert not offenders, f"developer terms visible: {offenders}"


_CASE_JS = r"""
() => [...document.querySelectorAll('main > section.pane:not([hidden]) button, main > section.pane:not([hidden]) summary, main > section.pane:not([hidden]) h1, main > section.pane:not([hidden]) h2')]
  .filter((e) => e.getClientRects().length)
  .filter((e) => !e.matches('.action-row-main'))
  .map((e) => e.textContent.replace(/\s+/g, ' ').trim())
  .filter(Boolean)
"""
_PROPER = {"Ametller", "Origen", "Mercadona", "Carrefour", "CSV"}


@pytest.mark.e2e
def test_labels_are_sentence_case(page):
    """J-08: buttons, summaries and headings use sentence case (first word
    capitalised, the rest lower) — store names and CSV aside — on every view.
    An item row's tap target is skipped: its text is the item's own name (#254)."""
    mixed = {}
    for mode in _ALL_MODES:
        goto_mode(page, mode)
        page.wait_for_timeout(300)
        for label in page.evaluate(_CASE_JS):
            words = [w for w in label.split(" ")[1:] if w and w[0].isalpha()]
            bad = [w for w in words if w[0].isupper() and w not in _PROPER and not w.isupper()]
            if bad:
                mixed.setdefault(mode, []).append(label)
    assert not mixed, f"Title Case labels: {mixed}"
