# Ametller handler rewrite — VTEX is gone, the site is Salesforce Commerce Cloud now

**Date:** 2026-05-20
**Issue:** #12 (Ametller migrated VTEX → Salesforce Commerce Cloud)
**Follow-up to:** `docs/2026-05-15-ametller-orderform-verify.md`

Five days after the orderForm verification was declared "15/17 clean", a live
run reported **0/17** Ametller items added — every one a `🔗 Unavailable
(check URL)` alert: "product page rendered no title, add button or stepper".
Mercadona ran 25/25 clean in the same session.

---

## 1. It wasn't the URLs — the whole platform changed

The failure shape (all 17, identical message) ruled out stale individual SKUs:
that is a *systematic* break, not 17 coincidental discontinuations. A read-only
probe of a product page confirmed it:

- The page renders on **Chakra UI** (`chakra-heading`, `chakra-button`,
  `chakra-numberinput__field`). None of the VTEX selectors matched.
- The legacy `/p` URL **301-redirects** to `…/{productId}.html` — a new
  ID-based scheme.
- Network traffic is **SCAPI / OCAPI** (`/mobify/proxy/api/...shopper-products`,
  `shopper-baskets`, `shopper-customers`). The VTEX `orderForm` endpoint and the
  `<meta property="product:retailer_part_no">` tag are both gone.
- The cart page loaded as `?guest=true` — the saved Chrome profile session was
  not recognised by the new site.

Ametller Origen had migrated its entire storefront from VTEX to Salesforce
Commerce Cloud (the PWA Kit "Composable Storefront"). The handler from #10/#11
was reading a DOM and an API that no longer exist.

---

## 2. The rewrite

`automation/ametller.py` was rewritten end to end. The *shape* of the old
handler survived — navigate, idempotency-check, set quantity, click add, verify
against an authoritative JSON source, poll for propagation lag — because that
shape was right. Only the platform underneath it changed.

- **Add flow:** set the Chakra quantity stepper (`input.chakra-numberinput__field`),
  click the main "AÑADIR" button (first in DOM order — the recommendation
  carousel's per-card buttons come after).
- **Verification:** the **SCAPI Shopper Baskets** API
  (`shopper-customers/v1/.../customers/{id}/baskets`), called with the SLAS
  shopper token the storefront stashes in `localStorage`
  (`access_token_ametller` / `customer_id_ametller`). This is the new
  equivalent of the orderForm — the storefront's own source of truth.
- **Line matching:** by numeric `productId`, parsed from the redirected
  `/{productId}.html` URL and compared against `productItems[].productId`.
  Still an ID, never a string (the §3 lesson from #10 holds).
- **Session check:** `customer_type_ametller` in `localStorage` is
  `"registered"` for a logged-in shopper. Anything else means the profile has
  lapsed to a guest → `SessionExpiredError`.
- **Selectors:** Chakra component classes, ARIA labels and visible button text
  only — never the Emotion `css-*` hashes, which are regenerated on every
  deploy.

The legacy `/p` URLs redirect cleanly for all 17 items, so **no inventory
spreadsheet change was needed**. The `automation.ametller_postal_code` config
key is no longer read — the new site carries delivery settings in the profile.

---

## 3. The bot tell — `--enable-automation`

While validating, the operator spotted Chrome's "automated test software is
controlling Chrome" infobar. Playwright adds `--enable-automation` to the
launch by default; that switch draws the infobar and is a trivial bot
fingerprint. `automation/browser.py` now passes
`ignore_default_args=["--enable-automation"]`, so the window presents as a
normal browser (`navigator.webdriver` was already `false` via
`--disable-blink-features=AutomationControlled`).

---

## 4. Streamlit — a Refresh button

`app/app.py` gained a **🔄 Refresh** button at the top of the app. It stops any
live automation run, clears all session state (the run, bought/extra marks,
every widget value) and reruns — the next pass reloads the inventory fresh from
the Excel file. The Excel file itself is never written or cleared.

---

## 5. Validation — live, against the real cart

After a fresh `bootstrap_session` login on the new site:

```
run 1  ✅ burguer ternera ×2   added, attempt 1/2
       ✅ pollo ×4             added, attempt 1/2
       ✅ salmon ×2            added, attempt 1/2
run 2  ✅ all three            "already N in cart (≥ N wanted), leaving as is"
```

The second run proves idempotency — no double-add. `py_compile` clean on all
changed files; Streamlit boot check passes.

## Files modified

- `automation/ametller.py` — full rewrite for Salesforce Commerce Cloud.
- `automation/browser.py` — drop `--enable-automation` (infobar / bot tell).
- `app/app.py` — Refresh button + `_render_top_bar`.
- `automation/README.md` — Ametller store-specific notes updated.
