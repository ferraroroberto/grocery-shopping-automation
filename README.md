# Household Inventory & Shopping Helper

Mobile-responsive web app for managing household grocery inventory with intelligent shopping list generation and real-time purchase tracking. The primary surface is a **FastAPI + vanilla-JS PWA** served on `:8502`; a legacy **Streamlit** app on `:8501` remains available and drives the same modes against the same Excel-backed `src/data.py` layer.

## 📋 Project Summary

Comprehensive household inventory management across multiple operational modes. Audit current stock room-by-room, edit target quantities, track shopping in real time, and add on-the-fly items directly to the shopping list.

**Key Features:**
- Mobile access over local Wi-Fi — use the **Copy Link** button in the ⚙️ Settings tab to get the URL and open it on your phone
- Room-by-room inventory auditing with auto-save (best done from mobile)
- Shopping list grouped by supermarket with per-store progress bars (best done from desktop)
- Cart offset counters to account for items already in the cart
- Quick-add items (name + quantity) to any supermarket's shopping list
- Excel-based data storage with automatic calculations
- Cross-platform compatibility (works on any device with a browser)

## 🏗️ Project Structure

- **`app/`** — UI layers.
  - *Primary (FastAPI/PWA on `:8502`):* `api.py` (entrypoint — inventory, audit, edit/add, shopping, automation, and audio-audit endpoints), `middleware.py` (bearer-token auth for non-loopback requests), `static/` (PWA front end: `index.html`, `app.js`, `styles.css`, `_vendored/` fleet components), `automation_runner.py` (shared subprocess plumbing that streams the cart-automation CLI into the app).
  - *Legacy (Streamlit fallback on `:8501`):* `app.py` (entrypoint — page config, session state, sidebar, mode routing), `audit.py` / `audio_audit.py` / `edit_targets.py` / `edit_item.py` / `add_item.py` / `shopping.py` / `export.py` / `ui_helpers.py` (per-mode modules, each exposing `main(df)`).
- **`src/`** — UI-free data/business layer.
  - `data.py` — config loading, XLSX load/save, supermarket stats, quantity mutators.
  - `gen_ssl_cert.py` — generate a local CA + server cert for HTTPS.
  - `inventory_extract.py` / `transcribe_client.py` — audio-audit transcription + LLM extraction (via the `claude-local-calls` hub).
  - `audio_audit_core.py` — UI-agnostic transcript cleaning + audit-log writer shared by the PWA and the legacy Streamlit mode.
  - `net.py` — LAN-IP / port-probe helpers shared by the FastAPI and Streamlit front ends.
  - `webapp_config.py` — remote-access (token/password) config loader.
  - `config.example.json` — committed template; copied to `src/config.json` (gitignored) on first run.
- **`automation/`** — Playwright + real-Chrome browser cart automation (see `automation/README.md`).
- **`scripts/`** — `gen_token.py`, `set_password.py` (remote auth), `run_named_tunnel.py` (Cloudflare).
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

Tray menu: **Open grocery** · **Copy local URL** · **Restart webapp** · **Status** · **Quit**. `webapp.bat` remains available as the manual/no-tray alternative — same app, no tray icon, no lifecycle management:

```powershell
& .\.venv\Scripts\pip.exe install -r requirements.txt
webapp.bat
```

The FastAPI app on `:8502` covers the inventory dashboard, audit, target editing, item editing, item creation, shopping mode, automation controls, and the audio-audit workflow against the Excel-backed `src/data.py` layer. Open `http://127.0.0.1:8502` when no local cert exists, or `https://127.0.0.1:8502` after running `& .\.venv\Scripts\python.exe src\gen_ssl_cert.py`. Either launcher binds to `0.0.0.0`, so the same port is reachable over LAN or Tailscale from devices that can reach this PC.

The PWA follows the fleet design system (`~/.claude/design.md` + `design.dark.md`): a floating bottom-tab pill on the phone (inline top tabs on desktop) with six tabs — **Inventory · Shopping · Audit · Items · Auto · Settings** (Audio Audit lives as a sub-pill under Audit; Targets / Edit Item / Add Item under Items) — vendored fleet components under `app/static/_vendored/`, and a light/dark **theme toggle in the top bar** (moon/sun icon) that remembers your choice. The utility actions (Open Spreadsheet, Copy Link, Export CSV, Close App) live in the ⚙️ **Settings tab**; heavy cards (the dashboard item list, the per-store shopping panels, the audio zone checklist) are collapsible and folded by default; the search box appears only on the modes that filter the item list. A footer line shows the running build (`Build: <git sha> · <time>`, from `/api/version`) so you always know which deploy the app is serving, and the shell auto-reloads once when it detects a newer build.

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
& .\.venv\Scripts\python.exe scripts\set_password.py 816215
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
https://tower.tail1121fd.ts.net:8502
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

### Telegram notifications

The app can push short Telegram messages (e.g. purchase/delivery alerts) through
the universal `src/notify/` component, vendored verbatim from
`project-scaffolding` (see `src/notify/README.md`). This step only wires up the
notifier and proves delivery works — no alert content or trigger is wired to it
yet.

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

