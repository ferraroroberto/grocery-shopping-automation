"""Unit tests for the deterministic Ametller order-confirmation parser.

The fixture is a redacted real "La comanda està preparada!" email (issue
#72). The real order dropped one item (fresas / Freson) relative to the
purchase log written when the order was placed — the fixture intentionally
preserves that real-world gap rather than a synthetic complete list.
"""

from __future__ import annotations

from pathlib import Path

from automation.email_parsers.ametller import parse_confirmed_items

FIXTURE = Path(__file__).parent / "fixtures" / "ametller_order_prepared.txt"


def _body() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_confirmed_items_count_and_order():
    items = parse_confirmed_items(_body())
    assert len(items) == 19
    assert items[0] == "American Burger Ametller Origen 150g - 2uds."
    assert items[-1] == "Suprema de salmón familiar Ametller Origen"


def test_parse_confirmed_items_excludes_dropped_item():
    items = parse_confirmed_items(_body())
    assert not any("freson" in item.lower() for item in items)


def test_parse_confirmed_items_no_html_leaks_into_names():
    items = parse_confirmed_items(_body())
    assert all("<" not in item and ">" not in item for item in items)


def test_parse_confirmed_items_empty_body():
    assert parse_confirmed_items("") == []
