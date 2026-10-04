"""Tests for the deterministic Carrefour "Aviso de pedido preparado" parser.

The fixture is a redacted real email (issue #158): every name, address, tax
id, order number, date and total is replaced by a placeholder; only the
product names and ordered/delivered quantities are real. The real order had
five products Carrefour could not deliver (``Entregado 0``) — the fixture
preserves that gap, which is what the confirmation check exists to catch.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from automation import email_check
from automation.email_check import (
    STORE_PARSERS,
    STORE_SUBJECTS,
    check_latest_confirmation,
    subject_matches,
)
from automation.email_parsers.carrefour import (
    ConfirmedLine,
    parse_confirmed_items,
    parse_confirmed_lines,
    parse_order_number,
)

FIXTURE = Path(__file__).parent / "fixtures" / "carrefour_order_prepared.txt"


def _body() -> str:
    return FIXTURE.read_text(encoding="utf-8")


def test_parse_order_number():
    assert parse_order_number(_body()) == "00000000"


def test_parse_order_number_missing():
    assert parse_order_number("no order number here") is None


def test_lines_carry_ordered_and_delivered_quantities():
    by_name = {}
    for line in parse_confirmed_lines(_body()):
        by_name.setdefault(line.name, line)
    assert by_name["Bebida de soja Carrefour sin azúcares brik 1 l."] == ConfirmedLine(
        "Bebida de soja Carrefour sin azúcares brik 1 l.", 6, 6
    )
    assert by_name["Vino D.O. Rueda blanco verdejo Apoteosis 75 cl."] == ConfirmedLine(
        "Vino D.O. Rueda blanco verdejo Apoteosis 75 cl.", 1, 0
    )
    # Weighed goods: the unit count is parsed, the "(1 kg)" annotation is not.
    assert by_name["Patata lavada a granel 1 Kg aprox"] == ConfirmedLine(
        "Patata lavada a granel 1 Kg aprox", 1, 1
    )


def test_confirmed_items_count_and_order():
    items = parse_confirmed_items(_body())
    assert len(items) == 36
    assert items[0] == "Manzana pink lady a granel 1 kg aprox"
    assert items[-1] == "Barra Baguettina 100 g sin gluten"


def test_confirmed_items_exclude_undelivered_products():
    items = parse_confirmed_items(_body())
    for dropped in ("Hamburguesa de Pollo", "Pera conferencia", "pizza Cirio", "verdejo"):
        assert not any(dropped.lower() in item.lower() for item in items)
    # A partially delivered product is still confirmed.
    assert "Manzana pink lady a granel 1 kg aprox" in items


def test_product_reported_in_two_sections_is_listed_once():
    # "Manzana pink lady" is in both the partial-delivery and weight-variation
    # sections: one cart line, so one entry.
    assert parse_confirmed_items(_body()).count("Manzana pink lady a granel 1 kg aprox") == 1


def test_repeated_cart_lines_in_one_section_are_kept():
    # Two separate "Arroz Sos 2 kg." cart lines, each delivered.
    assert parse_confirmed_items(_body()).count("Arroz Sos 2 kg.") == 2


def test_no_stray_whitespace_or_markup_in_names():
    items = parse_confirmed_items(_body())
    assert all(item == item.strip() and "\t" not in item and "<" not in item for item in items)


def test_wrapped_name_is_rejoined():
    body = (
        "Productos que te entregamos con normalidad\n"
        "Gel de ducha con Flor de cereza y Leche\n"
        "Hidratante NB Palmolive 600 ml.\n"
        "Pedido  1\tEntregado  1\n"
    )
    assert parse_confirmed_items(body) == [
        "Gel de ducha con Flor de cereza y Leche Hidratante NB Palmolive 600 ml."
    ]


def test_quoted_forward_prefix_is_ignored():
    body = (
        "> Productos que te entregamos con normalidad\n"
        "> Arroz Sos 2 kg.\n"
        "> Pedido  1\tEntregado  1\n"
    )
    assert parse_confirmed_items(body) == ["Arroz Sos 2 kg."]


def test_empty_body():
    assert parse_confirmed_items("") == []
    assert parse_confirmed_lines("") == []


# --- registration + email_check wiring ------------------------------------


def test_carrefour_is_registered():
    assert "carrefour" in STORE_SUBJECTS
    assert "carrefour" in STORE_PARSERS


def test_subject_matches_real_format_with_any_order_number():
    canonical = STORE_SUBJECTS["carrefour"]
    assert subject_matches("Aviso de pedido 00000000 preparado. Carrefour.", canonical)
    assert subject_matches("Fwd: Aviso de pedido 12345678 preparado. Carrefour.", canonical)


def test_subject_rejects_the_item_less_receipt_and_other_stores():
    canonical = STORE_SUBJECTS["carrefour"]
    # "Hemos recibido tu pedido" has no product list — must not be parsed.
    assert not subject_matches("Confirmación pedido nº 00000000", canonical)
    assert not subject_matches("La comanda està preparada!", canonical)


class _Mailbox:
    def __init__(self, emails):
        self._emails = emails

    def resolve_sources(self, **_kwargs):
        return (SimpleNamespace(search="q"),)

    def messages(self, _search, **_kwargs):
        return list(self._emails)

    def close(self):
        pass


class _Notifier:
    def __init__(self):
        self.sent = []

    def send_text(self, text):
        self.sent.append(text)


def test_check_matches_confirmation_against_a_carrefour_purchase_log(tmp_path, monkeypatch):
    logs = tmp_path / "purchase_logs"
    logs.mkdir()
    # Synthetic log: three items Carrefour delivered, one it dropped.
    (logs / "2026-10-04_carrefour.json").write_text(
        json.dumps(
            {
                "date": "2026-10-04",
                "store": "carrefour",
                "items": [
                    {"comida": "bebida de soja", "comprar": 6, "buscador": ""},
                    {"comida": "arroz sos", "comprar": 2, "buscador": ""},
                    {"comida": "yogur griego", "comprar": 2, "buscador": ""},
                    {"comida": "verdejo", "comprar": 3, "buscador": ""},
                ],
            }
        ),
        encoding="utf-8",
    )
    email = SimpleNamespace(
        message_id="msg1",
        timestamp="2026-10-04T10:00:00+00:00",
        subject="Aviso de pedido 00000000 preparado. Carrefour.",
        body_text=_body(),
    )
    notifier = _Notifier()
    monkeypatch.setattr(email_check, "load_gmail_senders", lambda: (object(),))
    monkeypatch.setattr(email_check, "build_gmail_mailbox", lambda: _Mailbox([email]))
    monkeypatch.setattr(email_check, "load_alias_table", lambda *a, **k: {})
    monkeypatch.setattr(email_check, "build_notify_notifier", lambda: notifier)

    result = check_latest_confirmation(
        "carrefour",
        processed_state_path=tmp_path / "state.json",
        purchase_logs_dir=logs,
    )

    assert result.checked and result.match is not None
    assert result.match.dropped_comida == ["verdejo"]
    assert {m.comida for m in result.match.matched} == {
        "bebida de soja",
        "arroz sos",
        "yogur griego",
    }
    assert notifier.sent and notifier.sent[0].startswith("✅ Carrefour order confirmed")
    assert "verdejo" in notifier.sent[0]
