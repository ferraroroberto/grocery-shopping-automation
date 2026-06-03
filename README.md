# Household Inventory & Shopping Helper

Mobile-responsive Streamlit application for managing household grocery inventory with intelligent shopping list generation and real-time purchase tracking.

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

- **app.py** — Entry point: page config, session state, sidebar, mode routing
- **data.py** — Config, XLSX load/save, supermarket stats, quantity mutators
- **ui_helpers.py** — CSS, inline HTML formatters, sidebar utility actions
- **audit.py / edit_targets.py / edit_item.py / add_item.py / shopping.py / export.py** — One file per mode, each exposing `main(df)`
- **config.json** — Application configuration and UI settings
- **launcher.bat** — Windows batch file for easy app launching
- **.streamlit/config.toml** — Streamlit theme customization

## 🚀 Quick Start

### Prerequisites
- Python 3.8+
- Required Python packages: `streamlit`, `pandas`, `openpyxl`

### Method 1: Using the Batch File (Recommended)
Double-click `launcher.bat` in the grocery folder.

### Method 2: Manual Launch
```bash
pip install streamlit pandas openpyxl
cd E:\automation\automation\system\grocery
streamlit run app.py
```

### Mobile Access (same Wi-Fi network)
The app binds to all network interfaces automatically. To open it on your phone:
1. Launch `launcher.bat` on the PC as usual
2. Click **📋 Copy link** in the sidebar — this copies `https://<local-ip>:8501` to the clipboard
3. Paste the URL into Telegram (or any messaging app) and open it on your phone

> **Firewall:** if the phone cannot connect on first use, run this once in PowerShell (admin):
> ```powershell
> New-NetFirewallRule -DisplayName "Streamlit Grocery" -Direction Inbound -Protocol TCP -LocalPort 8501 -Action Allow
> ```

> **Audit mode on mobile:** rotate your phone to **landscape** for the best layout — the row-per-item grid fits without horizontal scrolling.

### HTTPS setup (required for microphone access on mobile)

Mobile browsers block microphone access on plain HTTP. The app is configured to serve over HTTPS using a self-signed certificate stored in `certificates/` (gitignored).

**First-time setup** (run once from the grocery folder):
```powershell
& e:\automation\automation\.venv\Scripts\python.exe gen_ssl_cert.py
```

This detects all local IP addresses (LAN + Tailscale) and writes three files to `certificates/` (valid 10 years):
- `ca.pem` — local CA certificate, installed into Windows `CurrentUser\Root` (no admin required)
- `cert.pem` — server certificate signed by that CA (used by Streamlit)
- `key.pem` — server private key (used by Streamlit)

Chrome and Edge on this PC will show no security warning because the CA is trusted.

**Accepting the cert on mobile (one-time per device):**  
Open `https://<local-ip>:8501` — the browser will warn "Not secure". Tap **Advanced → Proceed to … (unsafe)**. You won't be asked again on that device.

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
Walk the house dictating the inventory in Spanish (*"ahora en la nevera, dos yogures, un litro de leche…"*). The audio is transcribed by the local whisper-server and matched against the inventory by the local LLM hub — same `claude-local-calls` services that power the rest of this monorepo. The record view shows a per-zone, alphabetical checklist of tracked items so nothing gets missed while dictating. See [`audio_audit.md`](audio_audit.md) for recording technique, configuration, and troubleshooting.

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
| Browser says "Your connection is not private" on desktop | Re-run `python gen_ssl_cert.py` — it installs the cert into Windows trust store |
| Browser says "Your connection is not private" on mobile | Self-signed cert warning — tap **Advanced → Proceed** once per device |
| HTTPS cert missing / app won't start | Run `python gen_ssl_cert.py` from the grocery folder, then restart |

---

*Built for efficient household inventory management and grocery shopping.*
