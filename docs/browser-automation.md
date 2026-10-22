# Browser automation (cart filling)

The `automation/` package fills the online carts of the supermarkets in the
shopping list using Playwright + real Chrome. It uses a dedicated, gitignored
Chrome profile (`automation/chrome_user_data/`) that is kept separate from your
normal browser profile — you log in once via plain Chrome, and Playwright
reuses that session for cart work.

**One-time setup:**
```powershell
& .\.venv\Scripts\pip.exe install -r requirements.txt
```
No `playwright install` step is required — it uses your installed Chrome.

**Log in to the stores (run once, and again whenever a session expires):**
```powershell
& .\.venv\Scripts\python.exe -m automation.bootstrap_session
```
A plain Chrome window opens with a tab per store — log into each, close the
window, then press Enter in the terminal. See
[`automation/README.md`](../automation/README.md) for details.

**Run it from the app:** ⚙️ **Settings** (the header gear) → **Store logins** has a **Log in to stores**
button — it opens the same Chrome window without needing a terminal; log into
each store, then close the window.

**Check the logins (read-only):** **Check logins** in Settings → Store logins (each store's result stays on the row and in its sheet), or
`& .\.venv\Scripts\python.exe -m automation.check_logins`, reports each store
as logged in, logged out or unknown without touching a cart (log of the last
app-run check: `logs/check_logins.log`). A failed cart run names its cause —
login redirect, not logged in, bot challenge, or an unexpected API answer —
see [`automation/README.md`](../automation/README.md).

**Start a cart run from the app:** the **Carts** glance card at the top of the
PWA's **🛒 Shop** tab runs it with **Fill carts** (nothing runs until you tap
it). Its **Options** sheet picks a store (or "All stores"), the **cart mode**
and *Dry run*, and its **What will run** line says in words what the run will
do. Progress streams into the folded run log and a **Stop** button cancels an
in-progress run; the card keeps its picks and live log while you tick items
off the list below it. The legacy Streamlit app offers the same
controls under its **🛒 Shopping List** mode's **🤖 Run Automation** section.
From a terminal you can also run
`& .\.venv\Scripts\python.exe -m automation.run_automation --keep-open`, which
fills the cart and then waits so you can review and pay before it closes.

**Cart mode (`--cart-mode {keep,clean}`, default `keep`):**

- **Keep** (default) — leaves whatever is already in the store cart and adds the
  managed shopping list on top. Use this so one-off products dropped into the
  cart by hand survive the run.
- **Clean** — empties the store cart completely first, then adds the managed
  list from zero. The manual extras are intentionally wiped. In the app this
  mode is gated behind a confirmation checkbox because it is destructive.

In both modes the run summary reports each store's whole-cart total **before**
and **after**, plus the units the automation added — e.g.
`🛒 mercadona: cart 7 → 12 (automation +5)` — so you can confirm end-to-end that
the cart changed by the expected amount.

Every live run then **checks the store's final cart against the list**: a failed
add is retried once, every item is reconciled with the cart, unavailable items
get a suggested alternative, and the run ends with a summary of verified items
and every item **not** in the cart with its reason — in the Shop carts glance and
through the Telegram notifier. A run with misses exits non-zero and never reads
as a clean success. Each run also leaves a record under `purchase_logs/runs/`.
Details: [`automation/README.md`](../automation/README.md) → "Cart guard".

A walk-through of how this automation was built, store quirk by store quirk,
is in [`browser-automation-build.md`](browser-automation-build.md).

