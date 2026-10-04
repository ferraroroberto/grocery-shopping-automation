"""Deterministic parser for Carrefour's "Aviso de pedido NNN preparado" email.

Built against a real message (issue #158) read from the local email archive —
never from a live mailbox. Unlike Ametller's HTML table, Carrefour's body is
line-oriented text: each product is a name line followed by one
``Pedido <n> ... Entregado <m>`` line, grouped under section headings
("Productos que no te hemos podido entregar", "... solo te entregamos
algunas unidades", "... con variaciones en el peso", "... con normalidad").

Carrefour reports what it *delivered*, so a product with ``Entregado 0`` is
the store dropping it — exactly the signal ``email_check`` diffs against the
purchase log — and is left out of :func:`parse_confirmed_items`. The email
carries no per-line prices (only order totals), so none are parsed.

The Ametller parser takes the same ``body_text`` string, so this module is a
drop-in sibling in ``STORE_PARSERS``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_ORDER_NUMBER_RE = re.compile(r"Pedido\s+n[º°o]\s*(\d+)", re.IGNORECASE)
# "Pedido  1\tEntregado  1" or, for weighed goods,
# "Pedido  1 (1 kg) \tEntregado  1 (1,027 kg)".
_QUANTITY_LINE_RE = re.compile(
    r"^Pedido\s+(\d+)(?:\s*\([^)]*\))?\s+Entregado\s+(\d+)", re.IGNORECASE
)
_SECTION_RE = re.compile(r"^Productos\s+(?:que|de\s+los\s+que)\b", re.IGNORECASE)
_PARTIAL_SECTION_RE = re.compile(r"solo\s+te\s+entregamos\s+algunas", re.IGNORECASE)
_WEIGHT_SECTION_RE = re.compile(r"variaciones\s+en\s+el\s+peso", re.IGNORECASE)
_QUOTE_PREFIX_RE = re.compile(r"^[>\s]+")
_NBSP_RE = re.compile(r"[ \s]+")


@dataclass(frozen=True)
class ConfirmedLine:
    """One product line of the email: its name and the ordered/delivered units."""

    name: str
    ordered: int
    delivered: int


def _clean_line(raw: str) -> str:
    """Drop a reply/forward ``>`` prefix, then collapse whitespace."""
    return _NBSP_RE.sub(" ", _QUOTE_PREFIX_RE.sub("", raw)).strip()


def parse_order_number(body_text: str) -> str | None:
    """Return the order number (e.g. ``93752541``), or ``None`` if absent."""
    match = _ORDER_NUMBER_RE.search(body_text)
    return match.group(1) if match else None


def parse_confirmed_lines(body_text: str) -> list[ConfirmedLine]:
    """Return every product line with its ordered and delivered quantities.

    Order follows the email. A name wrapped over several lines (plain-text
    mail clients hard-wrap near 72 columns) is rejoined: every non-blank line
    since the last quantity line or section heading belongs to the name.

    A product shown in both the "only some units" and the "weight variations"
    sections is one cart line reported twice, so the second report is skipped.
    """
    lines: list[ConfirmedLine] = []
    pending: list[str] = []
    in_partial = False
    in_weight = False
    partial_names: set[str] = set()

    for raw in body_text.splitlines():
        text = _clean_line(raw)
        if not text:
            pending = []
            continue
        if _SECTION_RE.match(text):
            pending = []
            in_partial = bool(_PARTIAL_SECTION_RE.search(text))
            in_weight = bool(_WEIGHT_SECTION_RE.search(text))
            continue
        quantity = _QUANTITY_LINE_RE.match(text)
        if quantity is None:
            pending.append(text)
            continue
        name = " ".join(pending)
        pending = []
        if not name:
            continue
        if in_weight and name in partial_names:
            continue
        if in_partial:
            partial_names.add(name)
        lines.append(ConfirmedLine(name, int(quantity.group(1)), int(quantity.group(2))))
    return lines


def parse_confirmed_items(body_text: str) -> list[str]:
    """Return the ordered names of the products Carrefour actually delivered.

    Products with ``Entregado 0`` are omitted — callers diff this list against
    the purchase log to find them (#73).
    """
    return [line.name for line in parse_confirmed_lines(body_text) if line.delivered > 0]
