# Configuration and data format

Edit `src/config.json` to customize:
- **Data Paths** — Excel file location and column mappings
- **UI Settings** — Page config, mode labels, layout
- **Logging** — Log level and format
- **Benchmark runs** *(optional)* — `benchmark.runs_dir`, the one directory every benchmark CLI writes to and the store-links simulator reads from (default `benchmark_runs/` in the repo; set it and the runs, `_state/` mappings, history and per-store Chrome profiles all live there)
- **Usage log** *(automatic)* — the PWA records which views and `data-track` controls get used, and on which device class, into `usage.sqlite3` under the fleet runtime-data directory (`C:\sqlite\grocery-shopping-automation\` on Windows; `GROCERY_SHOPPING_AUTOMATION_DATA_DIR` redirects it), never into the repo. Only constant ids, a per-visit session id, device class and timestamps are stored: no item names, quantities, prices, list contents or search text. Read it with project-scaffolding's `scripts/usage_summary.py --app grocery-shopping-automation --days 30`. Tests and the design-review demo instance set the same variable to a temp directory.
- **Served host/port** *(optional)* — `config/webapp_config.json` (`host`/`port`, default `0.0.0.0:8502`) is what `tray.bat`, `webapp.bat`, `/api/access` and the named tunnel all follow; the `:8502` quoted throughout this README is that default

## Telegram notifications

The app can push short Telegram messages through the universal `src/notify/`
component, vendored verbatim from `project-scaffolding` (see
`src/notify/README.md`). Two senders are wired to it: every cart run's
summary, including every item **not** in the cart with its reason
(`automation/run_automation.py`'s `_notify`, see
[Browser Automation](browser-automation.md)), and the Gmail
order-confirmation check — a scheduled or manual check that finds
a dropped or unrecognized item in the confirmation email, and the Settings
**Email watch** sheet's *Test last email* action. See the "Read-only Gmail
order-confirmation check" section further down for what triggers that second
notification.

Configure credentials by copying `config/notify_config.sample.json` to
gitignored `config/notify_config.json` and filling in `bot_token` + `chat_id`
(or set `TELEGRAM_BOT_TOKEN` / `TELEGRAM_CHAT_ID` in `.env`, which take
precedence). With no credentials configured, `build_notify_notifier()` returns
`None` and every send is a silent no-op.

Manual smoke check:

```powershell
& .\.venv\Scripts\python.exe tests\smoke_notify.py ["optional custom message"]
```

## Read-only Gmail order-confirmation check

The app can read order-confirmation emails (read-only `gmail.readonly` scope)
to catch items a store dropped from an order, via the vendored
`gmail_readonly/` component (see `automation/README.md`'s "Order-confirmation
email check" section for the full setup, matching, and notification details).
Copy `auth/gmail/credentials.json` + `auth/gmail/token.json` from the
`whatsapp-radar` sister repo (same account, same scope) and
`config/gmail_config.sample.json` → gitignored `config/gmail_config.json`.

The PWA's ⚙️ Settings (the header gear) drives this via the **Email watch** row and its sheet: pick the
monitored senders (each mapped to a store), switch automatic polling on/off
and set its frequency, run a one-off *Check now* or an end-to-end *Test last
email*, and review the last-check log. Scheduled checks alert only when the
confirmation drops an ordered item (a clean order stays silent), and
already-processed emails are never re-checked or re-notified.

Manual smoke check:

```powershell
& .\.venv\Scripts\python.exe tests\smoke_email_check.py [store]
```

## 📊 Data Format

Excel file columns:

| Column | Description | Notes |
|--------|-------------|-------|
| `super` | Supermarket name (e.g., `mercadona`, `ametller`, `carrefour`) | Required |
| `buscador` | Product URL for online shopping | Optional |
| `lugar` | Zone in the house (e.g., `fridge`, `pantry`) | Required |
| `comida` | Item name | Required |
| `cantidad` | Target quantity to maintain | Required |
| `tenemos` | Current quantity on hand | Required |
| `comprar` | Auto-calculated: `max(0, cantidad − tenemos)` | Auto |
| `url_<store>` (one per store key in `benchmark/stores.json`) | Per-store product link, seeded from the benchmark or picked manually | Optional — see [Store links](benchmark-and-store-links.md) |
| `fallback_super` / `fallback_url` | Store and product link the cart run sends short units to when the item's own product comes up short | Optional, added together — see [Fallback if short](items.md) |
| `units_per_pack` | Units-per-pack conversion so the cart run buys whole packs | Optional — see [Units per pack](items.md) |

