# Ametller verification, take three — orderForm + `productId`

**Date:** 2026-05-15
**Issue:** #10 (minicart drawer is an unreliable verification source)
**PR:** #11
**Follow-up to:** `docs/2026-05-14-browser-automation-build.md` §5 and `docs/2026-05-14-ametller-robustness.md`

Issue #8 confidently misdiagnosed the Ametller failures as discontinued SKUs.
Issue #10 fixed the *real* defect — but only after two more wrong turns. This
is the retrospective for getting from "10/17 with confident false alerts" to
"15/17 clean, 2 honest alerts, 1m34s end-to-end".

---

## 1. What #8 got wrong

The "frozen AGREGADO" alert from #8 was *consistent* — the same 5 items
flagged every run. That consistency made the diagnosis feel right: stale SKUs
the storefront has half-removed.

Then the operator opened the live checkout cart and showed me reality:

```
zanahoria        ×14   (wanted 1)
jamón cocido     ×16   (wanted 2)
bases pizza      ×24   (wanted 3)
tomate Mutti     ×8    (wanted 1)
mozzarella       ×32   (wanted 4)
```

Every "failed" attempt had been a real successful add. The retry loop (3× per
attempt × multiple runs) had compounded the damage line by line. The product
page wasn't frozen — it was correctly showing "AGREGADO" because the items
*were* in the cart. The handler just couldn't see them.

**Lesson:** consistency is not validation. Five items failing the same way
across five runs is consistent with *the verification being consistently
wrong*, not with the items being broken.

