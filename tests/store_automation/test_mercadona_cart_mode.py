"""Mercadona `add_to_cart` keep-mode semantics (issue #339).

`--cart-mode keep` ("add the list on top of whatever is already in the
cart", `automation/README.md`) must never shrink a line that already holds
more than the list wants — Ametller and Carrefour already refuse to do this
(`add_to_cart`'s own "already … in cart, leaving as is" early return in each).
Mercadona's `_set_quantity` used to decrement a line down to `item.comprar`
instead, which is the bug this guards: a cart someone filled by hand to 4
units, with the list wanting only 1, lost 3 units on the next run.
"""

from automation import mercadona
from automation.models import CartItem

URL = "https://tienda.mercadona.es/product/12345/fixture"


class FakeLocator:
    def __init__(self, count, text="", on_click=None):
        self._count = count
        self._text = text
        self._on_click = on_click
        self.clicks = 0

    def count(self) -> int:
        return self._count

    @property
    def first(self) -> "FakeLocator":
        return self

    def is_visible(self) -> bool:
        return True

    def inner_text(self) -> str:
        return self._text() if callable(self._text) else self._text

    def click(self) -> None:
        self.clicks += 1
        if self._on_click:
            self._on_click()


class FakePage:
    """Mercadona's product page, with the product already in the cart at
    `cart_qty` units (the +/- picker shown, "Añadir al carro" hidden)."""

    def __init__(self, cart_qty: int, has_add: bool = False):
        self.cart_qty = cart_qty
        self.cart_badge = cart_qty
        self._has_add = has_add
        self.increase = FakeLocator(count=1, on_click=self._bump(1))
        self.decrease = FakeLocator(count=1, on_click=self._bump(-1))

    def _bump(self, delta: int):
        def _click() -> None:
            self.cart_qty += delta
            self.cart_badge += delta
        return _click

    def locator(self, selector: str) -> FakeLocator:
        S = mercadona.SELECTORS
        if selector == S["product_info"]:
            return FakeLocator(count=1)
        if selector == S["add_button"]:
            return FakeLocator(count=1 if self._has_add else 0, on_click=self._bump(1))
        if selector == S["increase"]:
            return self.increase
        if selector == S["decrease"]:
            return self.decrease
        if selector == S["product_qty"]:
            return FakeLocator(count=1, text=lambda: f"{self.cart_qty} ud.")
        if selector == S["cart_badge"]:
            return FakeLocator(count=1, text=lambda: f"{self.cart_badge} ud.")
        if selector == S["unsaved_modal_dismiss"]:
            return FakeLocator(count=0)
        raise KeyError(selector)


def _patch_browser(monkeypatch):
    monkeypatch.setattr(mercadona, "goto_with_login_check", lambda *a, **k: None)
    monkeypatch.setattr(mercadona, "human_delay", lambda *a, **k: None)


def test_keep_mode_never_shrinks_a_line_already_above_target(monkeypatch):
    _patch_browser(monkeypatch)
    item = CartItem("mercadona", "fixture item", 1, URL)  # list wants 1
    page = FakePage(cart_qty=4)  # hand-filled cart already holds 4

    mercadona.add_to_cart(page, item)

    assert page.decrease.clicks == 0
    assert page.increase.clicks == 0
    assert page.cart_qty == 4  # left exactly as the human filled it


def test_keep_mode_tops_up_a_line_below_target(monkeypatch):
    _patch_browser(monkeypatch)
    item = CartItem("mercadona", "fixture item", 3, URL)  # list wants 3
    page = FakePage(cart_qty=0, has_add=True)  # not yet in the cart

    mercadona.add_to_cart(page, item)

    assert page.decrease.clicks == 0
    assert page.cart_qty == 3
