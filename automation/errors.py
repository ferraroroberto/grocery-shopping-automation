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
