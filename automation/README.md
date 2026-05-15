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

`--keep-open` pauses after each store's cart is filled and waits for **Enter**
in the terminal — so you can open the cart, review it, and pay before the
window closes and the next store starts. It blocks on stdin, so it is for
interactive terminal use only (the Streamlit integration never passes it). The
shared profile means one store window is open at a time, not both at once; the
filled cart itself persists in your store account regardless.

### From the Streamlit app

The Shopping List mode has a **🤖 Run Automation** section: pick a store (or
"All stores"), optionally tick *Dry run*, and click **▶ Run Automation**. The
app spawns this same CLI as a subprocess and streams its output live into the
page; a **🛑 Stop** button terminates an in-progress run. See `app/shopping.py`
and `app/automation_runner.py`.

### Store-specific notes

- **Mercadona** counts *units* — 3 of the same product shows as `3` on the
  header badge. Every add is verified against both the product's on-page count
  and the header badge (picker clicks sometimes silently no-op, so they are
  retried until the count moves).
- **Ametller Origen** (VTEX) verifies quantities against VTEX's
  `/api/checkout/pub/orderForm` JSON endpoint — the storefront's own source of
  truth. The minicart drawer DOM was tried first and turned out to silently
  omit lines, which caused inflated add attempts in early runs (issue #10).
  Both modals are handled: cart-restore (keeps the previous cart) and the
  delivery postal-code modal (needs `automation.ametller_postal_code` in
  `src/config.json` the first time; it then persists in the profile). A
  product page that renders an empty shell — a stale/discontinued buy URL —
  is reported as an end-of-run **🔗 Unavailable (check URL)** alert, not a
  hard failure.

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

The Streamlit-side glue lives under `app/`, not here: `app/automation_runner.py`
(subprocess plumbing) and the **🤖 Run Automation** section in `app/shopping.py`.

Smoke tests live in `tests/automation_smoke_*.py` — run them manually (they are
live, not CI tests; see each file's docstring).
