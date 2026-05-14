"""Shared dataclasses for the browser-automation package."""

from __future__ import annotations

from dataclasses import dataclass


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
    """

    super_name: str
    comida: str
    comprar: int
    buscador: str
