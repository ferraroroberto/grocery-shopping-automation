# Household Inventory & Shopping Helper

Mobile-responsive web app for managing household grocery inventory with intelligent shopping list generation and real-time purchase tracking. The primary surface is a **FastAPI + vanilla-JS PWA** served on `:8502`; a legacy **Streamlit** app on `:8501` remains available and drives the same modes against the same Excel-backed `src/data.py` layer.

## 📋 Project Summary

Comprehensive household inventory management across multiple operational modes. Audit current stock room-by-room, edit target quantities, track shopping in real time, and add on-the-fly items directly to the shopping list.

**Key Features:**
- Mobile access over local Wi-Fi — use the **Copy link** button in ⚙️ Settings (the gear in the page header) to get the URL and open it on your phone
- Room-by-room inventory auditing with auto-save (best done from mobile)
- Shopping list grouped by supermarket with per-store done counts (best done from desktop)
- Cart offset counters to account for items already in the cart
- Quick-add items (name + quantity) to any supermarket's shopping list
- Excel-based data storage with automatic calculations
- Cross-platform compatibility (works on any device with a browser)

## 🏗️ Project Structure

- **`app/`** — UI layers.
  - *Primary (FastAPI/PWA on `:8502`):* `api.py` (composition root only — builds the app, wires middleware and the static mount, and includes one router per concern; every route lives in `routers/`, not here), `routers/` (`system.py` / `inventory.py` / `stores.py` / `automation.py` / `product_search.py` / `audio.py` / `voice.py` / `email.py` / `usage.py` — one `APIRouter` per concern), `api_common.py` (data-layer → HTTP translation and the shared inventory payload builder), `audio_hub.py` (voice-transcriber session proxy + whisper transcode/transcribe), `middleware.py` (bearer-token auth for non-loopback requests), `static_files.py` (`CachingStaticFiles` cache policy + JS import stamping), `automation_runner.py` / `product_search_runner.py` / `subprocess_plumbing.py` (shared subprocess plumbing that streams the cart-automation and product-search CLIs into the app), `email_poller.py` (background confirmation-email poll loop), `static/` (PWA front end: `index.html`, `app.js` orchestrator, `styles.css`, `modules/` ES modules — `core` / `dom` / `rows` (the one item row + stepper) / `confirm` (the confirm sheet) / `media` / `api` / `inventory` / `shopping` / `stores` / `automation` / `email` / `audio` / `search`, `_vendored/` fleet components).
  - *Legacy (Streamlit fallback on `:8501`):* `app.py` (entrypoint — page config, session state, sidebar, mode routing), `audit.py` / `audio_audit.py` / `edit_targets.py` / `edit_item.py` / `add_item.py` / `shopping.py` / `export.py` / `ui_helpers.py` (per-mode modules, each exposing `main(df)`).
