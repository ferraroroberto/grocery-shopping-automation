"""Run-summary dataclass for cart-automation runs."""

from __future__ import annotations

from dataclasses import dataclass, field

from automation.models import CartItem


@dataclass
class RunReport:
    """Outcome of a cart-automation run, aggregated for a final summary.

    Attributes:
        added: Items successfully added to the cart.
        skipped_no_url: Items skipped because they had no usable product URL.
        unavailable: ``(item, message)`` pairs for items whose product page
            rendered no product at all — a likely stale/discontinued URL to
            fix on the data side. Surfaced as an alert, not a hard error.
        errors: ``(item, message)`` pairs for items that failed to be added.
        mode: Cart mode for the run — ``"keep"`` (additive, leave existing
            cart contents) or ``"clean"`` (empty the cart first, then fill).
        dry_run: True when the run was a dry-run (no browser opened); the
            per-store cart totals are then unavailable.
        cart_before: Store key → total cart units read before processing that
            store (for ``clean``, this is the count *before* the cart was
            emptied). Empty in a dry-run.
        cart_after: Store key → total cart units read after processing that
            store. Empty in a dry-run.
    """

    added: list[CartItem] = field(default_factory=list)
    skipped_no_url: list[CartItem] = field(default_factory=list)
    unavailable: list[tuple[CartItem, str]] = field(default_factory=list)
    errors: list[tuple[CartItem, str]] = field(default_factory=list)
    mode: str = "keep"
    dry_run: bool = False
    cart_before: dict[str, int] = field(default_factory=dict)
    cart_after: dict[str, int] = field(default_factory=dict)

    def print_summary(self) -> None:
        """Write a compact, emoji-tagged summary to stdout."""
        print("\n── Cart automation summary ──")
        print(f"✅ Added:             {len(self.added)}")
        for item in self.added:
            print(f"   ✅ {item.comida} ×{item.comprar}")
        print(f"⚠️  Skipped (no URL):  {len(self.skipped_no_url)}")
        for item in self.skipped_no_url:
            print(f"   ⚠️  {item.comida}")
        print(f"🔗 Unavailable (check URL): {len(self.unavailable)}")
        for item, message in self.unavailable:
            print(f"   🔗 {item.comida}: {message}")
        print(f"❌ Errors:            {len(self.errors)}")
        for item, message in self.errors:
            print(f"   ❌ {item.comida}: {message}")
        self._print_cart_deltas()
        print("─────────────────────────────")

    def _print_cart_deltas(self) -> None:
        """Print the cart mode and, per store, the before/after total + delta.

        In ``keep`` mode the automation-added delta is ``after - before``
        (everything that was already in the cart survives). In ``clean`` mode
        the cart is emptied first, so the units the automation added equal the
        final total — ``before`` is reported only to show what was wiped.
        """
        print(f"🛒 Cart mode:         {self.mode}")
        if self.dry_run:
            print("   (dry run — browser not opened, cart totals not measured)")
            return
        for store, before in self.cart_before.items():
            after = self.cart_after.get(store)
            if after is None:
                continue
            if self.mode == "clean":
                print(
                    f"   🛒 {store}: cart {before} → {after} "
                    f"(cleared first; automation +{after})"
                )
            else:
                print(
                    f"   🛒 {store}: cart {before} → {after} "
                    f"(automation +{after - before})"
                )
