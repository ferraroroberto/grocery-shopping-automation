"""Run-summary dataclass for cart-automation runs."""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field, replace
from typing import Optional

from automation.models import CartItem

# Per-item outcome statuses (issue #247). Only the store's own final cart
# decides "in cart": the handler's "added" is provisional until verified.
ADDED_VERIFIED = "added_verified"
# The handler added it but the store cart could not be read — not confirmed.
ADDED_UNVERIFIED = "added_unverified"
ADDED_NOT_IN_CART = "added_not_in_cart"
# Some units are in the cart, fewer than wanted — a stock cap (issue #283).
PARTIAL_IN_CART = "partial_in_cart"
OUT_OF_STOCK = "out_of_stock"
UNAVAILABLE = "unavailable"
NO_URL = "no_url"
ERROR = "error"
# Dry run only — nothing was added.
WOULD_ADD = "would_add"
# Fallback only — the run could not try it (a single-store run, no handler, ...).
NOT_TRIED = "not_tried"

_IN_CART = (ADDED_VERIFIED, ADDED_UNVERIFIED)


@dataclass
class FallbackOutcome:
    """What the item's fallback supplied (issue #283).

    Attributes:
        store: Store key the fallback was (to be) bought at.
        url: The fallback's product URL.
        wanted: Units the primary was short — what the fallback was asked for.
        status: One of the status constants, for the fallback's own add;
            ``NOT_TRIED`` until (or unless) the run tries it.
        message: The reason for a fallback miss.
        cart_qty: Units the fallback's cart line holds after the add; ``None``
            when that cart could not be read.
        units: Units this item got at the fallback — read from the cart, or
            the add's own report when the cart is unreadable.
    """

    store: str
    url: str
    wanted: int
    status: str = NOT_TRIED
    message: str = ""
    cart_qty: Optional[float] = None
    units: float = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ItemOutcome:
    """What happened to one intended item.

    Attributes:
        item: The list item the run tried to put in the cart.
        status: One of the module-level status constants.
        message: The reason for a miss, or a note on a verified item.
        retries: How many extra add attempts were made (0 or 1).
        cart_qty: Units the store's final cart holds; ``None`` when unread.
        suggestion: A replacement proposed for an unavailable item.
        fallback: What the item's declared fallback supplied; ``None`` when the
            primary was not short or the row declares no fallback.
    """

    item: CartItem
    status: str
    message: str = ""
    retries: int = 0
    cart_qty: Optional[float] = None
    suggestion: str = ""
    fallback: Optional[FallbackOutcome] = None

    @property
    def shortfall(self) -> Optional[int]:
        """Units the primary is short of ``comprar``; ``None`` when unknown.

        From the cart when it was read (whatever the add reported); else zero
        for an add that reported success, the whole quantity for an item the
        store could not sell, and unknown for a failed add.
        """
        if self.cart_qty is not None:
            return max(0, math.ceil(self.item.comprar - self.cart_qty))
        if self.status in (OUT_OF_STOCK, UNAVAILABLE, NO_URL):
            return self.item.comprar
        if self.status == ADDED_UNVERIFIED:
            return 0
        return None

    @property
    def primary_units(self) -> float:
        """Units of ``comprar`` the primary product supplied (capped at ``comprar``)."""
        if self.cart_qty is not None:
            return min(self.cart_qty, self.item.comprar)
        if self.status in _IN_CART and self.fallback is None:
            return self.item.comprar
        return 0

    @property
    def total_units(self) -> float:
        """Units supplied by the primary and the fallback together."""
        return self.primary_units + (self.fallback.units if self.fallback else 0)

    def to_dict(self) -> dict:
        return {
            "comida": self.item.comida,
            "comprar": self.item.comprar,
            "buscador": self.item.buscador,
            "status": self.status,
            "message": self.message,
            "retries": self.retries,
            "cart_qty": self.cart_qty,
            "suggestion": self.suggestion,
            "fallback": self.fallback.to_dict() if self.fallback else None,
        }