- **`src/`** — UI-free data/business layer.
  - `data.py` — config loading, XLSX load/save, supermarket stats, quantity mutators, optional per-store `url_<store>` column helpers.
  - `store_links.py` — per-store product links, store picks and the cost simulator (#148): imports URLs from the benchmark mappings, picks a row's store (`super` + `buscador`), prices any store split with `benchmark.score` against the latest scored run, and applies picks with pack-size conversion.
  - `gen_ssl_cert.py` — generate a local CA + server cert for HTTPS.
  - `inventory_extract.py` / `transcribe_client.py` — audio-audit transcription + LLM extraction (via the `local-llm-hub` hub).
  - `audit_resolve.py` — deterministic fuzzy resolver mapping a dictated phrase back to one inventory row when the LLM leaves it unresolved (#132); declines on a weak or ambiguous match rather than guessing.
  - `voice_command.py` — voice-command bridge for the HA Voice PE pucks (home-automation#315): `POST /api/voice/command` takes free Spanish text with an intent (`add` / `target` / `stock` / `query`), parses items + quantities against the inventory via the hub LLM, applies through `data.py`, and returns a short ready-to-speak `speech` reply. The intent is chosen by Home Assistant's deterministic sentence match — the LLM never picks the operation.
  - `audio_audit_core.py` — UI-agnostic transcript cleaning + audit-log writer shared by the PWA and the legacy Streamlit mode.
  - `net.py` — LAN-IP / port-probe helpers shared by the FastAPI and Streamlit front ends.
  - `certs.py` — the one `cert.pem`/`key.pem` lookup (`webapp/certificates/`, then `certificates/`) the router, tray manager and tunnel launcher share.
  - `webapp_config.py` — remote-access (token/password) config loader.
  - `config.example.json` — committed template; copied to `src/config.json` (gitignored) on first run.
- **`automation/`** — Playwright + real-Chrome browser cart automation for Mercadona, Ametller and Carrefour (see `automation/README.md`).
- **`benchmark/`** — quality-locked supermarket price benchmark: `build_basket.py` (monthly basket), `ametller_guest.py` (Ametller API via guest token), `results.py` (validated per-store research writer), `browser_fetch.py` (real Chrome for bot-protected stores), `score.py` (split scenarios, max-savings and recommended plan), `history.py` (run history), `report.py` (HTML report), `stores.json` (store registry). Driven by the `/supermarket-benchmark` project skill; see [📊 Supermarket Benchmark](#-supermarket-benchmark).
- **`scripts/`** — `gen_token.py`, `set_password.py` (remote auth), `run_named_tunnel.py` (Cloudflare), `gen_icons.py` (canonical PWA/tray/Stream Deck icon generation), and the design-review demo instance (`design_review_synthetic.py` launcher, `synthetic_demo_data.py` invented data, `synthetic_demo_app.py` stubbed app, `synthetic_demo_guard.py` runtime guard).
- **`assets/`** — generated external application surfaces: `tray/grocery-shopping-automation.ico` and `stream-deck/grocery-shopping-automation-144.png`.
- **`webapp/`** — `cloudflared.sample.yml` and the gitignored `certificates/`.
- **`config/`** — `webapp_config.sample.json` template.
- **`data/`** — `list.example.xlsx` sample inventory.
- **`docs/`** — design records and the browser-automation build walk-through.
- **`tests/`** — `pytest` suite (`test_*.py`: unit + FastAPI `TestClient` + a Playwright e2e that drives the real buttons) plus standalone smoke scripts (`smoke_*.py`, `automation_smoke_*.py`). Run `& .\.venv\Scripts\python.exe -m pytest`. The e2e stubs the LLM hub by default; set `GROCERY_E2E_LIVE=1` (with the hub running) to exercise the real model.
- **Launchers** — `tray.bat` (FastAPI/PWA on `:8502`, tray-managed, recommended), `webapp.bat` (same app, manual/no-tray), `launch_app.bat` (legacy Streamlit on `:8501`), `webapp_tunnel_named.bat` (FastAPI + Cloudflare tunnel).
- **`app/tray/`** — Windows tray (`tray.py` pystray icon, `manager.py` uvicorn adopt-or-spawn, `single_instance.py` vendored named-mutex primitive) + root `launcher.py` entrypoint.
- **`.streamlit/config.toml`** — Streamlit theme customization (legacy app).

## 🚀 Quick Start

### Prerequisites
- Python 3.10+ with the project `.venv`
- Install dependencies: `& .\.venv\Scripts\pip.exe install -r requirements.txt`

### Launch the app (FastAPI/PWA — recommended)

`tray.bat` is the recommended launcher — it puts a tray icon in the notification area that owns the webapp's lifecycle (idempotent start, orphan-proof `--restart`, single-instance guard). Drop a shortcut to it in your Startup folder for an always-on service across reboots, or just double-click it:

```powershell
& .\.venv\Scripts\pip.exe install -r requirements.txt
tray.bat              REM start (no-op if already running)
tray.bat --restart    REM stop + start, to pick up new code
```

Tray menu: **Open grocery** · **Copy local URL** · **Restart webapp** · **Status** · **Quit**. **Open grocery** and **Copy local URL** use `https://<host>.ts.net:8502` when the served cert (`certificates/cert.pem`) names a Tailscale host — read straight off that cert's SAN, never hardcoded — falling back to the loopback URL (`http://127.0.0.1:8502`, or `https://` once any cert exists) when it doesn't. Same mechanism as the sibling `facilitation-suite` tray. `webapp.bat` remains available as the manual/no-tray alternative — same app, no tray icon, no lifecycle management:

```powershell
& .\.venv\Scripts\pip.exe install -r requirements.txt
webapp.bat
```

**Email poller (which launch runs it).** The background confirmation-email poller (Settings → Email watch) is opt-in per process: only the tray-launched webapp starts it, because `tray.bat` sets `GROCERY_EMAIL_POLLER=1` in the environment of the webapp it spawns. Every other boot — `webapp.bat`, the named-tunnel launcher, a git worktree, a design-review or fixture instance — logs `Email poller is off` and never reads the mailbox on its own (**Check now** in Settings still works there). Pytest never starts it either, flag or not. To run the poller from a manual `webapp.bat` run, opt in deliberately for that shell first: `$env:GROCERY_EMAIL_POLLER = "1"; webapp.bat`.

The FastAPI app on `:8502` covers the inventory dashboard, audit, target editing, item editing, item creation, shopping mode, automation controls, and the audio-audit workflow against the Excel-backed `src/data.py` layer. Open `http://127.0.0.1:8502` when no local cert exists, or `https://127.0.0.1:8502` after running `& .\.venv\Scripts\python.exe src\gen_ssl_cert.py`. Either launcher binds to `0.0.0.0`, so the same port is reachable over LAN or Tailscale from devices that can reach this PC.

The PWA follows the fleet design system (`~/.claude/design.md` + `design.dark.md`): a floating bottom-tab pill on the phone (inline top tabs on desktop, a left icon-over-label rail on a desktop window 1100px or wider) with four tabs — **Home · Shop · Audit · Items** (**Home** is the landing page: a **This week** card with one row each for the audit, the shopping list, the carts and the prices — each row opens the tab that owns it — then a search over every item; Audit opens on **Voice** and switches to **Manual** with a segmented control; Items switches between **Items**, one list grouped by zone, and **Prices** the same way; the store product search sits at the top of the **Add item** sheet the Items list opens, and **Shop** opens on the **Carts** glance card: the last fill's outcome per store, the one **Fill carts** button, and an **Options** sheet for the store, cart mode and dry run) — vendored fleet components under `app/static/_vendored/`, and a **page header** atop every tab (icon, bold tab name, one live context line that names what needs you — e.g. Home's "1 not in cart" else "Audited 6 days ago", Shop's "1 not in cart · 2 no link", Audit's "3 unmatched", Items' "4 need checking" — else a plain count, a light/dark **theme toggle**, moon/sun icon, that remembers your choice, and the **Settings gear**) so each tab opens the same way. Settings is never a tab: the gear beside the theme toggle opens it over whichever tab you are on (tap the gear again, or any tab, to leave). It is three inset groups, every row showing its current value: **Display** (the **text size**, Small / Default / Large, remembered on the device and applied to every view), **Stores** (**Store logins** — the last check per store, with Check logins and Log in to stores — and **Email watch**, each a sheet whose changes save as they happen) and **Data** (Open spreadsheet, Copy link, Export CSV, each acting on tap) — Log in to stores opens the same plain Chrome window as `python -m automation.bootstrap_session` (see below) so a stale store login can be refreshed without a terminal; heavy cards (the per-store shopping panels) are collapsible and folded by default, and the Voice checklist folds each zone; the search box appears only on the modes that filter the item list; every list of items shares one row (the manual Audit row has one in-stock stepper, an Items row its in-stock/target figures, a Shop row a leading Got-it check and a trailing buy link), and tapping a row anywhere opens the same **item sheet**: in-stock and target steppers, then Zone, Store, Buy link (a real web link also has an open-in-new-tab button beside it), Stores & prices, Quantity check and Name, each one tap away, and Delete — your changes stay staged until **Save** (Esc or × discards them), and deleting asks first in an in-app confirm sheet; the result of an action you started (Saved, Link copied, …) shows as a brief neutral toast above the nav. A footer line shows the running build (`Build: <git sha> · <time>`, from `/api/version`) so you always know which deploy the app is serving, and the shell auto-reloads once when it detects a newer build.

### Regenerate application icons

All external icon surfaces derive from the canonical Lucide `shopping-basket` master in the sibling `project-scaffolding` repository. The generated files are committed and used by the PWA, Windows tray, shortcuts, and Stream Deck:

```powershell
& .\.venv\Scripts\python.exe scripts\gen_icons.py
```

The command writes the PWA files under `app/static/`, `assets/tray/grocery-shopping-automation.ico`, and `assets/stream-deck/grocery-shopping-automation-144.png`. Set `PROJECT_SCAFFOLDING_ROOT` if the sibling repository is checked out somewhere other than `E:\automation\project-scaffolding`.

### Design-review demo instance

`scripts/design_review_synthetic.py` boots a throwaway copy of this checkout on **invented data** (a temp workbook and two benchmark runs, made-up products and `demo.invalid` links) so the design review and the redesign can click through every tab, mode and dialog without touching the household's list. It is what fleet-config's `design_review measure grocery-shopping-automation --synthetic` runs (declared in `.fleet.toml` under `[design.review.synthetic]`); to look at it yourself:

```powershell
& .\.venv\Scripts\python.exe scripts\design_review_synthetic.py   # prints ROOT= and URL=; stops (and deletes its temp tree) when stdin closes
```

Nothing real is reachable from it: the copy leaves out the gitignored real data, `config` points at the temp tree and at a closed port, the cart automation, product search, store logins, Chrome launch, "open spreadsheet", the email poll, the voice transcriber, whisper and the LLM hub are all replaced by invented answers (`synthetic_demo_app.py`), and a runtime audit-hook guard (`synthetic_demo_guard.py`) refuses any process launch, non-loopback or real-service connection, open of the real data, or write outside the temp tree, logging it to `<root>/guard.log`. It copies the working tree (tracked and untracked-unignored files), so a redesign in progress is what gets walked. `tests/test_design_review_synthetic.py` boots it and proves all of the above, with a canary file standing in for the real spreadsheet.

### Legacy Streamlit app

The original Streamlit UI is **intentionally retained**, not dead code: it's a fully working fallback over the same `src/data.py` layer, handy for quick desktop access without the PWA's HTTPS-cert / bearer-token setup, and a reference for the per-mode logic. The FastAPI/PWA app on `:8502` is the recommended surface — reach for Streamlit only when you specifically want the old interface.

It's still available on `:8501` and drives the same modes. Double-click `launch_app.bat`, or from the repo root:

```powershell
& .\.venv\Scripts\python.exe -m streamlit run app/app.py
```

### FastAPI remote access: Tailscale + Cloudflare

The FastAPI preview follows the same access mechanics as `voice-transcriber` and `app-launcher`: local/LAN/Tailscale traffic reaches the app directly on `:8502`, and `webapp_tunnel_named.bat` can start the app plus a named Cloudflare tunnel using `webapp/cloudflared.yml`. The committed `webapp/cloudflared.sample.yml` points Cloudflare at `https://localhost:8502` with `noTLSVerify: true`, matching the sibling apps where Cloudflare terminates public TLS and the local origin uses a self-signed cert.

One-time Cloudflare setup:

```powershell
winget install Cloudflare.cloudflared
cloudflared tunnel login
cloudflared tunnel create grocery
cloudflared tunnel route dns grocery grocery.<your-domain>
copy webapp\cloudflared.sample.yml webapp\cloudflared.yml
notepad webapp\cloudflared.yml
```

Then run:

```powershell
webapp_tunnel_named.bat
```

Optional app-level auth matches the sibling pattern. `auth_token` is off by default; when set, non-loopback API calls need `Authorization: Bearer <token>` or a `?token=...` bootstrap URL. The page stores the token in `localStorage` and removes it from the visible URL. A password can also be set so a fresh phone can unlock by typing the password, then the server hands back the bearer token.

```powershell
& .\.venv\Scripts\python.exe scripts\gen_token.py
& .\.venv\Scripts\python.exe scripts\set_password.py PW
```

Re-run `scripts\gen_token.py --force` to rotate the token, `scripts\gen_token.py --clear` to disable bearer auth, and `scripts\set_password.py --clear` to remove the password prompt.

### Mobile Access (same Wi-Fi network)
For the FastAPI/PWA rebuild, launch `webapp.bat` and open `http://<local-ip>:8502` or `https://<local-ip>:8502` if local certs are present. The app binds to all network interfaces automatically, so the same URL also works over Tailscale when the device can reach this PC.

For the legacy Streamlit app:
1. Launch `launch_app.bat` on the PC as usual
2. Click **📋 Copy link** in the sidebar — this copies `https://<local-ip>:8501` to the clipboard
3. Paste the URL into Telegram (or any messaging app) and open it on your phone

> **Firewall:** if the phone cannot connect on first use, run this once in PowerShell (admin):
> ```powershell
> New-NetFirewallRule -DisplayName "Grocery Web Apps" -Direction Inbound -Protocol TCP -LocalPort 8501,8502 -Action Allow
> ```

> **Audit mode on mobile:** rotate your phone to **landscape** for the best layout — the row-per-item grid fits without horizontal scrolling.

### HTTPS setup (required for microphone access on mobile)

Mobile browsers block microphone access on plain HTTP. The app serves over HTTPS using a certificate stored in `certificates/` (gitignored). `webapp.bat` picks the cert up automatically and starts uvicorn with TLS on `:8502`.

There are two certificate options — pick one:

#### Option A: Tailscale HTTPS cert (recommended — no cert installation on any device)

If you access the app over Tailscale, provision a real Let's Encrypt certificate via the Tailscale CLI. Browsers on every device trust it automatically — no manual cert installation, no "Not secure" warning anywhere.

**Prerequisites (one-time):**
1. Enable HTTPS in the Tailscale admin console: go to **DNS → HTTPS Certificates** and toggle it on.
2. `tailscale` must be running and authenticated on this machine.

**Run once from the repo root:**
```powershell
& .\.venv\Scripts\python.exe scripts\gen_tailscale_cert.py
```

The script auto-detects your Tailscale hostname and writes `certificates/cert.pem` and `certificates/key.pem`. Restart `webapp.bat`, then open:
```
https://<host>.ts.net:8502
```

**Renewal:** Tailscale certs expire after 90 days. Re-run the script when they expire (the script is idempotent — it renews if needed).

**Note:** after switching to the Tailscale cert, `https://localhost:8502` will show a cert hostname-mismatch warning (the cert is issued for the Tailscale domain, not `localhost`). Use `http://localhost:8502` for plain local desktop access when not on Tailscale.

#### Option B: self-signed cert (LAN / localhost access)

Generates a local CA and a self-signed server cert that covers `localhost` and all LAN IPs. Requires installing the CA once on this PC; mobile devices need a one-time "Proceed to … (unsafe)" tap per device.

**First-time setup** (run once from the repo root):
```powershell
& .\.venv\Scripts\python.exe src\gen_ssl_cert.py
```

This detects all local IP addresses and writes three files to `certificates/` (valid 10 years):
- `ca.pem` — local CA certificate, installed into Windows `CurrentUser\Root` (no admin required)
- `cert.pem` — server certificate signed by that CA
- `key.pem` — server private key

Chrome and Edge on this PC will show no security warning because the CA is trusted.

**Accepting the cert on mobile (one-time per device):** open `https://<local-ip>:8502` — the browser will warn "Not secure". Tap **Advanced → Proceed to … (unsafe)**. You won't be asked again on that device.

**If your PC's IP changes**, regenerate and reinstall with the same command above, then restart the app.

## ⚙️ Configuration

Edit `src/config.json` to customize:
- **Data Paths** — Excel file location and column mappings
- **UI Settings** — Page config, mode labels, layout
- **Logging** — Log level and format
- **Benchmark runs** *(optional)* — `benchmark.runs_dir`, the one directory every benchmark CLI writes to and the store-links simulator reads from (default `benchmark_runs/` in the repo; set it and the runs, `_state/` mappings, history and per-store Chrome profiles all live there)
- **Usage log** *(automatic)* — the PWA records which views and `data-track` controls get used, and on which device class, into `usage.sqlite3` under the fleet runtime-data directory (`C:\sqlite\grocery-shopping-automation\` on Windows; `GROCERY_SHOPPING_AUTOMATION_DATA_DIR` redirects it), never into the repo. Only constant ids, a per-visit session id, device class and timestamps are stored: no item names, quantities, prices, list contents or search text. Read it with project-scaffolding's `scripts/usage_summary.py --app grocery-shopping-automation --days 30`. Tests and the design-review demo instance set the same variable to a temp directory.
- **Served host/port** *(optional)* — `config/webapp_config.json` (`host`/`port`, default `0.0.0.0:8502`) is what `tray.bat`, `webapp.bat`, `/api/access` and the named tunnel all follow; the `:8502` quoted throughout this README is that default

### Telegram notifications

The app can push short Telegram messages through the universal `src/notify/`
component, vendored verbatim from `project-scaffolding` (see
`src/notify/README.md`). Two senders are wired to it: every cart run's
summary, including every item **not** in the cart with its reason
(`automation/run_automation.py`'s `_notify`, see
[Browser Automation](#-browser-automation) below), and the Gmail
order-confirmation check below — a scheduled or manual check that finds
a dropped or unrecognized item in the confirmation email, and the Settings
**Email watch** sheet's *Test last email* action. See the "Read-only Gmail
order-confirmation check" section below for what triggers that second
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

### Read-only Gmail order-confirmation check

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

## 📱 Modes

### 🔍 Audit inventory
Audit's **Manual** segment: walk through each zone of the house and update each item's stock with its one in-stock stepper (targets are edited in the item sheet). Auto-saves every change to Excel. The search box looks in every zone, matching what you type or, when nothing has it, the words of it — so a phrase as it was said still finds the item.

### 🎙️ Audio audit
Audit's **Voice** segment, where the tab opens: walk the house dictating the inventory in Spanish (*"ahora en la nevera, dos yogures, un litro de leche…"*). The audio is transcribed by the local whisper-server and matched against the inventory by the local LLM hub — same `local-llm-hub` services that power the rest of this monorepo. The **Voice audit** glance card leads: the last audit's date, how many counts it changed and how many mentions it left unmatched (read from the newest log, `GET /api/audio/last`), and the one **Record** button, with a level waveform and a timer while recording. A line under it names any unreachable service (recorder, hub, whisper). Below it, the **Checklist** card lists each zone's tracked items, folded per zone, so nothing gets missed while dictating, and the **Transcript** card holds the transcript and **Match**; its menu (⋯) holds the **Match model** (defaults to `gemini_pro`, configurable via `audio_audit.llm_model` / `llm_models_available`) and **Clear**, behind a confirm. Transcribe and Match show a **live elapsed timer** with staged progress and a **Cancel** button (calls budget up to 10 min — `audio_audit.llm_timeout`), and surface errors inline instead of failing silently. After a match, the **Match results** glance carries **Apply**, the view's one primary, with the counts to apply; then **Needs you** comes first — named items with no count, and mentions no item took, whose **Find** (a plain search icon on the row) opens Manual searching every zone for what was heard (fix it with one stepper tap; the review waits on Voice as you left it) — then the detected items by zone (current → new, evidence) and the items not mentioned in a walked zone (tick to set 0). Applying writes a JSON audit log to `audio_audit_logs/`.

**Named but uncounted items (#132).** Whisper regularly drops a dictated number or pushes it onto the next item, so an item you clearly named comes back with no count. Those rows land in **Needs you** as "Count missing": type the number to apply it, leave the box blank to skip. They are *never* offered in the "set to 0" section — an item you named out loud must not be zeroable, which is what previously wiped a stocked row. Each mention is pinned to an inventory row by the LLM, and by the deterministic fallback in `src/audit_resolve.py` when the LLM leaves it open (it recovers whisper drift like *Aximell*→actimel, *Copos de arena*→copos avena, *Ganzos*→garbanzos, and declines rather than guessing when two rows fit equally well).

**Replaying past walks.** Every applied audit stores its transcript and raw LLM result, so `scripts/replay_audit_log.py` can re-run the matcher over that corpus and *measure* a prompt or resolver change instead of asserting it:

```powershell
& .\.venv\Scripts\python.exe scripts\replay_audit_log.py --all         # deterministic resolver only — instant, no hub call
& .\.venv\Scripts\python.exe scripts\replay_audit_log.py --latest --live  # full re-extraction through the hub
```

**Hardened recording (never lose a take).** Recording does **not** buffer the whole take on the phone. Each 1-second chunk is streamed to the PC and archived to disk the moment it arrives by the sibling **voice-transcriber** app, so the audio survives even if the phone dies or the page is backgrounded mid-walk. The transcript fills in live as you talk (rolling partials over SSE when the voice-transcriber has them enabled), and **Stop** returns the canonical transcript ready to Match. The status line shows elapsed time and bytes streamed to the PC. **Redo** re-runs whisper on the saved audio (crash recovery, or after a transient transcribe error); **Clear** resets everything for the next audit. grocery only *proxies* the recording lifecycle (`POST /api/audio/session` → `…/chunk` → `…/events` SSE → `…/finish` → `…/retranscribe`) to the voice-transcriber session API on loopback — it never re-implements recording or transcription. The recorder URL is `audio_audit.voice_transcriber_url` (default `https://127.0.0.1:8443`). (The old Choose-File upload fallback is gone from the UI — record, Redo, or paste a transcript; `POST /api/audio/transcribe` still accepts a direct file upload for scripted use.)

> **Pre-requisites:** the **voice-transcriber** webapp must be running (it boots from its tray) for live recording — when it's down the audio view shows a clear banner and disables Record instead of hanging. The hub on `:8000` and whisper-server on `:8090` must also be running (start them via `E:\automation\local-llm-hub\run_hub.bat` and `launchers\run_model.bat whisper`, or its tray launcher). **`ffmpeg` must be on `PATH`** (`winget install Gyan.FFmpeg`) for the direct-upload `/api/audio/transcribe` endpoint — uploaded webm/mp4 is transcoded to 16 kHz WAV, then sent to the hub's transcribe role on `:8000` (parakeet primary + whisper failover), not to whisper directly (the streamed record path transcodes inside voice-transcriber). The audio-audit diagnostic still targets whisper-server on `:8090` directly.

### 📦 Items (Items tab)
The **Items** tab has two segments, **Items** and **Prices** (#256). **Items** is every item in one list, grouped by zone (A–Z inside a zone), each row showing its store, what to buy and its in-stock/target figures. The search box above it filters the list by item name, zone or supermarket. Tap any row to open its item sheet: set the target and in-stock count, change the name, zone, store or buy link, see its stores and prices, or delete it. Nothing is written until **Save**, except the store pages and **Fallback if short**, which write when you save them there. **Fallback if short** names the store and product link the cart run sends the missing units to when the item's own product comes up short (a stock cap, out of stock): pick a registered store, paste the product link, **Save fallback**; **Clear fallback** blanks both. They live in two optional spreadsheet columns, `fallback_super` and `fallback_url`, which a sheet gains only when a fallback is first saved. A link that is a search page or lacks the store's product id is saved as typed but flagged, and a fallback equal to the item's own buy link is refused. (The legacy Streamlit app keeps its separate Edit targets / Edit item / Add item modes.)

**Add item** (beside the search box) opens the Add item sheet: **Find a store product** first (below), then **Add by hand**, a form for the name, supermarket, zone, target, in-stock count and URL. Supermarket and Zone are pickers of the values already in the sheet (no typing on a phone); choose **New supermarket…** / **New zone…** to enter a first-time one. The sheet's **Add item** saves the item and closes the sheet; Esc or × discards. A search's results stay there when you reopen the sheet.

#### 🔎 Find a store product (Add item sheet)
Find the store product that fills an item's `buscador` so the cart automation
can buy it. **Speak or type a product in Spanish** (e.g. *"añade sandía"*); the
spoken clip is transcribed by whisper (language auto-detect) and parsed into the
search term the same way the HA voice bridge parses commands — with a short
budget (`audio_audit.product_search_parse_timeout`, default 20 s, no retries),
falling back to searching your raw text if the hub is slow or down. The app
then searches **Mercadona** (its Algolia search endpoint) and **Carrefour** (a
real-Chrome DOM read of its results grid, Cloudflare-gated like its cart
handler), driving the logged-in Chrome profile. Carrefour cards whose add
button is disabled (*Agotado temporalmente*) are skipped: their product page
redirects away, so the cart run could never add them (#249). (Ametller left product search in
#211; its cart handler and stored links are unchanged.) Each store's cards
appear **as soon as that store answers**, under one line per store — *waiting
for the browser*, *searching…*, *N results*, *no results*, or *couldn't search*
(session, slow site, or network) — so a slow or failed store never hides the
other's results and never reads as "no results". The candidates are **cards
you validate**:
each card has the product name, store, price and a **link to open the product
and check it yourself**. The stores are always searched with **your own words**
(issue #214) — the results are headed *Results for “boniato”* — never with the
name of an inventory row the text happened to match. A term is tagged **New**,
or, when it matches an item already on the list, shows *Matches your item …*,
and **Use** acts on that:

- **New** — **Use** fills the **Add by hand** form below with the term as the
  name, the card's store and product link, and a target of 1; pick the zone
  (and adjust the quantities) and tap **Add item**. Nothing is saved until
  then, and the card then reads **Added**.
- **Matches your item** — **Use** opens a compact confirm row on the card —
  a **zone** combo (the inventory's existing zones) plus **have** and
  **target** quantities, prefilled from the item's current row. Tapping
  **Update item** writes the product's URL to that item's `buscador` and
  applies the store, zone, and quantities; **Add as new item** instead hands
  the card to the Add by hand form under the term you searched.

**Nothing is auto-picked**; you always choose. A term the
stores don't carry (e.g. *"añade flurbos"*) simply returns no cards. The search
runs on demand with a live elapsed timer and a Cancel button (it drives a real
browser, so it takes seconds-to-minutes) and reuses the same `automation/`
launch helpers and shared-profile serialization as the cart automation.
Each run's phases (parse model + time, browser wait, per-store elapsed and
failures) are appended to `logs/product_search.log` (gitignored) — the webapp
has no console, so that file is where a slow or empty search is diagnosed.

> **Pre-requisites:** the stores must be logged in
> (`python -m automation.bootstrap_session`), and the LLM hub (`:8000`) +
> whisper-server (`:8090`) running for the spoken-term parse/transcription.

### 🏪 Prices (Items tab)
The **Prices** segment under **Items** (the Stores mode until #256) is where you decide which supermarket each item is bought from, using the latest [supermarket benchmark](#-supermarket-benchmark):
- **Monthly cost** (the glance card, first on the page, #257) — answers "am I on the cheapest plan?": the what-if total for your picks and how much it saves (or costs) a month against **today** — normally the stores you bought from when the benchmark ran, so the saving stays visible after you apply a switch — at a chosen ordering frequency (weekly, 2-weekly or monthly). Its one action is **Load recommended plan**, which becomes **Review & apply (N)** once a pick differs from your list. Its footer row, **Benchmark**, shows when the next review is due (*Review due* once it has passed) and opens the Benchmark sheet.
- **Benchmark sheet** — **Latest run**: which run the prices come from, how old it is, when the next review is due (run date + 90 days), how many stores it covers and how many overrides of yours are in force. **How to refresh the prices** folds out the three steps (run `/supermarket-benchmark` in Claude Code in this repo → **Import latest run** → review **Needs checking**) with a **Copy steps** button; the app never launches the benchmark itself, since it needs your quality-spec confirmation and about an hour of agents. **Import latest run**, the sheet's one primary, fills in every item's per-store product links (it never overwrites a link you set, and warns about links that look broken). **Plan**: **Load recommended plan** / **Reset to my list** replace the what-if picks (the what-if starts from the stores saved in your list). **Cost breakdown**: goods, delivery and total, what-if next to today, per store (stores without a cart handler are marked *manual order*) and with the fee-optimised totals. Items with no price at the picked store are listed under **No price** and left out of the totals, never counted as 0 €. Items with target 0 aren't bought, so they are left out of **both** sides (a store kept only by them would otherwise add a delivery fee nobody pays); the breakdown says how many.
- **Set as baseline** (#183, in the cost breakdown) freezes your list's current stores as the new "today", once a real switch has actually happened and the benchmark's stores are no longer the status quo — the next what-if then measures from there, so an unrelated later pick doesn't show yesterday's already-applied saving all over again. It confirms first (and says how many items in the review worklist are still unchecked); once set, the hint reads *"Today = your stores as of \<date> (baseline)"* with a **Reset to benchmark** control. A baseline only applies while it's still tied to the latest benchmark run — a newer run supersedes it (it's then ignored and flagged as out of date). The review worklist (the **moved** flag, the quantity check's *before*, which store counts as *benchmarked from*) keeps comparing against the benchmark run regardless, until a new one is imported.
- **Item rows** — each row has a store picker and, under it, one link pill per store that wraps onto as many lines as it needs (opens the product; the picked store is highlighted; each pill shows the item's monthly €, e.g. `€12.80/mo`). Tapping the row opens the item sheet on its **Stores & prices** page (below). Rows show only the stores your list buys from; the **Show all stores** switch reveals every benchmarked store. A dashed pill with a magnifier opens a store *search* page (Bonpreu and Alcampo blocked the benchmark's browser, so only search URLs were recorded); a dashed pill with an alert icon is a URL missing the store's product id. The search box above the list (also used to search every item on Home, and to filter Audit and the Items list) filters these rows by item name, zone or supermarket.
- Picks are only a what-if (remembered on this device) until **Review & apply…**, which shows each change with the old → new product and pack, and the target **and in-stock count** converted by pack size (both editable), then writes `super`, `buscador`, `cantidad` and `tenemos` in one save.
- **All · Needs checking · Checked** — filter pills above the list (the choice is remembered on this device). An item needs checking when it moved store since the benchmark, its pack changed ×2 or more, the units differ, its link is a search page or looks broken, it has an override (or the benchmark changed under one), or its in-stock count may still be in the old packs. Flagged rows carry a badge with the first reason (`Moved store +2`).
- **Store** — a second pill row with one toggle per store your list buys from and its item count (`Ametller 37 · Carrefour 37 · Mercadona 87`); pick one or several to list only the items bought there today (none picked = every store). It combines with the status pills and the search box, is remembered on this device, and never changes the simulator's totals — handy for re-picking a whole store's items to see whether dropping it is worth it.
- **Only items I buy (target above 0)** — a switch beside **Show all stores** that hides rows whose target (`cantidad`) is 0: they stay in your list but are never ordered. It combines with the status and Store pills (the Store counts then count only the items you buy), is remembered on this device, and an item whose target you set to 0 in its item sheet drops out at once.
- **Item sheet: Stores & prices and Quantity check** (a Prices row tap opens Stores & prices; the sheet's main page names what needs checking on its Stores & prices row) — a full-height sheet on a phone, wide on a desktop for the store table. Stores & prices shows the reasons as badges and a **Checked** switch, stored server-side so your progress is the same on every device. **Quantity check** puts the switch in real units — *Before: Ametller Origen 4 × 0.38 kg = 1.52 kg · Now: Carrefour 2 × 1 kg = 2 kg (+32%) · you use ≈ 3.7 kg a month* — beside the target and in-stock count as the sheet holds them, with a one-tap **Use suggested** when the packs convert; the steppers and the sheet's **Save** write them to `list.xlsx`. Its store table lists every store with a record, link or override: product, a labelled link to its page, pack, pack price, €/unit, €/month and status. Each store's pencil edits its link and, for basket items, **your override** (product name, pack size, unit, pack price, note); overridden values are marked *yours* next to the benchmark's, **Reset to benchmark** drops them, and the simulator totals and chip prices follow at once. A product the cart picks a page option for — the dorada's **Cut: Entero limpio** at Carrefour — shows it as a chip on that store's row (set in `config/product_options.json`, see `automation/README.md`). **Evidence & quality spec** folds out the tier, spec and each store's research notes.

**Quarterly flow:** run `/supermarket-benchmark` → open the app → **Items → Prices** → **Benchmark** → **Import latest run** → **Load recommended plan** → **Review & apply** → work through **Needs checking**, marking each item **Checked**.

### 🛒 Shopping List
**Fill carts first:** the **Shop** tab opens on the **Carts** glance card — one row per store on the list with what to buy and how the last fill went ("12 to buy · 11 in cart", a "1 not in cart" chip when it left something out), the one **Fill carts** button, the run's progress and outcome, and the run log folded underneath. Its **Options** row opens a sheet with the store, cart mode, dry run and the clean-cart confirm, and says in plain words what will run. When a fill leaves items out, Shop's header names them and the **Shop** tab carries a count badge until you next open Shop. The last fill is read from the run records under `purchase_logs/runs/`, so it survives a restart — see [Browser Automation](#-browser-automation).

**The list:** below the glance, the items to buy are grouped by supermarket, each group folded with its done/total count. A row has a leading Complete check (tap again to undo) and a trailing "Open buy link" icon.

**Add to a store:** the last row of each group adds an ad-hoc item (name + quantity) to that store's list. These are session-only and support the same Complete check.

**Store options:** the row under it opens the store sheet — the **already in cart** items/units counters (for things put in the cart before you opened the app; the group's totals update at once), the ad-hoc items with their **Remove**, and **Unmark all** for that store.

### 💾 Save / Export
Manual save to Excel or download as CSV, plus summary statistics.

## 🤖 Browser Automation

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
[`automation/README.md`](automation/README.md) for details.

**Run it from the app:** ⚙️ **Settings** (the header gear) → **Store logins** has a **Log in to stores**
button — it opens the same Chrome window without needing a terminal; log into
each store, then close the window.

**Check the logins (read-only):** **Check logins** in Settings → Store logins (each store's result stays on the row and in its sheet), or
`& .\.venv\Scripts\python.exe -m automation.check_logins`, reports each store
as logged in, logged out or unknown without touching a cart (log of the last
app-run check: `logs/check_logins.log`). A failed cart run names its cause —
login redirect, not logged in, bot challenge, or an unexpected API answer —
see [`automation/README.md`](automation/README.md).

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
Details: [`automation/README.md`](automation/README.md) → "Cart guard".

A walk-through of how this automation was built, store quirk by store quirk,
is in [`docs/browser-automation-build.md`](docs/browser-automation-build.md).

## 📊 Supermarket Benchmark

The `benchmark/` package answers *"is our Mercadona + Ametller split still the
cheapest way to buy this basket without dropping quality?"* It prices the
household's **average monthly basket** (from `purchase_logs/`) at every online
supermarket in `benchmark/stores.json`, with quality locked per item (brand,
meat %, ingredients) and every product compared per kg, litre or piece, and scores status
quo vs single-store vs the best 1–5-store splits including delivery fees. Each
run is recorded in a history (`benchmark_runs/_state/`), so the report shows
how prices and the recommendation move over time.

Re-run it with the project skill **`/supermarket-benchmark`** (Claude Code) —
it rebuilds the basket, fans out one research agent per store, scores and
publishes a recommendation. The building blocks run standalone too:

```powershell
& .\.venv\Scripts\python.exe -m benchmark.build_basket                 # benchmark_runs/<today>/basket.json
& .\.venv\Scripts\python.exe -m benchmark.results status --run benchmark_runs/<date> --store dia
& .\.venv\Scripts\python.exe -m benchmark.score benchmark_runs/<date>   # scenarios.json + report.md
& .\.venv\Scripts\python.exe -m benchmark.score benchmark_runs/<date> --promote   # finalise: mappings + history
& .\.venv\Scripts\python.exe -m benchmark.report benchmark_runs/<date>  # report.html (history included)
& .\.venv\Scripts\python.exe -m benchmark.history show                 # one line per past run
```

`benchmark_runs/` is gitignored (prices, basket and product mappings stay
local). Methodology, quality tiers and schemas:
[`docs/supermarket-benchmark.md`](docs/supermarket-benchmark.md).

### Per-store links, store picks and the cost simulator

Each inventory row can hold one product URL per supermarket in optional
`url_<store>` columns of `list.xlsx` (store keys from `benchmark/stores.json`;
a sheet without them loads unchanged). `super` + `buscador` stay the chosen
store and its URL, so the cart automation keeps working. `src/store_links.py`
does the work; the API (`app/routers/stores.py`) exposes it:

| Route | Does |
|---|---|
| `POST /api/stores/import-latest` | Seeds `url_<store>` from `benchmark_runs/_state/mappings/` (and each row's own `buscador`), matched by `item_key(comida)`. Never overwrites a non-empty cell; returns `seeded` / `skipped_existing` / `unmatched`. Re-run after every benchmark. |
| `PUT /api/items/{id}/store-url` | Sets or clears one store's URL for one item. |
| `POST /api/items/{id}/pick` | Makes a store the item's chosen one (`super` + `buscador` together). |
| `GET /api/stores` | Store registry, which stores have a cart handler, frequency presets, and the benchmark status: latest `run_date`, `age_days`, `next_due` (run date + 90 days), `stores_covered` and the `overrides` count. |
| `GET /api/stores/recommended` | The latest run's recommended plan as item → store picks. |
| `POST /api/stores/simulate` | Monthly goods + delivery (normal and fee-optimised) per store and overall for a set of picks vs today's stores, at a chosen frequency (weekly default). Unpriced picks are listed and excluded, never counted as 0 €. |
| `POST /api/stores/apply-preview` | Per changed item: from/to store, old/new pack and product name (`old_name` / `new_name`), the converted target `cantidad`, and the stock `tenemos` converted by the same pack factor (rounded to nearest, next to `tenemos_from`). |
| `POST /api/stores/apply` | Writes `super`, `buscador` and `cantidad` — and `tenemos` when a change carries it — for the given changes in one save. |
| `GET /api/items/{id}/store-detail` | Everything the app believes about one item: the list row, the basket product it was benchmarked against (store, pack, price, run-time target), monthly use, spec/tier, one entry per store (product, pack, price, €/unit, monthly €, status, confidence, quality, evidence, link and its kind, and whether the values are the benchmark's, your override or link-only), the before → now quantity maths in real units, review flags and the checked date. |
| `PUT` / `DELETE /api/items/{id}/store-override` | Save your own `name` / `pack_size` / `unit` (kg, l, ud, m) / `pack_price` / `note` for one basket item at one `store`, or drop it (`?store=`) to go back to the benchmark. Returns the store detail. |
| `PUT /api/items/{id}/checked` | Marks an item reviewed (`{"checked": true}`) or not. |
| `GET /api/stores/checks` | Items that need checking and why: `moved`, `pack_x2`, `unit_mismatch`, `search_link` / `suspect_link`, `override`, `benchmark_changed`, `stock_unconverted`; per-flag counts; checked dates. |

Prices come from the newest *scored* run (one with `scenarios.json`); the fee
maths is `benchmark.score`'s own. The PWA's **Items → Prices** view
([🏪 Stores](#-stores-items-tab)) is the front end for all of it.

**Your corrections (overrides).** When your own research disagrees with the
benchmark — a different pack, a new price, another product — save it as an
override instead of editing the benchmark. Overrides and the review's
*checked* marks live in `benchmark_runs/_state/overrides.json` (gitignored,
like the rest of `benchmark_runs/`). An override replaces only the fields you
give; the rest stay the benchmark's. The simulator, the per-item prices and
the pack conversion on apply all use it. The benchmark's values at the time
are kept with it, so when a later run changes them the item is flagged
`benchmark_changed`. A corrupt file is ignored (with a warning) and moved
aside to `overrides.corrupt-<time>.json` on the next write. `stock_unconverted`
is a best guess, since nothing records whether stock was recounted: the item
moved store, the packs differ, you have stock, and you haven't checked it yet.

## 🖥️ Typical Workflow

1. **Set targets** — in **Items**, tap an item and set its target
2. **Audit inventory** — walk through zones and update current stock
3. **Shopping List** — check what to buy, mark as bought while shopping
4. Use **cart offset counters** if items were already in the cart
5. Use **quick-add** for anything not in the system

## 🐛 Troubleshooting

| Issue | Fix |
|-------|-----|
| Excel file not found | Verify the path in `src/config.json` |
| Permission error on save | Close Excel before running the app |
| Interface appears broken | Clear browser cache |
| Config errors | Validate `src/config.json` is well-formed JSON |
| Microphone shows "An error has occurred" on mobile | App must be opened over **HTTPS** — see HTTPS setup section above |
| Browser says "Your connection is not private" on desktop | Re-run `& .\.venv\Scripts\python.exe src\gen_ssl_cert.py` — it installs the cert into Windows trust store |
| Browser says "Your connection is not private" on mobile | Self-signed cert warning — tap **Advanced → Proceed** once per device |
| HTTPS cert missing / app won't start | Run `& .\.venv\Scripts\python.exe src\gen_ssl_cert.py` (or `scripts\gen_tailscale_cert.py`) from the repo root, then restart |
| `https://localhost:8502` shows cert mismatch after running `gen_tailscale_cert.py` | Expected — the Tailscale cert is issued for the Tailscale hostname, not `localhost`. Use `http://localhost:8502` locally or `https://<host>.ts.net:8502` over Tailscale |
| `tailscale cert` fails with permission error | Enable HTTPS Certificates in the Tailscale admin console: **DNS → HTTPS Certificates** |

---

*Built for efficient household inventory management and grocery shopping.*
