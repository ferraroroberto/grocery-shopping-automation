# Household Inventory & Shopping Helper

Mobile-responsive web app for managing household grocery inventory with intelligent shopping list generation and real-time purchase tracking. The primary surface is a **FastAPI + vanilla-JS PWA** served on `:8502`; a legacy **Streamlit** app on `:8501` remains available and drives the same modes against the same Excel-backed `src/data.py` layer.

## 📋 Project Summary

Comprehensive household inventory management across multiple operational modes. Audit current stock room-by-room, edit target quantities, track shopping in real time, and add on-the-fly items directly to the shopping list.

**Key Features:**
- Mobile access over local Wi-Fi — use the **Copy link** button in the sidebar to get the URL and open it on your phone
- Room-by-room inventory auditing with auto-save (best done from mobile)
- Shopping list grouped by supermarket with per-store progress bars (best done from desktop)
- Cart offset counters to account for items already in the cart
- Quick-add items (name + quantity) to any supermarket's shopping list
- Excel-based data storage with automatic calculations
- Cross-platform compatibility (works on any device with a browser)

## 🏗️ Project Structure

- **`app/`** — UI layers.
  - `api.py` — FastAPI entrypoint (the primary app): inventory, audit, edit/add, shopping, automation, and audio-audit endpoints.
  - `middleware.py` — bearer-token auth for non-loopback (remote) requests.
  - `static/` — the PWA front end (`index.html`, `app.js`).
  - `automation_runner.py` — shared subprocess plumbing that streams the cart-automation CLI into the app.
  - `app.py` — legacy Streamlit entrypoint (page config, session state, sidebar, mode routing).
  - `audit.py` / `audio_audit.py` / `edit_targets.py` / `edit_item.py` / `add_item.py` / `shopping.py` / `export.py` / `ui_helpers.py` — Streamlit per-mode modules, each exposing `main(df)`.
- **`src/`** — UI-free data/business layer.
  - `data.py` — config loading, XLSX load/save, supermarket stats, quantity mutators.
  - `gen_ssl_cert.py` — generate a local CA + server cert for HTTPS.
  - `inventory_extract.py` / `transcribe_client.py` — audio-audit transcription + LLM extraction (via the `claude-local-calls` hub).
  - `webapp_config.py` — remote-access (token/password) config loader.
  - `config.example.json` — committed template; copied to `src/config.json` (gitignored) on first run.
- **`automation/`** — Playwright + real-Chrome browser cart automation (see `automation/README.md`).
- **`scripts/`** — `gen_token.py`, `set_password.py` (remote auth), `run_named_tunnel.py` (Cloudflare).
- **`webapp/`** — `cloudflared.sample.yml` and the gitignored `certificates/`.
- **`config/`** — `webapp_config.sample.json` template.
- **`data/`** — `list.example.xlsx` sample inventory.
- **`docs/`** — design records and the browser-automation build walk-through.
- **`tests/`** — manual smoke tests (`smoke_*.py`, `automation_smoke_*.py`).
- **Launchers** — `webapp.bat` (FastAPI/PWA on `:8502`), `launch_app.bat` (legacy Streamlit on `:8501`), `webapp_tunnel_named.bat` (FastAPI + Cloudflare tunnel).
- **`.streamlit/config.toml`** — Streamlit theme customization (legacy app).

## 🚀 Quick Start

### Prerequisites
- Python 3.10+ with the project `.venv`
- Install dependencies: `& .\.venv\Scripts\pip.exe install -r requirements.txt`

### Launch the app (FastAPI/PWA — recommended)

Double-click `webapp.bat`, or from the repo root:

```powershell
& .\.venv\Scripts\pip.exe install -r requirements.txt
webapp.bat
```

The FastAPI app on `:8502` covers the inventory dashboard, audit, target editing, item editing, item creation, shopping mode, automation controls, and the audio-audit workflow against the Excel-backed `src/data.py` layer. Open `http://127.0.0.1:8502` when no local cert exists, or `https://127.0.0.1:8502` after running `& .\.venv\Scripts\python.exe src\gen_ssl_cert.py`. The launcher binds to `0.0.0.0`, so the same port is reachable over LAN or Tailscale from devices that can reach this PC.