The PWA's Auto tab drives this via the **Email Watch** card: pick the
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
| `super` | Supermarket name (e.g., `mercadona`, `ametller`) | Required |
| `buscador` | Product URL for online shopping | Optional |
| `lugar` | Zone in the house (e.g., `fridge`, `pantry`) | Required |
| `comida` | Item name | Required |
| `cantidad` | Target quantity to maintain | Required |
| `tenemos` | Current quantity on hand | Required |
| `comprar` | Auto-calculated: `max(0, cantidad − tenemos)` | Auto |

## 📱 Modes

### 🔍 Audit Inventory
Walk through each zone of the house, update current stock levels with ±1 buttons. Auto-saves every change to Excel.
Best done from mobile — rotate to **landscape** for optimal layout.

### 🎙️ Audio Audit
Walk the house dictating the inventory in Spanish (*"ahora en la nevera, dos yogures, un litro de leche…"*). The audio is transcribed by the local whisper-server and matched against the inventory by the local LLM hub — same `claude-local-calls` services that power the rest of this monorepo. The record view shows a per-zone, alphabetical checklist of tracked items so nothing gets missed while dictating. A service-status banner reports recorder/hub/whisper reachability. A **Match model** selector picks which hub model performs the match (defaults to `gemini_pro`, configurable via `audio_audit.llm_model` / `llm_models_available`). Transcribe and Match show a **live elapsed timer** with staged progress and a **Cancel** button (calls budget up to 10 min — `audio_audit.llm_timeout`), and surface errors inline instead of failing silently. The review groups detected items by zone with current→new/Δ/evidence and a "not mentioned in audited zones → set 0" section; applying writes a JSON audit log to `audio_audit_logs/`.

**Hardened recording (never lose a take).** Recording does **not** buffer the whole take on the phone. Each 1-second chunk is streamed to the PC and archived to disk the moment it arrives by the sibling **voice-transcriber** app, so the audio survives even if the phone dies or the page is backgrounded mid-walk. The transcript fills in live as you talk (rolling partials over SSE when the voice-transcriber has them enabled), and **Stop** returns the canonical transcript ready to Match. The status line shows elapsed time and bytes streamed to the PC. **Redo** re-runs whisper on the saved audio (crash recovery, or after a transient transcribe error); **Clear** resets everything for the next audit. grocery only *proxies* the recording lifecycle (`POST /api/audio/session` → `…/chunk` → `…/events` SSE → `…/finish` → `…/retranscribe`) to the voice-transcriber session API on loopback — it never re-implements recording or transcription. The recorder URL is `audio_audit.voice_transcriber_url` (default `https://127.0.0.1:8443`). (The old Choose-File upload fallback is gone from the UI — record, Redo, or paste a transcript; `POST /api/audio/transcribe` still accepts a direct file upload for scripted use.)

> **Pre-requisites:** the **voice-transcriber** webapp must be running (it boots from its tray) for live recording — when it's down the audio view shows a clear banner and disables Record instead of hanging. The hub on `:8000` and whisper-server on `:8090` must also be running (start them via `E:\automation\claude-local-calls\run_hub.bat` and `launchers\run_whisper.bat`, or its tray launcher). **`ffmpeg` must be on `PATH`** (`winget install Gyan.FFmpeg`) for the direct-upload `/api/audio/transcribe` endpoint — uploaded webm/mp4 is transcoded to 16 kHz WAV before whisper (the streamed record path transcodes inside voice-transcriber).

### ✏️ Edit Targets
Set or adjust target quantities per item. Auto-saves every change.

### 🔧 Edit Item
Search for any item and edit all its fields (name, supermarket, zone, URL, quantities) or delete it.

### ➕ Add Item
Add new items to the inventory via a form.

### 🛒 Shopping List
View items that need to be purchased, grouped by supermarket.

**Cart offset counters:**
Each supermarket shows an editable `＋items` and `＋units` counter below its progress bar. Use these when items were already placed in the physical cart before opening the app — the bar and totals update immediately to reflect the combined count.

**Quick-add items:**
At the bottom of each supermarket's expander, a small inline form lets you add ad-hoc items (name + quantity). These are session-only and support the full `Got it` / `Undo` / `Remove` workflow. Works for both Ametller and Mercadona (and any other supermarket in the list).

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

**Run it from the app:** the **🛒 Shopping List** mode has a **🤖 Run
Automation** section — pick a store (or "All stores"), choose a **cart mode**,
optionally tick *Dry run*, and click **Run Automation**. Output streams live
into the page and a **Stop** button cancels an in-progress run. From a
terminal you can also run
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

A walk-through of how this automation was built, store quirk by store quirk,
is in [`docs/browser-automation-build.md`](docs/browser-automation-build.md).

## 🖥️ Typical Workflow

1. **Edit Targets** — set desired quantities for tracked items
2. **Audit Inventory** — walk through zones and update current stock
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
| `https://localhost:8502` shows cert mismatch after running `gen_tailscale_cert.py` | Expected — the Tailscale cert is issued for the Tailscale hostname, not `localhost`. Use `http://localhost:8502` locally or `https://tower.tail1121fd.ts.net:8502` over Tailscale |
| `tailscale cert` fails with permission error | Enable HTTPS Certificates in the Tailscale admin console: **DNS → HTTPS Certificates** |

---

*Built for efficient household inventory management and grocery shopping.*
