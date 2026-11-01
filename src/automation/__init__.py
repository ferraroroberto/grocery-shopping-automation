"""Browser-automation package: Playwright-driven cart filling for grocery stores.

Store-agnostic plumbing only — the persistent Chrome-profile factory, the
grocery-list reader, and shared dataclasses. Store-specific selectors and
add-to-cart logic live in their own modules (see issues #2 / #3).

This package stays thin on purpose: it imports no store handlers.
"""