The operator's pointer was the unblock: the checkout cart page  
`https://www.ametllerorigen.com/es/checkout-io/cart` (reachable from the
minicart's "IR AL CHECKOUT" button) is the storefront's own source of truth.
Underneath it, VTEX exposes `/api/checkout/pub/orderForm` as JSON.

---

## 2. The minicart drawer was lying

The drawer reading worked for *some* items, not others. The lines it returned
matched real cart lines; lines it omitted were also real cart lines. No
pattern from selectors alone explained which ones. The DOM-based reader was
just unreliable — likely the drawer renders a subset by some internal rule
(virtual list / pagination / lazy hydration) and a flat `querySelectorAll`
misses the rest.

**Decision:** drop minicart-drawer reading entirely, use VTEX's own JSON cart.
The drawer code, the `_drawer_open` polling, the `_open_minicart` /
`_close_minicart` retry helpers — all gone. The `_dismiss_overlays` helper
stayed but simplified: just Escape any stray modal that isn't the postal-code
prompt.

```python
def _cart_items_via_orderform(page):
    resp = page.request.get(_ORDERFORM_URL, timeout=15000)
    data = resp.json()
    return [
        {"productId": str(it["productId"]),
         "refId":     str(it["refId"]),
         "name":      str(it["name"]),
         "qty":       int(it["quantity"])}
        for it in data.get("items", [])
    ]
```

`page.request.get` rides the live session cookies — no extra navigation, no
DOM scraping. ~80 ms per call.

---

## 3. The first orderForm attempt — wrong join key

Switched to orderForm matching by name. Ran live. **9/17 errors.** Same
familiar story: "the cart shows 0 for X, expected N, after 2 attempts."

Probed the orderForm right after the run. The cart did have the items — just
under different names:

```
Page h1                                       orderForm item.name
─────────────────────────────────────────     ──────────────────────────────────
Pechuga de pollo fileteado Ametller Origen ➜ Pit de pollastre filetejat Ametller Origen
Zanahoria                                  ➜ Pastanaga
Cebolla cocina                             ➜ Cebolla cocina            (same)
Calabacín extra                            ➜ Carbassó extra
Berenjena extra                            ➜ Berenjena extra           (same)
```

The Spanish storefront (`/es/...`) returns Spanish names in the page h1; the
orderForm returns the canonical (Catalan) line names from the catalog. Items
that happened to share a Spanish↔Catalan name verified; the rest fell through
the name match — and a retry double-added them.

**Lesson:** the moment you can match on an ID, do not match on a string.
"They were equal last time we checked" is the famous last word of every
internationalised system.

VTEX gives every product a numeric `productId`. It appears in the orderForm
(`items[].productId`) and on the product page in a meta tag exposed for
retailer integrations:

```html
<meta property="product:retailer_part_no" content="7777">
```

(VTEX also exposes it in `window.__STATE__["Product:<slug>"].productId`, but
the meta tag is dead-simple and just as stable.)

Switching the join key from `name` to `productId` made every item match
across both languages — confirmed live for the 14 then-in-cart items.

---

## 4. The second orderForm attempt — propagation lag

With productId matching in, ran live again. Most items now correctly
*skipped* via idempotency (cart already had inflated quantities from the
buggy earlier runs — see §6) and didn't double-add. But a clean add (pulpo
cocido onto a line that didn't exist yet) still occasionally read back 0
right after the click and triggered the retry.

VTEX's mutation propagation is fast but not instant: the response to
`addToCart` comes back, but `/api/checkout/pub/orderForm` can briefly return
the pre-mutation snapshot. Reading it once after a hard-coded sleep is a
coin flip.

**Fix:** poll instead. Read up to `_CART_POLL_COUNT` times with
`_CART_POLL_INTERVAL_S` between reads, returning as soon as `qty >= target`.
Successful adds resolve in under a second; a genuine miss still gets the
full wait before the retry decides the add really didn't land. The retry
budget dropped from `_MAX_ADD_ATTEMPTS = 3` to `2` because polling absorbs
the failure shape the higher count was originally compensating for.

```python
def _cart_qty_settled(page, product_id, *, target):
    qty = _cart_qty_by_id(page, product_id)
    if qty >= target:
        return qty
    for _ in range(_CART_POLL_COUNT):
        time.sleep(_CART_POLL_INTERVAL_S)
        qty = _cart_qty_by_id(page, product_id)
        if qty >= target:
            return qty
    return qty
```

---

## 5. Validation — full live Ametller run, 1m34s, exit 0

```
✅ Added:        15   (14 idempotent skips + 1 fresh add of pulpo cocido)
🔗 Unavailable:   2   huevos, manzana — page renders an empty shell
❌ Errors:        0
exit code 0
```

- The 14 lines already at-or-above target were correctly *left alone* — no
  more double-adds.
- The one item that genuinely needed adding (pulpo cocido) landed on attempt
  1/2 — the poll caught its qty=1 inside the first second after the click.
- The 2 empty-shell URLs (huevos, manzana) are honest data alerts — the
  product page really does render with no title/button/stepper.
- Total wall-clock: **1 min 34 s** — vs. ~8 min before, because idempotency
  skips are an `orderForm` GET (~80 ms) instead of an `open-drawer + scrape +
  close-drawer` cycle. Speed is the side-effect of correctness.

`py_compile` clean; `automation_smoke_dryrun` passes.

---

## 6. The damage left behind

This is the price of the earlier bugs: the cart held inflated quantities for
14 items by the time #10 was fixed. The new handler does the right thing —
the idempotency contract is "never reduce a line that already has more than
the shopping list asks for," so re-runs will leave the inflation alone — but
the operator has to delete the extras by hand once.

Example: `burguer ternera` wanted 2, cart now holds 4. Operator removes 2
units via the checkout cart, then re-runs to confirm the line settles at 2.

The handler logs every "already N in cart (≥ M wanted)" line, so the
discrepancy is visible at a glance.

---

## 7. If a verification "feels off" next time

1. **Compare against an independent source.** The drawer is "the cart" only
   in the same sense the keyboard is "the document": it's a UI of the truth,
   not the truth itself. Read the truth.
2. **Watch the cart grow.** When the count keeps climbing across runs of an
   idempotent handler, the bug is in *verification*, not in the action.
3. **Match by ID, not by string** — especially on internationalised stores.
4. **Poll instead of sleeping.** A hard-coded wait is correct exactly once;
   a poll with early return is correct always and free when it lands fast.
