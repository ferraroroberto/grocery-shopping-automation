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
`buscador` product URL — the "what we bought" source of truth the
order-confirmation email check below diffs against (issue #72). The URL is
what could let a later step resolve back to the actual product instead of
matching on name alone. Dry runs and stores with zero added items produce no
file.

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

### Order-confirmation email check (issue #72)

Supermarkets sometimes drop an item from an order (out of stock) and send a
confirmation email listing what actually shipped. `automation.email_check`
reads that email read-only, parses its item list deterministically (no LLM),
and matches it against the latest purchase log so a dropped item is visible.

- **Gmail access** is the `gmail_readonly/` package at the repo root —
  vendored byte-for-byte from `whatsapp-radar` at commit
  `404e2685b97f4009f1ba5f9e53c12582276698ff`, per that repo's
  `docs/gmail-reuse.md` adoption path (OAuth `gmail.readonly` scope only, no
  send/modify capability). Diff against that commit's `gmail_readonly/`
  before adopting a newer one (`git diff --no-index`, per `gmail-reuse.md`).
  This repo reuses the *same* OAuth client and
  refresh token as `whatsapp-radar` (same Google account, same scope) —
  copy `auth/gmail/credentials.json` and `auth/gmail/token.json` from that
  repo's `auth/gmail/` into this repo's gitignored `auth/gmail/`; no new
  Google Cloud registration or consent flow is needed. Override the paths
  with `GMAIL_CREDENTIALS_PATH` / `GMAIL_TOKEN_PATH` in `.env` if reusing a
  different token.
- **Sender whitelist**: copy `config/gmail_config.sample.json` to gitignored
  `config/gmail_config.json` and list the sender(s) to read from (today:
  Ametller Origen, `noreply@news.ametllerorigen.cat`). `src/gmail_config.py`
  wires this whitelist to the vendored component, mirroring
  `src/notify_config.py`'s shape.
- **Subject filtering**: the sender whitelist alone would also catch
  promotional mail from the same address, so `automation.email_check`
  additionally requires the subject to be *similar* (not identical — a
  difflib ratio ≥ 0.8 after stripping accents/emoji/punctuation) to the
  store's known "order prepared" subject, configured per store in
  `STORE_SUBJECTS`.
- **Per-store parser**: `automation/email_parsers/<store>.py` extracts the
  ordered item-name list from that store's specific email HTML/text
  structure — deterministic regex/parsing, never an LLM call, mirroring the
  per-store handler split (`ametller.py` / `mercadona.py`) above. Only
  Ametller is implemented today; add a sibling module once another store's
  confirmation-email format is available.
- **Matching**: `automation.item_matching` resolves each parsed website item
  name to a purchase-log `comida` value. The website's full catalogue name
  (e.g. *"American Burger Ametller Origen 150g - 2uds."*) rarely shares
  enough characters with the short internal name (*"burguer ternera"*) for
  string similarity alone, so the primary path is a persisted
  correspondence table, `config/item_name_aliases.json` (committed — plain
  name pairs, no secrets), keyed by store. A fuzzy fallback (ratio ≥ 0.9)
  only catches minor future drift in an *already-aliased* website name (a
  wording or emoji tweak), not the first-time internal-name gap. Whatever
  purchase-log item is never matched is reported as dropped.
- **Closing the loop**: once a confirmation email is fetched, parsed, and
  matched, `check_latest_confirmation()` sends a plain Telegram summary via
  the `src/notify/` component (issue #71) — e.g. which items matched and
  which purchase-log item didn't show up in the confirmation. It records the
  processed Gmail message id in gitignored `config/gmail_processed_state.json`
  so a repeat call is a no-op.
- **Auto-tab poller (issue #73)**: `app/email_poller.py` calls that seam on a
  schedule. The PWA's Auto tab carries an **Email Watch** card (folded by
  default) that selects which whitelisted senders are monitored (each mapped
  to a store via the `store` field in `config/gmail_config.json`), switches
  automatic polling on/off, sets the cadence (15 min – daily), and shows the
  last-check log (last 20 entries, gitignored
  `config/email_check_log.json`). *Check now* runs one check immediately;
  *Test last email* re-processes the newest confirmation even if already
  seen — the end-to-end dry run, and it always sends the Telegram summary.
  Scheduled / *Check now* runs alert **only on a problem** (a dropped or
  unrecognized item) — a fully-matched order stays silent, per issue #73 —
  and are idempotent: an already-processed message logs "no new email" and
  never re-notifies.

Manual smoke check (runs the real pipeline once, including a real Telegram
send if a match is found):

```powershell
& .\.venv\Scripts\python.exe tests\smoke_email_check.py [store]
```

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
| `email_parsers/ametller.py` | Deterministic order-confirmation item-list parser for Ametller. |
| `item_matching.py` | `match_items()` — resolves confirmed email items to purchase-log `comida` values (alias table + fuzzy fallback). |
| `email_check.py` | `check_latest_confirmation()` — the Gmail-fetch → parse → match → notify orchestration entrypoint (issue #72). |

The app-side glue lives under `app/`, not here: `app/automation_runner.py`
(shared subprocess plumbing), the automation endpoints in `app/api.py` wired to
`app/static/app.js` (FastAPI/PWA), and the **🤖 Run Automation** section in
`app/shopping.py` (legacy Streamlit).

Smoke tests live in `tests/automation_smoke_*.py` — run them manually (they are
live, not CI tests; see each file's docstring).
