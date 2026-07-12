# `automation/` — browser cart automation

Playwright-driven automation that fills the online carts of the grocery stores
in the inventory spreadsheet — one handler per store, plus a CLI runner.

## How sessions work

We use a single **dedicated, shared** Chrome profile directory:
`automation/chrome_user_data/`.

- It is **gitignored** and **separate** from your normal Chrome profile —
  nothing here ever touches `%LOCALAPPDATA%\Google\Chrome\User Data`.
- Both store logins (Mercadona + Ametller) live in this one profile.
- **Login** happens in a plain, un-instrumented `chrome.exe` (see the bootstrap
  below). The store login pages are reCAPTCHA-protected and reject Playwright's
  CDP instrumentation outright — a normal Chrome process started with only
  `--user-data-dir` looks like a human's browser and logs in fine.
- **Cart operations** afterwards are driven by Playwright (`channel="chrome"`)
  against that same profile — they reuse the saved session and are not
  reCAPTCHA-gated.

## One-time setup

From the repo root, with the project `.venv`:

```powershell
& .\.venv\Scripts\pip.exe install -r requirements.txt
```

No `playwright install` step is needed — we use your already-installed Chrome.

## Bootstrap / re-login

Run the interactive bootstrap whenever the profile is empty or a store's
session has expired:

```powershell
& .\.venv\Scripts\python.exe -m automation.bootstrap_session
```

A plain Chrome window opens with one tab per store. Log into **each** store,
then **close that Chrome window completely**, and press **Enter** in the
terminal. The profile is saved to `chrome_user_data/`.

(`chrome.exe` is auto-detected; pass `--chrome-path` if it lives somewhere
non-standard.)

`SessionExpiredError` from `launch_context` / `goto_with_login_check` means a
store bounced you to its login page — just re-run the bootstrap.

## Running the automation

```powershell
# See what would be added, no browser:
& .\.venv\Scripts\python.exe -m automation.run_automation --store mercadona --dry-run

# Live run — opens Chrome and fills the cart:
& .\.venv\Scripts\python.exe -m automation.run_automation --store mercadona
& .\.venv\Scripts\python.exe -m automation.run_automation --store ametller

# Both stores, capped at N items, headless:
& .\.venv\Scripts\python.exe -m automation.run_automation --limit 10 --headless

# Fill the cart, then leave the browser open so you can review and pay:
& .\.venv\Scripts\python.exe -m automation.run_automation --keep-open
```

Each handler is **idempotent** — it reads the current cart quantity and only
adds what is missing, so a re-run after a partial failure is safe. Existing
cart contents are never wiped.

### Purchase log

Every **live** (non-dry-run) run writes one JSON file per store that had at
least one item added, to the directory configured by
`automation.purchase_logs_dir` in `src/config.json` (default `purchase_logs/`,
gitignored — mirrors the `audio_audit_logs/` convention). The file is named
`<date>_<store>.json` and records, per ordered item, the name, quantity, and
`buscador` product URL — the "what we bought" source of truth a later step
diffs against a parsed order-confirmation email (issue #70). The URL is what
lets that later step resolve back to the actual product instead of matching
on name alone. Dry runs and stores with zero added items produce no file.

`--keep-open` pauses after each store's cart is filled and waits for **Enter**
in the terminal — so you can open the cart, review it, and pay before the
window closes and the next store starts. It blocks on stdin, so it is for
interactive terminal use only (the in-app integration never passes it). The
shared profile means one store window is open at a time, not both at once; the
filled cart itself persists in your store account regardless.

### From the web app

The FastAPI/PWA app drives automation from the **🛒 Shopping** view: pick a store
(or "All stores"), choose a cart mode, optionally tick *Dry run*, and start the
run. The automation endpoints in `app/api.py` spawn this same CLI as a
subprocess and stream its output live to the page (`app/static/app.js`); a
**🛑 Stop** control terminates an in-progress run. The legacy Streamlit app
offers the same controls in its Shopping List mode (`app/shopping.py`). Both
surfaces share the subprocess plumbing in `app/automation_runner.py`.

### Store-specific notes

- **Mercadona** counts *units* — 3 of the same product shows as `3` on the
  header badge. Every add is verified against both the product's on-page count
  and the header badge (picker clicks sometimes silently no-op, so they are
  retried until the count moves).
- **Ametller Origen** runs on **Salesforce Commerce Cloud** (the Chakra-UI
  "Composable Storefront" — it migrated off VTEX in May 2026, issue #12).
  Quantities are verified against the **SCAPI Shopper Baskets** API — the
  storefront's own source of truth — using the SLAS shopper token the site
  stores in `localStorage`. Lines are matched by numeric `productId`, read from
  the redirected product URL (`…/{productId}.html`); the legacy `/p` buy URLs
  still 301-redirect there, so no inventory change was needed. The same
  `localStorage` also reveals whether the session is still a *registered*
  shopper — if it has lapsed to a guest, a `SessionExpiredError` is raised. A
  product page that renders an empty shell — a stale/discontinued buy URL — is
  reported as an end-of-run **🔗 Unavailable (check URL)** alert, not a hard
  failure. Selectors use Chakra component classes, ARIA labels, and visible
  button text only — never the Emotion `css-*` hashes, which are regenerated
  on every deploy and will silently break the handler.

## Modules

| File | Responsibility |
|------|----------------|
| `models.py` | `CartItem` dataclass — shared shape for one item to buy. |
| `errors.py` | `OutOfStockError`, `AddToCartFailed` — shared handler exceptions. |
| `grocery_reader.py` | `read_cart_items(store=None)` — inventory XLSX → `list[CartItem]`. |
| `browser.py` | `launch_context()`, `goto_with_login_check()`, `human_delay()`. |
| `bootstrap_session.py` | One-time interactive login (run via `-m`). |
| `mercadona.py` | Mercadona `add_to_cart(page, item)` handler. |
| `ametller.py` | Ametller Origen `add_to_cart(page, item)` handler. |
| `run_automation.py` | CLI runner — reads the list, dispatches to handlers, prints a summary. |
| `report.py` | `RunReport` — per-run summary with `print_summary()`. |
| `purchase_log.py` | `write_purchase_logs()` — persists what was ordered, per store, after a live run. |

The app-side glue lives under `app/`, not here: `app/automation_runner.py`
(shared subprocess plumbing), the automation endpoints in `app/api.py` wired to
`app/static/app.js` (FastAPI/PWA), and the **🤖 Run Automation** section in
`app/shopping.py` (legacy Streamlit).

Smoke tests live in `tests/automation_smoke_*.py` — run them manually (they are
live, not CI tests; see each file's docstring).