### Legacy Streamlit app

The original Streamlit UI is still available on `:8501` and drives the same modes. Double-click `launch_app.bat`, or from the repo root:

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

Mobile browsers block microphone access on plain HTTP. The app serves over HTTPS using a self-signed certificate stored in `certificates/` (gitignored). `webapp.bat` picks the cert up automatically and starts uvicorn with TLS on `:8502`.

**First-time setup** (run once from the repo root):
```powershell
& .\.venv\Scripts\python.exe src\gen_ssl_cert.py
```

This detects all local IP addresses (LAN + Tailscale) and writes three files to `certificates/` (valid 10 years):
- `ca.pem` — local CA certificate, installed into Windows `CurrentUser\Root` (no admin required)
- `cert.pem` — server certificate signed by that CA (passed to uvicorn as `--ssl-certfile`)
- `key.pem` — server private key (passed to uvicorn as `--ssl-keyfile`)

Chrome and Edge on this PC will show no security warning because the CA is trusted.

**Accepting the cert on mobile (one-time per device):**  
Open `https://<local-ip>:8502` — the browser will warn "Not secure". Tap **Advanced → Proceed to … (unsafe)**. You won't be asked again on that device.

**If your PC's IP changes**, regenerate and reinstall with the same command above, then restart the app.

## ⚙️ Configuration

Edit `config.json` to customize:
- **Data Paths** — Excel file location and column mappings
- **UI Settings** — Page config, mode labels, layout
- **Logging** — Log level and format

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
Walk the house dictating the inventory in Spanish (*"ahora en la nevera, dos yogures, un litro de leche…"*). The audio is transcribed by the local whisper-server and matched against the inventory by the local LLM hub — same `claude-local-calls` services that power the rest of this monorepo. The record view shows a per-zone, alphabetical checklist of tracked items so nothing gets missed while dictating.

> **Pre-requisites:** the hub on `:8000` and whisper-server on `:8090` must be running. Start them via `E:\automation\claude-local-calls\run_hub.bat` and `launchers\run_whisper.bat`, or its tray launcher.

### ✏️ Edit Targets
Set or adjust target quantities per item. Auto-saves every change.

### 🔧 Edit Item
Search for any item and edit all its fields (name, supermarket, zone, URL, quantities) or delete it.

### ➕ Add Item
Add new items to the inventory via a form.

### 🛒 Shopping List
View items that need to be purchased, grouped by supermarket.

**Cart offset counters (sidebar):**
Each supermarket shows an editable `＋items` and `＋units` counter below its progress bar. Use these when items were already placed in the physical cart before opening the app — the bar and totals update immediately to reflect the combined count.

**Quick-add items:**
At the bottom of each supermarket's expander, a small inline form lets you add ad-hoc items (name + quantity). These are session-only and support the full `✅ Got it` / `↩️ Undo` / `🗑️ Remove` workflow. Works for both Ametller and Mercadona (and any other supermarket in the list).

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
optionally tick *Dry run*, and click **▶ Run Automation**. Output streams live
into the page and a **🛑 Stop** button cancels an in-progress run. From a
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
| Excel file not found | Verify the path in `config.json` |
| Permission error on save | Close Excel before running the app |
| Interface appears broken | Clear browser cache |
| Config errors | Validate `config.json` is well-formed JSON |
| Microphone shows "An error has occurred" on mobile | App must be opened over **HTTPS** — see HTTPS setup section above |
| Browser says "Your connection is not private" on desktop | Re-run `& .\.venv\Scripts\python.exe src\gen_ssl_cert.py` — it installs the cert into Windows trust store |
| Browser says "Your connection is not private" on mobile | Self-signed cert warning — tap **Advanced → Proceed** once per device |
| HTTPS cert missing / app won't start | Run `& .\.venv\Scripts\python.exe src\gen_ssl_cert.py` from the repo root, then restart |

---

*Built for efficient household inventory management and grocery shopping.*
