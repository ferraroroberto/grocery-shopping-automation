# Browser cart automation — structure and adding a store handler

How the `src/automation/` package is put together and what a new store handler has to provide. For the module-by-module reference see [`src/automation/README.md`](../src/automation/README.md); for running it, logins and the cart guard see [browser-automation.md](browser-automation.md).

## 1. Shape

```
src/automation/         UI-agnostic browser automation
  browser.py            Playwright factory: persistent Chrome profile, login check
  bootstrap_session.py  one-time interactive login (plain chrome.exe)
  check_logins.py       read-only per-store login check
  grocery_reader.py     inventory XLSX -> list[CartItem]
  models.py             CartItem dataclass
  errors.py             OutOfStockError, AddToCartFailed
  mercadona.py          store handler
  ametller.py           store handler
  carrefour.py          store handler
  product_options.py    per-store, per-product page-option picks (e.g. a cut)
  report.py             RunReport summary
  run_automation.py     CLI runner: read list -> group by store -> dispatch,
                        plus the cart guard (retry, verify, fallback, notify)
app/
  automation_runner.py  subprocess plumbing (spawn, drain stdout, stop)
  shopping.py           legacy Streamlit "Run Automation" section
```

`src/automation/` never imports `streamlit`, and `app/` never drives Playwright. The CLI runner is the seam: the PWA (`app/webapp/routers/automation.py`) and the legacy Streamlit integration both wrap the exact command typed in a terminal, so the terminal stays a first-class debugging path.

## 2. Login and cart operations are split

The stores' login pages are reCAPTCHA-protected and reject a Playwright-launched browser (even `channel="chrome"`: "Could not connect to the reCAPTCHA service"), so exporting cookies into a fresh Playwright context does not work. The split that does:

1. **Bootstrap login in a plain `chrome.exe` subprocess**, never Playwright: `subprocess.Popen([chrome, f"--user-data-dir={USER_DATA_DIR}", *urls])`. A Chrome process started with only `--user-data-dir` looks like a human's browser. The operator logs into each store, closes the window and presses Enter.
2. **Drive cart operations with Playwright against that same on-disk profile** (`launch_persistent_context(user_data_dir=..., channel="chrome")`). Cart pages are not reCAPTCHA-gated, only login is, so once the session cookie is in the profile Playwright drives the carts without a challenge.
3. **One shared profile for all stores** (`automation/chrome_user_data/`, gitignored).

Playwright never touches a login page. A persistent profile directory can be opened by one browser process at a time, so the runner works stores sequentially (open one context, finish, close, open the next); two store windows cannot be open side by side. The filled cart persists in the store account, so any store can be opened afterwards with its cart already there, and `--keep-open` pauses after each store in an interactive terminal run so the cart can be reviewed and paid before the window closes.

## 3. What every handler shares

- **Idempotent.** A handler reads the current cart quantity and adds only what is missing, so a re-run after a partial failure never double-adds and never reduces a line that already has enough.
- **Pinned selectors.** Selectors sit in a `SELECTORS` dict per handler with a "verified live" date, so a DOM change is a one-line fix and the staleness is visible. Prefer stable hooks (`data-testid`, ARIA labels, component classes, visible button text); never generated style hashes such as Emotion `css-*` classes, which change on every deploy.
- **Wait before believing the page.** Verification polls with a timeout instead of checking once, and a click is never trusted: the handler confirms that a counter moved and retries a no-op click.
- **Typed, per-item errors.** `OutOfStockError` and `AddToCartFailed` bubble to the runner, which records them in `RunReport` and keeps going, so one bad item never aborts the run.
- **Verified against the store's own cart.** The cart guard reconciles every intended item against the final cart, read through the handler's `read_cart_lines`. A store with no cart reader leaves its adds "not confirmed" rather than verified.

How each store differs is in [`src/automation/README.md`](../src/automation/README.md) under *Store-specific notes*.

## 4. Adding a store handler

1. **Login.** Add the store's URL to `_BOOTSTRAP_URLS` in `bootstrap_session.py`; the plain-Chrome bootstrap does the rest. Add the substrings of its login page URL to `_LOGIN_URL_MARKERS` in `browser.py` so a redirect to the login page raises `SessionExpiredError`.
2. **Handler module.** Write `src/automation/<store>.py`. Open the product page by hand first and find selectors, then pin them in `SELECTORS`. It exposes:
   - `add_to_cart(page, item)` — required.
   - `read_cart_total(page)` — the cart's unit count, used for the before/after totals.
   - `read_cart_lines(page)` and `cart_key(url)` — the final-cart reader the cart guard verifies against, keyed by the id `cart_key` extracts from a product URL.
   - `clear_cart(page)` — used by the `clean` cart mode.
   - `check_login(page)` — the read-only session signal behind `check_logins`; raise `NotLoggedInError` when the store reports no account.
3. **Counting model.** Decide first whether the store counts units or distinct products, since it dictates how to verify. Find the most reliable place to read the true quantity back, and do not trust a header badge until it is proven.
4. **Clicks and modals.** Assume every click can silently fail: verify by polling a counter and retry on a no-op. Catalogue the modals before writing the happy path; each is a `_handle_*` helper that is a safe no-op when the modal is absent.
5. **Idempotence.** Read the current quantity and add only the difference.
6. **Register** the module in `HANDLERS` in `run_automation.py`. The CLI, the `RunReport` summary, `check_logins` and the app integrations read that registry.
7. **Rest of the repo.** Any other place that keys on the store name needs its entry too: the `super` values the inventory sheet uses, the store registry in `config/benchmark_stores.json`, the product search, and the confirmation-email parser and sender whitelist if the store sends one.
