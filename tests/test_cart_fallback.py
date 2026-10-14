"""Partial stock and per-item fallbacks in the cart run (issue #283).

Everything runs against fake store adapters whose cart caps each product's
line at its stock: no Chrome, no store, no cart, and a recording notifier.
"""

import json
import math

import pandas as pd
import pytest

from automation import grocery_reader, product_search, run_automation
from automation.errors import OutOfStockError, PartialStockError
from automation.models import CartItem, Fallback
from automation.report import ADDED_UNVERIFIED, ADDED_VERIFIED, NOT_TRIED, OUT_OF_STOCK, PARTIAL_IN_CART
from src.data import COLUMNS, FALLBACK_SUPER_COLUMN, FALLBACK_URL_COLUMN


class CappedStore:
    """A store adapter double whose cart holds at most ``stock[key]`` units of a product.

    ``add_to_cart`` mirrors the real handlers: it sets a line to an absolute
    total, leaves a larger line alone, raises ``OutOfStockError`` for a
    product with no stock, and at a cap either returns as if it succeeded
    (``raise_cap=False``) or raises ``PartialStockError``.
    """

    def __init__(self, stock=None, cart=None, *, raise_cap=False, readable=True):
        self.stock = stock or {}
        self.cart = dict(cart or {})
        self.raise_cap = raise_cap
        self.readable = readable
        self.calls: list[tuple[str, int]] = []
        self.pages: list[object] = []

    @staticmethod
    def cart_key(url: str) -> str:
        return url.rsplit("/", 1)[-1]

    def add_to_cart(self, page, item: CartItem) -> None:
        self.calls.append((item.comida, item.comprar))
        self.pages.append(page)
        key = self.cart_key(item.buscador)
        have = self.cart.get(key, 0)
        if have >= item.comprar:
            return
        cap = self.stock.get(key, math.inf)
        if cap == 0 and have == 0:
            raise OutOfStockError(item)
        self.cart[key] = max(have, min(item.comprar, cap))
        if self.cart[key] < item.comprar and self.raise_cap:
            raise PartialStockError(item, self.cart[key])

    def read_cart_lines(self, page) -> dict:
        if not self.readable:
            raise RuntimeError("cart API changed")
        return dict(self.cart)

    def read_cart_total(self, page) -> int:
        return int(sum(self.cart.values()))


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


def _url(store: str, product: str) -> str:
    return f"https://{store}.test/p/{product}"


def _item(store: str, name: str, qty: int, product: str, fallback=None) -> CartItem:
    fb = Fallback(*fallback) if fallback else None
    if fb is not None:
        fb = Fallback(fb.store, _url(fb.store, fb.url))
    return CartItem(store, name, qty, _url(store, product), fallback=fb)


@pytest.fixture()
def run(monkeypatch, tmp_path):
    """Run ``main`` over fake stores → a namespace of exit code, report, notifier, launches, logs."""

    def _run(items, stores, *, argv=()):
        notifier = RecordingNotifier()
        launches: list[object] = []
        written = []

        def launch(**kw):
            page = object()
            launches.append(page)
            return _Closable(), _Closable(), page

        for name, store in stores.items():
            monkeypatch.setitem(run_automation.HANDLERS, name, store)
            monkeypatch.setitem(product_search.SEARCHERS, name, lambda page, q, limit: [])
        monkeypatch.setattr(run_automation, "read_cart_items", lambda _store: items)
        monkeypatch.setattr(run_automation, "launch_context", launch)
        monkeypatch.setattr(run_automation, "human_delay", lambda *a, **k: None)
        monkeypatch.setattr(run_automation, "build_notify_notifier", lambda: notifier)
        monkeypatch.setattr(run_automation, "purchase_logs_dir", lambda: tmp_path)
        real_write = run_automation._write_purchase_log_if_live
        monkeypatch.setattr(run_automation, "_write_purchase_log_if_live",
                            lambda report, dry, text="": written.append(report) or real_write(report, dry, text))
        code = run_automation.main(list(argv))

        def purchase_log(store):
            files = list(tmp_path.glob(f"*_{store}.json"))
            if not files:
                return {}
            items_ = json.loads(files[0].read_text(encoding="utf-8"))["items"]
            return {CappedStore.cart_key(i["buscador"]): i["comprar"] for i in items_}

        class Result:
            pass

        r = Result()
        r.code, r.report, r.notifier, r.launches, r.purchase_log = code, written[0], notifier, launches, purchase_log
        r.out = {o.item.comida: o for o in written[0].outcomes}
        return r

    return _run


