"""Run-summary dataclass for cart-automation runs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from automation.models import CartItem

# Per-item outcome statuses (issue #247). Only the store's own final cart
# decides "in cart": the handler's "added" is provisional until verified.
ADDED_VERIFIED = "added_verified"
# The handler added it but the store cart could not be read — not confirmed.
ADDED_UNVERIFIED = "added_unverified"
ADDED_NOT_IN_CART = "added_not_in_cart"
OUT_OF_STOCK = "out_of_stock"
UNAVAILABLE = "unavailable"
NO_URL = "no_url"
ERROR = "error"
# Dry run only — nothing was added.
WOULD_ADD = "would_add"

_IN_CART = (ADDED_VERIFIED, ADDED_UNVERIFIED)


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
    """

    item: CartItem
    status: str
    message: str = ""
    retries: int = 0
    cart_qty: Optional[float] = None
    suggestion: str = ""

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
    """

    outcomes: list[ItemOutcome] = field(default_factory=list)
    mode: str = "keep"
    dry_run: bool = False
    cart_before: dict[str, int] = field(default_factory=dict)
    cart_after: dict[str, int] = field(default_factory=dict)
    cart_lines: dict[str, Optional[dict[str, float]]] = field(default_factory=dict)

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
            out += [f"   ✅ [{o.item.super_name}] {o.item.comida} ×{o.item.comprar}" for o in verified]
        unverified = self.unconfirmed
        if unverified:
            out.append(f"❔ Added, not confirmed (cart unreadable): {len(unverified)}")
            out += [f"   ❔ [{o.item.super_name}] {o.item.comida} ×{o.item.comprar}" for o in unverified]
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


def miss_line(outcome: ItemOutcome) -> str:
    """One line for a missing item: store, name, reason, retry and suggestion."""
    item = outcome.item
    text = f"[{item.super_name}] {item.comida} ×{item.comprar} — {outcome.status}"
    if outcome.message:
        text += f": {outcome.message}"
    if outcome.retries:
        text += f" (after {outcome.retries} retry)"
    if outcome.suggestion:
        text += f" → try {outcome.suggestion}"
    return text
