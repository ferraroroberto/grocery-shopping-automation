"""The cart run's guard (issue #247): retry once, verify the final cart, suggest, report.

Everything runs against a fake store adapter: no Chrome, no store, no cart, and
the notifier is a recorder (the autouse conftest fixture keeps the real one off).
"""

import pytest

from automation import mercadona, product_search, run_automation
from automation.errors import AddToCartFailed, OutOfStockError, ProductUnavailableError
from automation.models import CartItem
from automation.product_search import Candidate
from automation.report import (
    ADDED_NOT_IN_CART,
    ADDED_UNVERIFIED,
    ADDED_VERIFIED,
    OUT_OF_STOCK,
    UNAVAILABLE,
)

URL = "https://shop.test/p/{}"


class FakeStore:
    """A store adapter double: scripted add results and a scripted final cart."""

    def __init__(self, results: dict, cart):
        self.results = {name: list(seq) for name, seq in results.items()}
        self.cart = cart
        self.calls: list[str] = []

    def add_to_cart(self, page, item: CartItem) -> None:
        self.calls.append(item.comida)
        result = self.results[item.comida].pop(0)
        if result is not None:
            raise result

    @staticmethod
    def cart_key(url: str) -> str:
        return url.rsplit("/", 1)[-1]

    def read_cart_lines(self, page) -> dict:
        if isinstance(self.cart, Exception):
            raise self.cart
        return self.cart

    def read_cart_total(self, page) -> int:
        return 0


class _Closable:
    def close(self) -> None:
        pass

    def stop(self) -> None:
        pass


class RecordingNotifier:
    def __init__(self) -> None:
        self.sent: list[str] = []

    def send_text(self, text: str) -> None:
        self.sent.append(text)


def _item(name: str, qty: int = 1, **kw) -> CartItem:
    return CartItem("fake", name, qty, URL.format(name), **kw)


@pytest.fixture()
def run(monkeypatch):
    """Run ``main`` over a fake store → ``(exit code, report, store, notifier)``."""

    def _run(items, results, cart, *, search=None, argv=()):
        store = FakeStore(results, cart)
        notifier = RecordingNotifier()
        written = []
        monkeypatch.setitem(run_automation.HANDLERS, "fake", store)
        monkeypatch.setitem(product_search.SEARCHERS, "fake", search or (lambda page, q, limit: []))
        monkeypatch.setattr(run_automation, "read_cart_items", lambda _store: items)
        monkeypatch.setattr(run_automation, "launch_context", lambda **kw: (_Closable(), _Closable(), object()))
        monkeypatch.setattr(run_automation, "human_delay", lambda *a, **k: None)
        monkeypatch.setattr(run_automation, "build_notify_notifier", lambda: notifier)
        monkeypatch.setattr(run_automation, "_write_purchase_log_if_live",
                            lambda report, dry, text="": written.append((report, text)) or [])
        code = run_automation.main(list(argv))
        report, _text = written[0]
        return code, report, store, notifier

    return _run


def _by_name(report):
    return {o.item.comida: o for o in report.outcomes}


def test_a_transient_add_failure_is_retried_once_and_verified(run):
    code, report, store, notifier = run(
        [_item("kiwi"), _item("pan")],
        {"kiwi": [AddToCartFailed(_item("kiwi"), "click did not register"), None], "pan": [None]},
        {"kiwi": 1, "pan": 1},
    )
    assert store.calls == ["kiwi", "pan", "kiwi"]  # the retry runs after the pass
    kiwi = _by_name(report)["kiwi"]
    assert (kiwi.status, kiwi.retries, kiwi.cart_qty) == (ADDED_VERIFIED, 1, 1)
    assert code == 0 and report.missing == []
    assert notifier.sent == ["🛒 Cart run: ✅ 2 verified in cart, nothing missing"]


