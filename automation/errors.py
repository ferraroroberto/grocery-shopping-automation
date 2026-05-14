"""Shared exception types raised by the store handlers.

`SessionExpiredError` and `ProfileNotInitializedError` live in
:mod:`automation.browser` (they are about the browser session, not a cart
item). The exceptions here are about a specific :class:`CartItem` failing to
make it into the cart.
"""

from __future__ import annotations

from automation.models import CartItem


class OutOfStockError(RuntimeError):
    """Raised when a product page shows the item as unavailable."""

    def __init__(self, item: CartItem) -> None:
        super().__init__(f"'{item.comida}' is out of stock")
        self.item = item


class AddToCartFailed(RuntimeError):
    """Raised when an item could not be added to the cart at the wanted quantity.

    Covers DOM/selector failures, clicks that never registered, and post-action
    verification mismatches (the cart counter did not move as expected).
    """

    def __init__(self, item: CartItem, reason: str) -> None:
        super().__init__(f"failed to add '{item.comida}': {reason}")
        self.item = item
        self.reason = reason


class ProductUnavailableError(RuntimeError):
    """Raised when a product page renders no product at all.

    Distinct from :class:`OutOfStockError` (the page renders fine but the item
    is sold out) and :class:`AddToCartFailed` (the add itself failed): here the
    ``/p`` URL serves an empty shell with no title, no add control and no
    stepper — almost always a stale or discontinued URL in the inventory sheet.
    The runner records these as an end-of-run *alert* to fix on the data side,
    not as a hard error, so a stale URL never fails the whole run.
    """

    def __init__(self, item: CartItem, reason: str) -> None:
        super().__init__(f"'{item.comida}' unavailable: {reason}")
        self.item = item
        self.reason = reason