def test_a_partial_primary_is_filled_in_full_by_a_same_store_fallback(run):
    tienda = CappedStore(stock={"griego": 1})
    r = run([_item("tienda", "yogur", 2, "griego", fallback=("tienda", "griego-b"))], {"tienda": tienda})

    assert tienda.calls == [("yogur", 2), ("yogur (fallback)", 1)]
    yogur = r.out["yogur"]
    assert (yogur.status, yogur.cart_qty, yogur.fallback.units) == (ADDED_VERIFIED, 1, 1)
    assert yogur.message == f"1 filled at fallback tienda {_url('tienda', 'griego-b')}"
    assert len(r.launches) == 1 and r.code == 0
    assert r.notifier.sent == ["🛒 Cart run: ✅ 1 verified in cart, nothing missing"]
    assert r.purchase_log("tienda") == {"griego": 1, "griego-b": 1}


def test_a_fallback_that_is_also_short_reads_could_add_only_n_of_m(run, capsys):
    tienda = CappedStore(stock={"griego": 1, "griego-b": 0})
    r = run([_item("tienda", "yogur", 2, "griego", fallback=("tienda", "griego-b"))], {"tienda": tienda})

    yogur = r.out["yogur"]
    assert yogur.status == PARTIAL_IN_CART and yogur.fallback.status == OUT_OF_STOCK
    line = "[tienda] yogur ×2 — could add only 1 of 2 (primary 1, fallback 0)"
    summary = capsys.readouterr().out
    assert f"❌ {line}" in summary
    assert "🚨 RUN INCOMPLETE — 1 item(s) are NOT in the cart" in summary
    assert line in r.notifier.sent[0] and "❌ 1 NOT in cart" in r.notifier.sent[0]
    assert r.code == 1
    assert r.purchase_log("tienda") == {"griego": 1}  # only what landed


def test_a_total_miss_is_filled_by_a_cross_store_fallback_in_one_reopen(run):
    tienda = CappedStore(stock={"queso": 0, "pan": 0})
    otra = CappedStore()
    items = [
        _item("tienda", "queso", 2, "queso", fallback=("otra", "queso-o")),
        _item("tienda", "pan", 1, "pan", fallback=("otra", "pan-o")),
        _item("otra", "leche", 3, "leche"),
    ]
    r = run(items, {"tienda": tienda, "otra": otra})

    assert len(r.launches) == 3  # tienda, otra, then otra once more for both fallbacks
    assert otra.calls == [("leche", 3), ("queso (fallback)", 2), ("pan (fallback)", 1)]
    assert set(otra.pages[1:]) == {r.launches[2]}
    assert [r.out[n].status for n in ("queso", "pan", "leche")] == [ADDED_VERIFIED] * 3
    assert r.code == 0
    assert r.purchase_log("otra") == {"leche": 3, "queso-o": 2, "pan-o": 1}
    assert r.purchase_log("tienda") == {}


def test_the_cross_store_pass_opens_nothing_when_no_fallback_is_needed(run):
    tienda, otra = CappedStore(), CappedStore()
    items = [
        _item("tienda", "queso", 2, "queso", fallback=("otra", "queso-o")),
        _item("otra", "leche", 3, "leche"),
    ]
    r = run(items, {"tienda": tienda, "otra": otra})

    assert len(r.launches) == 2
    assert otra.calls == [("leche", 3)]
    assert r.out["queso"].fallback is None and r.code == 0


def test_a_stock_cap_the_handler_reports_is_not_retried(run, capsys):
    tienda = CappedStore(stock={"huevos": 2}, raise_cap=True)
    r = run([_item("tienda", "huevos", 3, "huevos")], {"tienda": tienda})

    assert tienda.calls == [("huevos", 3)]
    huevos = r.out["huevos"]
    assert (huevos.status, huevos.retries, huevos.cart_qty) == (PARTIAL_IN_CART, 0, 2)
    assert "❌ [tienda] huevos ×3 — could add only 2 of 3: likely a stock cap" in capsys.readouterr().out
    assert r.code == 1