@dataclass
class RunReport:
    """Outcome of a cart-automation run, aggregated for a final summary.

    Attributes:
        outcomes: One :class:`ItemOutcome` per intended item, in run order —
            the single source for every list below.
        mode: Cart mode for the run — ``"keep"`` (additive, leave existing
            cart contents) or ``"clean"`` (empty the cart first, then fill).
        dry_run: True when the run was a dry-run (no browser opened); the
            per-store cart totals are then unavailable.
        cart_before: Store key → total cart units read before processing that
            store (for ``clean``, this is the count *before* the cart was
            emptied). Empty in a dry-run.
        cart_after: Store key → total cart units read after processing that
            store. Empty in a dry-run.
        cart_lines: Store key → the final cart read for verification
            (cart key → units), or ``None`` when it could not be read.
        pending_fallbacks: Outcomes whose fallback is at another store, waiting
            for the run's cross-store pass. Not part of the run record.
    """

    outcomes: list[ItemOutcome] = field(default_factory=list)
    mode: str = "keep"
    dry_run: bool = False
    cart_before: dict[str, int] = field(default_factory=dict)
    cart_after: dict[str, int] = field(default_factory=dict)
    cart_lines: dict[str, Optional[dict[str, float]]] = field(default_factory=dict)
    pending_fallbacks: list[ItemOutcome] = field(default_factory=list)

    def record(self, item: CartItem, status: str, message: str = "") -> ItemOutcome:
        """Append and return a new outcome for ``item``."""
        outcome = ItemOutcome(item, status, message)
        self.outcomes.append(outcome)
        return outcome

    @property
    def added(self) -> list[CartItem]:
        """Items in the cart — verified, or added where the cart was unreadable."""
        return [o.item for o in self.outcomes if o.status in _IN_CART]

    @property
    def purchased(self) -> list[CartItem]:
        """What the run put in a cart, as purchase-log lines.

        The primary's units under its own store and URL, a fallback's under the
        store and URL it was bought at, so the order-email reconciliation and
        later runs read them where they really are.
        """
        out: list[CartItem] = []
        for o in self.outcomes:
            primary = int(o.primary_units)
            if primary > 0 and o.item.buscador:
                out.append(replace(o.item, comprar=primary, fallback=None))
            fb = o.fallback
            if fb is not None and int(fb.units) > 0:
                out.append(CartItem(fb.store, o.item.comida, int(fb.units), fb.url))
        return out

    @property
    def unconfirmed(self) -> list[ItemOutcome]:
        """Adds the store cart could not confirm (no cart reader, or the read failed)."""
        return self._of(ADDED_UNVERIFIED)

    @property
    def missing(self) -> list[ItemOutcome]:
        """Every outcome that did not end in the cart (dry-run "would add" excluded)."""
        return [o for o in self.outcomes if o.status not in (*_IN_CART, WOULD_ADD)]

    def _of(self, status: str) -> list[ItemOutcome]:
        return [o for o in self.outcomes if o.status == status]

    def summary_text(self) -> str:
        """A compact, emoji-tagged summary; a run with misses ends on a loud banner."""
        out = ["", "── Cart automation summary ──"]
        if self.dry_run:
            would = self._of(WOULD_ADD)
            out.append(f"🔎 Would add:         {len(would)}")
            out += [f"   🔎 [{o.item.super_name}] {o.item.comida} ×{o.item.comprar}" for o in would]
        verified = self._of(ADDED_VERIFIED)
        if not self.dry_run:
            out.append(f"✅ In cart (verified): {len(verified)}")
            out += [f"   ✅ {in_cart_line(o)}" for o in verified]
        unverified = self.unconfirmed
        if unverified:
            out.append(f"❔ Added, not confirmed (cart unreadable): {len(unverified)}")
            out += [f"   ❔ {in_cart_line(o)}" for o in unverified]
        missing = self.missing
        out.append(f"❌ NOT in cart:       {len(missing)}")
        out += [f"   ❌ {miss_line(o)}" for o in missing]
        out += self._cart_delta_lines()
        if missing:
            out.append(
                f"🚨 RUN INCOMPLETE — {len(missing)} item(s) are NOT in the cart. "
                "Sort them out before checkout."
            )
        elif unverified:
            out.append(
                f"❔ {len(unverified)} item(s) added but NOT confirmed — the store cart "
                "could not be read. Check them before checkout."
            )
        elif not self.dry_run:
            out.append(f"🟢 All {len(verified)} item(s) verified in the cart.")
        out.append("─────────────────────────────")
        return "\n".join(out)

    def print_summary(self) -> str:
        """Write :meth:`summary_text` to stdout and return it."""
        text = self.summary_text()
        print(text)
        return text

    def _cart_delta_lines(self) -> list[str]:
        """The cart mode and, per store, the before/after total + delta.

        In ``keep`` mode the automation-added delta is ``after - before``
        (everything that was already in the cart survives). In ``clean`` mode
        the cart is emptied first, so the units the automation added equal the
        final total — ``before`` is reported only to show what was wiped.
        """
        out = [f"🛒 Cart mode:         {self.mode}"]
        if self.dry_run:
            out.append("   (dry run — browser not opened, cart totals not measured)")
            return out
        for store, before in self.cart_before.items():
            after = self.cart_after.get(store)
            if after is None:
                continue
            if self.mode == "clean":
                out.append(f"   🛒 {store}: cart {before} → {after} (cleared first; automation +{after})")
            else:
                out.append(f"   🛒 {store}: cart {before} → {after} (automation +{after - before})")
        return out


def in_cart_line(outcome: ItemOutcome) -> str:
    """One line for an item in the cart; a fallback that filled the gap is named."""
    item = outcome.item
    text = f"[{item.super_name}] {item.comida} ×{item.comprar}"
    return f"{text} — {outcome.message}" if outcome.fallback and outcome.message else text


def miss_line(outcome: ItemOutcome) -> str:
    """One line for a missing item: store, name, reason, retry and suggestion.

    A partial add, or any item a fallback was asked to fill, reads
    "could add only N of M" with the primary/fallback split.
    """
    item = outcome.item
    text = f"[{item.super_name}] {item.comida} ×{item.comprar} — "
    fb = outcome.fallback
    if fb is not None or outcome.status == PARTIAL_IN_CART:
        text += f"could add only {outcome.total_units:g} of {item.comprar}"
        if fb is not None:
            text += f" (primary {outcome.primary_units:g}, fallback {fb.units:g})"
    else:
        text += outcome.status
    if outcome.message:
        text += f": {outcome.message}"
    if outcome.retries:
        text += f" (after {outcome.retries} retry)"
    if outcome.suggestion:
        text += f" → try {outcome.suggestion}"
    return text
