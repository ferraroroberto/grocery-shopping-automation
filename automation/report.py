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
    """

    added: list[CartItem] = field(default_factory=list)
    skipped_no_url: list[CartItem] = field(default_factory=list)
    unavailable: list[tuple[CartItem, str]] = field(default_factory=list)
    errors: list[tuple[CartItem, str]] = field(default_factory=list)

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
        print("─────────────────────────────")
