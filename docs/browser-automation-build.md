# Browser cart automation — implementation notes and adding a new store

How the `automation/` package is structured and why. Written so the *next*
store handler is faster to add — most of the cost was discovering quirks, not
writing code. For the canonical module-by-module reference see
[`automation/README.md`](../automation/README.md).

---

## 1. The shape of the solution

```
src/                inventory Excel I/O + config (pre-existing, UI-agnostic)
automation/         the new package — UI-agnostic browser automation
  browser.py          Playwright factory: persistent Chrome profile, login check
  bootstrap_session.py  one-time interactive login (plain chrome.exe)
  grocery_reader.py   inventory XLSX -> list[CartItem]
  models.py           CartItem dataclass
  errors.py           OutOfStockError, AddToCartFailed
  mercadona.py        add_to_cart(page, item)  — store handler
  ametller.py         add_to_cart(page, item)  — store handler
  report.py           RunReport summary
  run_automation.py   CLI runner: read list -> group by store -> dispatch
app/
  automation_runner.py  subprocess plumbing (spawn, drain stdout, stop)
  shopping.py           "Run Automation" UI section + live output stream
```

The hard rule that paid off: **`automation/` never imports `streamlit`, and
`app/` never drives Playwright.** The CLI runner is the seam. The Streamlit
integration (#4) is "just" a subprocess wrapper around the exact command you
would type in a terminal — which means the terminal stays a first-class
debugging path forever.

---

## 2. Issue #1 — the thing that almost sank the project: reCAPTCHA

The original plan was the textbook one: log in once, export cookies to JSON,
re-inject them into a fresh Playwright Chromium context. **It does not work
here.** Mercadona's and Ametller's login pages are reCAPTCHA-protected and the
challenge inspects the browser: a Playwright-launched browser (even
`channel="chrome"`) trips *"Could not connect to the reCAPTCHA service"* and
the login never completes.

What worked, after iterating:

1. **Bootstrap login in a plain `chrome.exe` subprocess** — not Playwright at
   all. `subprocess.Popen([chrome, f"--user-data-dir={USER_DATA_DIR}", *urls])`.
   A normal Chrome process started with only `--user-data-dir` looks like a
   human's browser and logs in fine. The operator logs into each store, closes
   the window, presses Enter.
2. **Drive cart operations with Playwright against that same on-disk profile**
   (`launch_persistent_context(user_data_dir=..., channel="chrome")`). Cart
   pages are *not* reCAPTCHA-gated — only login is — so once the session cookie
   is in the profile, Playwright drives the carts without challenge.
3. **One shared profile for all stores** (`automation/chrome_user_data/`,
   gitignored). Decided during planning; keeps bootstrap and the model simple.

**Lesson:** the anti-bot gate is on *login*, not on *use*. Split the two. Never
let Playwright touch the login page. This single insight is the backbone of the
whole package.

A consequence to remember: a persistent profile directory **can only be opened
by one browser process at a time.** That is why the runner does stores
*sequentially* (open Mercadona context, finish, close, open Ametller) and why
"both store windows open side by side" is not possible — see #4 notes.

---

## 3. Issues #2 & #3 — two stores, two completely different DOMs

We verified every selector live and pinned them in a `SELECTORS` dict per
handler with a "verified live on <date>" note, so a DOM change is a one-line
fix and the staleness is visible.

### Mercadona (`mercadona.py`) — a React app, counts *units*

- Stable `data-testid` attributes — pleasant to target. Scope everything to
  `[data-testid='private-product-detail-info']` so the "related products"
  carousel (which reuses the same testids) is never matched.
- Two UI states: a single "Añadir al carro" button when the item is not in the
  cart, a `+`/`-` picker when it is. The handler reads which is present.
- **The critical quirk (operator-reported, then confirmed):** clicking `+`
  *sometimes silently no-ops*. The fix is `_click_until()` — click, then poll
  the on-page "N ud." count for up to ~4 s; if it did not move, click again
  (up to 3 attempts). Never trust a click; verify the counter moved.
- Mercadona counts **units**: 3 bottles of the same milk = the header badge
  goes +3. Every run verifies *both* the product's on-page unit count *and* the
  header cart badge delta before declaring success.

### Ametller Origen (`ametller.py`) — Salesforce Commerce Cloud, SCAPI verification

Ametller Origen migrated off VTEX to **Salesforce Commerce Cloud** (the
Chakra-UI "Composable Storefront") in May 2026 (issue #12).

- Quantity model: set the stepper *first*, then click "AÑADIR" once. The
  site hydrates slowly — navigation waits are deliberately generous (3–4 s).
- **Verification reads the SCAPI Shopper Baskets API** directly — not the
  minicart DOM. The storefront exposes its own basket endpoint via
  `/mobify/proxy/api`; the handler calls it with the SLAS shopper token that
  the storefront stores in `localStorage` (`access_token_ametller`). Both the
  idempotency check ("already have N") and the post-add verification ("add
  landed") use this authoritative source.
- **Lines are matched by numeric `productId`**, read from the redirected URL
  (`…/{productId}.html`). The legacy `/p` buy URLs stored in the inventory
  sheet 301-redirect there, so no inventory change was needed.
- **Session check**: the same `localStorage` entry reveals whether the session
  is still a *registered* shopper. If it has lapsed to a guest, the handler
  raises `SessionExpiredError` immediately.
- **Selectors** use Chakra component classes (`h1.chakra-heading`,
  `input.chakra-numberinput__field`) and ARIA labels — never the Emotion
  `css-*` hashes, which are regenerated on every deploy and would silently
  break the handler.
- A product page that renders an empty shell (no title, no productId) is
  reported as `ProductUnavailableError` — a run-end alert to fix the buy URL,
  not a hard failure.
- After firing "AÑADIR", the handler polls the basket a few times with a
  short delay, returning as soon as the target quantity appears. A retry (up
  to `_MAX_ADD_ATTEMPTS`) reloads the product page and re-reads the basket to
  avoid double-adding.

### Shared design decisions

- **Idempotency is mandatory.** Both handlers read the *current* cart quantity
  and only add what is missing. A re-run after a partial failure is safe; it
  never double-adds and never reduces a line that already has enough. This is
  what makes "just run it again" a valid recovery strategy.
- **Wait before believing the page.** The operator's exact words: *"if you go
  too fast maybe the page is not getting the feedback."* True for both stores.
  Verification polls with a timeout instead of checking once.
- **Errors are typed and per-item.** `OutOfStockError` / `AddToCartFailed`
  bubble to the runner, which records them in `RunReport` and *keeps going* —
  one bad item never aborts the whole run.

---

## 4. Issue #4 — surfacing it in the Streamlit app

The CLI already printed a clean ✅/⚠️/❌ summary, so #4 is a subprocess wrapper,
not new automation logic.

- `app/automation_runner.py` — `build_command()`, `start_run()` (spawns
  `python -u -m automation.run_automation`, attaches a daemon thread draining
  merged stdout/stderr into a bounded `deque`), `stop_run()` (terminate, then
  kill after a grace period). No `streamlit` import.
- `app/shopping.py` — a bordered **🤖 Run Automation** section: store
  selectbox, dry-run checkbox (default on), a live `st.code` view of the
  output, and a Stop button.

**The one real design decision:** how to stream output without trapping the
script. The audio-audit mode uses a *blocking* worker-thread loop — but a
blocking loop means a Stop button can never be clicked. So this uses a
**rerun-driven** loop instead: each Streamlit pass renders one frame of output
and calls `st.rerun()` after a 1 s sleep. The Stop button stays live because
every pass is a full, responsive script run. The subprocess and its output
deque live in `st.session_state`, so a page rerun never orphans a run, and
`app.py` shows a warning + Stop button if you switch modes mid-run.

- `-u` on the child Python is not optional — without it, the child's stdout is
  block-buffered when it is a pipe and nothing streams until the process exits.
- The subprocess inherits the repo root as `cwd` so the relative paths in
  `config.json` resolve exactly as they do in a terminal run.

**What #4 deliberately does *not* do:** leave both browser windows open side by
side "ready to pay". It can't — the shared persistent profile is single-process
(see #2 above). What actually happens is better anyway: the filled cart
**persists server-side in your store account**, so you open either store when
you like and the cart is already there. For an interactive terminal run, the
new `--keep-open` flag pauses after each store so you can review and pay before
the window closes; the Streamlit integration never passes it (it would block on
stdin).

---

## 5. Validation — the full Wednesday run

End-to-end validation reproduced a real weekly shop: the inventory Excel had
the usual stock-vs-target gaps, producing **52 pending items** (17 Ametller +
35 Mercadona).

1. `--dry-run` — confirmed all 52 items resolved to real product URLs, 0
   skipped, grouped correctly by store.
2. Live run, both stores: **45 / 52 added and verified.**
   - **Mercadona: 35 / 35.** Header badge tracked 5 → 59 across the run, every
     line verified against both the on-page unit count and the badge.
   - **Ametller: 10 / 17.** Items already in the cart from earlier #2/#3 tests
     were correctly left as-is (idempotency confirmed in the wild).
3. `py_compile` clean on all changed files; Streamlit boots headless without
   errors.

The 7 Ametller failures are a **genuine handler-robustness defect surfaced by
this validation** — exactly what a full-circle run is for — and are tracked as
a follow-up issue. Two distinct modes, confirmed by live DOM probes:

- **Product page never renders** (`huevos`, `manzana`): no `h1`, no add button,
  no stepper, even after the hydration wait — the `/p` URL returns an empty
  product shell. Likely discontinued SKUs; the handler correctly raises
  `AddToCartFailed` rather than guessing.
- **Add silently does not register** (`zanahoria`, `jamón cocido`, `bases
  pizza`, `tomate Mutti`, `mozzarella Galbani`): the product page loads fine,
  but clicking "Añadir" (and the retry) never puts a line in the minicart — a
  probe confirmed the minicart held exactly the 10 successful items and none of
  these. Every item from a certain point onward failed the same way, which
  points at a state that, once entered, is not reset between products (a
  leftover overlay / open drawer is the prime suspect). The Mercadona-style
  "click, then poll until the counter moves, retry" pattern needs to be applied
  to Ametller's add too — verify-and-retry currently only wraps the *stepper*,
  not the *add*.

Mercadona's 35/35 shows the verify-and-retry discipline works when it is
applied; the Ametller follow-up is about extending the same rigour.

---

## 6. If you add a third store tomorrow

1. **Do not touch the login flow.** Add the store's login URL to
   `bootstrap_session.py`'s URL list; the plain-Chrome bootstrap already
   handles it. Add login-redirect markers to `_LOGIN_URL_MARKERS` in
   `browser.py`.
2. Write `automation/<store>.py` with one function: `add_to_cart(page, item)`.
   Start by opening the product page by hand and finding selectors; pin them in
   a `SELECTORS` dict with a "verified live" date.
3. Decide the store's counting model first (units vs. distinct products) — it
   dictates how you verify. Find the *most reliable* place to read the true
   quantity back; do not trust a header badge until you have proven it.
4. Assume every click can silently fail. Verify by polling a counter, retry on
   no-op.
5. Catalogue the modals before writing the happy path. Each one is a
   `_handle_*` helper that is a safe no-op when the modal is absent.
6. Make it idempotent: read current quantity, add only the difference.
7. Register the module in `HANDLERS` in `run_automation.py`. The CLI, the
   `RunReport` summary, and the Streamlit integration all pick it up for free.
