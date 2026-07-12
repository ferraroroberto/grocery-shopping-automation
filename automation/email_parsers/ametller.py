"""Deterministic parser for Ametller Origen's "La comanda està preparada!"
order-confirmation email.

Built against a real message (issue #72) pulled read-only via the vendored
``gmail_readonly`` component. Ametller's plain-text MIME part is not actually
plain for the item table — the ESP template embeds the same raw HTML table
markup used in the ``text/html`` alternative, so
:class:`gmail_readonly.NormalizedEmail`'s ``body_text`` (which prefers
``text/plain``) already carries that markup verbatim; this parser regexes
that HTML fragment rather than requiring the raw MIME payload.

The item table ("RESUM DE LA TEVA COMANDA") lists one row per confirmed
product. Each row's product-name link is the only anchor styled
``color: #000000`` (the thumbnail-image anchor uses a different style), which
makes it a reliable, order-preserving marker — no LLM needed.
"""

from __future__ import annotations

import html
import re

_ORDER_NUMBER_RE = re.compile(r"N[ÚU]MERO COMANDA:\s*(\S+)")
_ITEM_NAME_RE = re.compile(
    r'<a[^>]+style="color:\s*#000000;[^"]*"[^>]*>(.*?)</a>',
    re.IGNORECASE | re.DOTALL,
)
_TAG_RE = re.compile(r"<[^>]+>")


def _clean_item_name(raw: str) -> str:
    """Strip any nested markup/whitespace and unescape HTML entities."""
    text = _TAG_RE.sub("", raw)
    return html.unescape(text).strip()


def parse_order_number(body_text: str) -> str | None:
    """Return the order number (e.g. ``AO00023111``), or ``None`` if absent."""
    match = _ORDER_NUMBER_RE.search(body_text)
    return match.group(1) if match else None


def parse_confirmed_items(body_text: str) -> list[str]:
    """Return the ordered list of confirmed product names from the email body.

    Items dropped by the store (out of stock, etc.) simply do not appear —
    callers diff this list against the purchase log to find them (#73).
    """
    return [
        _clean_item_name(match)
        for match in _ITEM_NAME_RE.findall(body_text)
        if _clean_item_name(match)
    ]