def test_unavailable_and_out_of_stock_are_not_retried_and_get_a_suggestion(run):
    def search(page, query, limit):
        return [
            Candidate("fake", "same product", URL.format(query), "1,00 €", 1.0, "", 0, 1.0, "strong"),
            Candidate("fake", f"other {query}", URL.format(f"{query}-alt"), "2,00 €", 2.0, "", 1, 0.8, "strong"),
        ]

    code, report, store, notifier = run(
        [_item("savoiardi", alt_stores=("mercadona",)), _item("azucar")],
        {"savoiardi": [ProductUnavailableError(_item("savoiardi"), "redirected to a category page")],
         "azucar": [OutOfStockError(_item("azucar"))]},
        {},
        search=search,
    )
    assert store.calls == ["savoiardi", "azucar"]  # no retry for either
    out = _by_name(report)
    assert out["savoiardi"].status == UNAVAILABLE and out["azucar"].status == OUT_OF_STOCK
    assert out["savoiardi"].suggestion == (
        f"other savoiardi (2,00 €) {URL.format('savoiardi-alt')}; also set up at mercadona"
    )
    assert out["azucar"].suggestion.startswith("other azucar")
    assert code == 1
    assert "❌ 2 NOT in cart" in notifier.sent[0]
    assert "savoiardi ×1 — unavailable: redirected to a category page" in notifier.sent[0]


def test_an_add_that_never_landed_is_reported_added_not_in_cart(run, capsys):
    code, report, _store, notifier = run(
        [_item("pavo", 8), _item("quinoa", 2)],
        {"pavo": [None], "quinoa": [None]},
        {"pavo": 8, "quinoa": 0},
    )
    quinoa = _by_name(report)["quinoa"]
    assert quinoa.status == ADDED_NOT_IN_CART
    assert quinoa.message == "the final cart holds 0 of 2"
    assert [o.item.comida for o in report.missing] == ["quinoa"]
    assert [i.comida for i in report.added] == ["pavo"]  # the purchase log only gets what is in the cart
    assert code == 1
    summary = capsys.readouterr().out
    assert "❌ [fake] quinoa ×2 — added_not_in_cart: the final cart holds 0 of 2" in summary
    assert "🚨 RUN INCOMPLETE — 1 item(s) are NOT in the cart" in summary
    assert "quinoa" in notifier.sent[0]


def test_a_failed_add_the_cart_holds_in_full_counts_as_verified(run):
    _code, report, _store, _notifier = run(
        [_item("leche")],
        {"leche": [AddToCartFailed(_item("leche"), "stuck"), AddToCartFailed(_item("leche"), "stuck")]},
        {"leche": 1},
    )
    leche = _by_name(report)["leche"]
    assert leche.status == ADDED_VERIFIED and leche.retries == 1
    assert "stuck" in leche.message


def test_an_unreadable_cart_leaves_adds_unconfirmed_never_missing(run, capsys):
    code, report, _store, notifier = run(
        [_item("pan")], {"pan": [None]}, RuntimeError("cart API changed"),
    )
    assert _by_name(report)["pan"].status == ADDED_UNVERIFIED
    assert report.missing == []
    assert code == run_automation.EXIT_UNCONFIRMED
    assert "NOT confirmed" in capsys.readouterr().out
    assert "❔ 1 not confirmed" in notifier.sent[0]


def test_a_dry_run_adds_nothing_and_sends_nothing(run):
    code, report, store, notifier = run([_item("pan")], {"pan": [None]}, {}, argv=["--dry-run"])
    assert store.calls == [] and notifier.sent == [] and code == 0
    assert report.added == [] and report.missing == []


def test_mercadona_cart_lines_parse_and_refuse_an_unknown_shape():
    assert mercadona.cart_key("https://tienda.mercadona.es/product/19897/azucar-blanco") == "19897"
    cart = {"lines": [{"product_id": "19897", "quantity": 2}, {"product": {"id": "2794"}, "quantity": 1.0}]}
    assert mercadona.cart_lines_from_json(cart) == {"19897": 2.0, "2794": 1.0}
    assert mercadona.cart_lines_from_json({"lines": []}) == {}
    with pytest.raises(ValueError):
        mercadona.cart_lines_from_json({"lines": [{"sku": "x", "quantity": 1}]})
    with pytest.raises(ValueError):
        mercadona.cart_lines_from_json({"items": []})