def test_the_fallback_target_sits_on_top_of_units_already_in_the_cart(run):
    # The fallback product is itself a row on the list, and the cart already held one.
    tienda = CappedStore(stock={"griego": 1}, cart={"griego-b": 1})
    items = [
        _item("tienda", "yogur", 3, "griego", fallback=("tienda", "griego-b")),
        _item("tienda", "yogur b", 2, "griego-b"),
    ]
    r = run(items, {"tienda": tienda})

    assert tienda.calls == [("yogur", 3), ("yogur b", 2), ("yogur (fallback)", 4)]
    assert tienda.cart == {"griego": 1, "griego-b": 4}
    assert [r.out[n].status for n in ("yogur", "yogur b")] == [ADDED_VERIFIED, ADDED_VERIFIED]
    assert r.out["yogur"].fallback.units == 2
    assert r.purchase_log("tienda") == {"griego": 1, "griego-b": 4}  # 2 for the row + 2 for the fallback


def test_a_row_without_a_fallback_behaves_as_before(run, capsys):
    tienda = CappedStore(stock={"queso": 0})
    r = run([_item("tienda", "queso", 1, "queso")], {"tienda": tienda})

    queso = r.out["queso"]
    assert (queso.status, queso.fallback) == (OUT_OF_STOCK, None)
    assert "❌ [tienda] queso ×1 — out_of_stock: out of stock" in capsys.readouterr().out
    assert len(r.launches) == 1 and r.code == 1


def test_a_single_store_run_reports_its_cross_store_fallback_as_not_tried(run, capsys):
    tienda, otra = CappedStore(stock={"queso": 0}), CappedStore()
    items = [_item("tienda", "queso", 2, "queso", fallback=("otra", "queso-o"))]
    r = run(items, {"tienda": tienda, "otra": otra}, argv=["--store", "tienda"])

    assert len(r.launches) == 1 and otra.calls == []
    assert r.out["queso"].fallback.status == NOT_TRIED
    assert ("could add only 0 of 2 (primary 0, fallback 0): out of stock; "
            "fallback otra not_tried: this run was limited to tienda") in capsys.readouterr().out
    assert r.code == 1


def test_keep_open_reviews_a_fallback_store_only_after_the_cross_store_pass(run, monkeypatch):
    prompts: list[str] = []
    monkeypatch.setattr("builtins.input", lambda prompt="": prompts.append(prompt) or "")
    tienda, otra = CappedStore(), CappedStore()
    items = [
        _item("tienda", "queso", 2, "queso", fallback=("otra", "queso-o")),
        _item("otra", "leche", 3, "leche"),
    ]
    r = run(items, {"tienda": tienda, "otra": otra}, argv=["--keep-open"])

    # otra's own session skips its pause; it is reopened once, for the review only.
    assert len(r.launches) == 3 and otra.calls == [("leche", 3)]
    assert [p.split("close the ")[1].split(" ")[0] for p in prompts] == ["tienda", "otra"]


def test_an_unreadable_cart_leaves_a_fallback_add_not_confirmed(run):
    tienda = CappedStore(stock={"queso": 0}, readable=False)
    r = run([_item("tienda", "queso", 2, "queso", fallback=("tienda", "queso-b"))], {"tienda": tienda})

    queso = r.out["queso"]
    assert tienda.calls == [("queso", 2), ("queso (fallback)", 2)]
    assert (queso.status, queso.fallback.status, queso.fallback.units) == (ADDED_UNVERIFIED, ADDED_UNVERIFIED, 2)
    assert r.code == run_automation.EXIT_UNCONFIRMED
    assert r.purchase_log("tienda") == {"queso-b": 2}


def test_the_fallback_columns_are_optional_and_validated(monkeypatch):
    rows = {
        COLUMNS["super"]: ["Tienda", "tienda", "tienda", "tienda"],
        COLUMNS["comida"]: ["a", "b", "c", "d"],
        COLUMNS["comprar"]: [1, 1, 1, 1],
        COLUMNS["buscador"]: [_url("tienda", p) for p in "abcd"],
    }
    fallbacks = {
        FALLBACK_SUPER_COLUMN: [" Otra ", None, "otra", "tienda"],
        FALLBACK_URL_COLUMN: [_url("otra", "a2"), _url("otra", "b2"), "NOT A URL", _url("tienda", "d")],
    }
    monkeypatch.setattr(grocery_reader, "load_inventory_data", lambda: pd.DataFrame({**rows, **fallbacks}))
    got = {i.comida: i.fallback for i in grocery_reader.read_cart_items()}
    # Blank store, invalid URL, and a fallback that is the primary itself all mean no fallback.
    assert got == {"a": Fallback("otra", _url("otra", "a2")), "b": None, "c": None, "d": None}

    monkeypatch.setattr(grocery_reader, "load_inventory_data", lambda: pd.DataFrame(rows))
    assert all(i.fallback is None for i in grocery_reader.read_cart_items())
