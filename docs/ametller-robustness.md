# Ametller handler robustness — diagnosing the 7/17 add failures

**Date:** 2026-05-14
**Issue:** #8 (Ametller handler: 7/17 adds fail in a full run)
**PR:** #9
**Follow-up to:** `docs/browser-automation-build.md` §5

Issue #4's full-circle validation added 45/52 items — Mercadona 35/35, Ametller
10/17. This is the retrospective for chasing down those 7 Ametller failures.
The headline lesson: **most of the work was diagnosis, and the first two
hypotheses were both wrong.** The fix is small; getting to it was not.

---

## 1. What we thought the bug was (and why we were wrong)

Issue #8 proposed a confident root cause: a minicart drawer or modal overlay
left open by one item silently swallows the next item's "Añadir" click, so
"every item from a certain point onward fails the same way." The suggested fix
was to harden `_open/_close_minicart`, dismiss stray overlays, and retry the add.

We built exactly that:

* `_dismiss_overlays()` — close any stray drawer / VTEX modal overlay before acting.
* `_open_minicart` / `_close_minicart` — verify the drawer actually opened/closed
  (poll the `...-x-opened` / `...-x-closed` class), escalate to the Escape key.
* `add_to_cart` — a retry loop firing "Añadir" up to 3×, **reloading the product
  page between attempts** (the operator's own manual workaround) and recomputing
  the delta from the live minicart each time so a re-fire never double-adds.

Then we ran it live. **Still 10/17.** Same 7 items, same failure. The overlay
hypothesis was wrong.

**Lesson:** a plausible, well-argued root cause written from probe *fragments*
is still a hypothesis. It has to survive a live re-run before you believe it.

---

## 2. The clue in the "successes"

Re-reading the failed run's log carefully: all 10 "✅ Added" lines actually said
*"already N in cart — leaving as is."* **Zero genuine adds succeeded.** The 10
"successes" — in this run *and* in #4's validation — were always items already
in the cart from earlier manual shopping and #2/#3 tests. The Ametller *add
path had never once worked in a full run.* That reframed everything: this isn't
"one stuck item poisons the rest," it's "the add never works, full stop."

**Lesson:** count what actually happened, not what the summary line implies.
"Added: 10" hid the fact that the add count was really 0.

---

## 3. Probing the live DOM — three throwaway scripts

With the cheap hypotheses dead, we probed the real page. Three small scripts
(`tests/_probe_ametller_*.py`, not committed) each answered one question:

1. **`_probe_ametller_add.py`** — drive zanahoria step by step, dump the buy box.
   Finding: on a *fresh load*, before any click, the add button already reads
   **`AGREGADO`** ("ADDED"). Clicking it — normal click and `force=True` — does
   nothing. The stepper sets fine; the click is a no-op.
2. **`_probe_ametller_minicart.py`** — open the drawer, dump its DOM.
   Findings: (a) the open-state class is `...-x-opened`, the closed one
   `...-x-closed` — our detection string was right. (b) `_MINICART_LINES_JS`
   reads every cart line perfectly — name *and* quantity. (c) The cart held
   exactly the 10 already-there items; zanahoria et al. were **genuinely
   absent**. So it is *not* a verification false-negative — the minicart
   reading works; the add really never happens.
3. **`_probe_ametller_buybox.py`** — try every add path on zanahoria.
   Finding: add button, forced click, stepper `+`, *and* a hard reload-then-click
   — **all no-op.** The button stays frozen on `AGREGADO`; the cart stays at 10.

**Conclusion:** these product pages are stuck in a desynced state — the page
believes the item is "added," the cart disagrees, and **no client-side action
reconciles them.** It is an Ametller-/account-side stale state, almost certainly
a discontinued SKU whose `/p` URL still half-renders. It is the same illness as
the two empty-shell pages (huevos, manzana), just failing more subtly.

**Lesson:** when the cheap fixes fail, spend the time to probe the live DOM
directly. One afternoon of `page.evaluate()` dumps beat two rounds of guessing.

---

## 4. The actual fix — classify, don't crash

There is no "click harder" fix; the page cannot be driven out of this state. So
the fix matches what the defect actually is — a **data problem** (a stale URL in
the inventory sheet), not a transient automation failure:

* **New exception `ProductUnavailableError`** (`automation/errors.py`) — distinct
  from `OutOfStockError` (renders fine, sold out) and `AddToCartFailed` (a real,
  retryable add failure).
* **`RunReport.unavailable`** — its own bucket, printed as a
  `🔗 Unavailable (check URL)` section. It does **not** count toward the exit
  code: a stale URL is an *alert*, not a run failure.
* **`ametller.add_to_cart`** raises `ProductUnavailableError` for two shapes,
  both meaning "fix the URL":
  * **Empty shell** — no title, no add button, no stepper (huevos, manzana).
  * **Frozen `AGREGADO`** — the page renders but the add button is stuck on its
    "added" label while the cart stays empty, after the full retry+reload loop
    (zanahoria, jamón cocido, bases pizza, tomate Mutti, mozzarella Galbani).
* The overlay/drawer hardening and the retry+reload loop from §1 were **kept** —
  they were not the root cause here, but they are correct defensive code and
  the retry loop is what cleanly separates a *transient* miss (still an
  `AddToCartFailed`) from the *permanent* frozen state (a `ProductUnavailableError`).

The distinguishing signal is precise: after every retry is exhausted, read the
add button's label. Frozen on `AGREGADO`/`AÑADIDO` → unavailable alert.
Anything else → a genuine `AddToCartFailed`.

---

## 5. Validation — the full Ametller run, exit 0

Same 17-item Ametller list, live run:

```
✅ Added:             10   (all idempotent "already in cart" — re-run safe)
🔗 Unavailable:        7   2 empty-shell + 5 frozen-AGREGADO, every one a stale URL
❌ Errors:             0
exit code 0
```

The run no longer fails. The 7 stale URLs are surfaced as one clear, actionable
end-of-run alert telling the operator exactly which `buscador` URLs in the
inventory Excel to refresh. `py_compile` clean on all changed files; the
dry-run smoke test passes and shows the new `🔗 Unavailable` summary line.

**Remaining data-side task (not code):** refresh or replace the 7 stale Ametller
URLs in the inventory sheet — `huevos`, `manzana`, `zanahoria`, `jamón cocido
lonchas`, `bases pizza`, `tomate Mutti`, `mozzarella Galbani`. Once their `/p`
URLs point at live products, the existing add path handles them with no further
code change.

---

## 6. If a handler "fails a lot" next time

1. **Read the log, don't trust the summary.** "Added: N" can be N items that
   were already there. Separate *did the action* from *was already done*.
2. **Re-run after a fix before believing the diagnosis.** A hypothesis that
   only ever met probe fragments has not been tested.
3. **Probe the live DOM with throwaway scripts** — one question per script,
   `page.evaluate()` dumps. Name them `tests/_probe_*.py` and delete them after.
4. **Match the fix to the real defect class.** A stale URL is a data problem;
   the right code change is to *detect and report it clearly*, not to retry
   forever or crash the run.
5. **Keep defensive hardening even when it wasn't the bug** — verified drawer
   state and a retry+reload loop are correct regardless, and the loop is what
   lets you tell a transient failure apart from a permanent one.
