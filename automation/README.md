# `automation/` — browser cart automation

Playwright-driven plumbing that fills the online carts of the grocery stores in
the inventory spreadsheet. This package is the foundation (issue #1);
store-specific add-to-cart logic lands in follow-up issues.

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

## Modules

| File | Responsibility |
|------|----------------|
| `models.py` | `CartItem` dataclass — shared shape for one item to buy. |
| `grocery_reader.py` | `read_cart_items(store=None)` — inventory XLSX → `list[CartItem]`. |
| `browser.py` | `launch_context()`, `goto_with_login_check()`, `human_delay()`. |
| `bootstrap_session.py` | One-time interactive login (run via `-m`). |
| `report.py` | `RunReport` — per-run summary with `print_summary()`. |
