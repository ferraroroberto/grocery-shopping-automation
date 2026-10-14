"""Shared dataclasses for the browser-automation package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class Fallback:
    """The product to buy instead when an item's primary comes up short (issue #283).

    Attributes:
        store: Store key holding the fallback, e.g. ``"carrefour"`` (lowercase).
            May be the item's own store or another one.
        url: The fallback's product URL.
    """

    store: str
    url: str


@dataclass(frozen=True)
class CartItem:
    """One product that needs to be added to a store's online cart.

    Attributes:
        super_name: Store key, e.g. ``"mercadona"`` or ``"ametller"``.
        comida: Human-readable item label — used in logs only.
        comprar: Quantity to add to the cart (always > 0).
        buscador: Product URL. Empty string when the inventory row has no
            usable URL — some rows carry an uppercase product description
            instead of a link.
        alt_stores: Other stores this row has a ``url_<store>`` for — named in
            the run summary as a fallback when the item can't be bought here.
        fallback: Where the run sends the units the primary could not supply,
            from the row's ``fallback_super`` / ``fallback_url`` cells; ``None``
            when the row declares none. A fallback never has its own fallback.
    """

    super_name: str
    comida: str
    comprar: int
    buscador: str
    alt_stores: tuple[str, ...] = ()
    fallback: Optional[Fallback] = None
