# Grocery Shopping Automation

Mobile-responsive Streamlit application for managing a household grocery
inventory and shopping list. Excel-backed, room-by-room auditing, real-time
purchase tracking, and an optional voice-narrated audit mode powered by a
local whisper-server + LLM hub.

## ✨ Features

- **Mobile-first UI** — open the app on your phone over local Wi-Fi and
  walk the house updating stock with ±1 buttons.
- **Six modes**: Audit Inventory, Audio Audit (voice), Edit Targets, Edit
  Item, Add Item, Shopping List, Save / Export.
- **Per-supermarket shopping list** with progress bars, cart-offset
  counters, and an inline quick-add form.
- **Auto-save** to Excel after every change, with rollback if the file is
  open in Excel or locked by OneDrive.
- **Audio Audit (optional)** — record a 2–3 minute Spanish narration of
  the inventory walk; whisper transcribes it locally and a local LLM hub
  matches the phrases to the candidate list before you accept the proposed
  changes.

## 🚀 Quick start

### 1. Clone and create a virtual environment

```powershell
git clone https://github.com/ferraroroberto/grocery-shopping-automation
cd grocery-shopping-automation
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```

POSIX equivalent:

```bash
python -m venv .venv
./.venv/bin/pip install -r requirements.txt
```

### 2. Bring your own inventory file

A small synthetic example is shipped at `data/list.example.xlsx`. Copy it
to `data/list.xlsx` and edit it to match your household:

```powershell
Copy-Item data\list.example.xlsx data\list.xlsx
```

The path is configurable in `config.json` (`data.xlsx_file`) — set it to
an absolute path if you'd rather keep the file outside the repo (e.g. on
OneDrive).

### 3. Run

Double-click `launcher.bat` (Windows), or:

```powershell
.\.venv\Scripts\python -m streamlit run app/app.py
```

The dashboard opens at `http://localhost:8501`. The sidebar's **📋 Copy
link** button gives you a `http://<lan-ip>:8501` URL you can paste into
Telegram (or any messaging app) to open on your phone over the same Wi-Fi.

> **Firewall:** if the phone can't connect on first use, run this once in
> PowerShell (admin):
> ```powershell
> New-NetFirewallRule -DisplayName "Streamlit Grocery" -Direction Inbound -Protocol TCP -LocalPort 8501 -Action Allow
> ```

## 📊 Data format

The Excel file must have these columns (Spanish names — the rest of the
app is in English, but the data layer is column-name-agnostic via
`config.json` if you want to rename them):

| Column     | Meaning                                                   | Notes    |
|------------|-----------------------------------------------------------|----------|
| `super`    | Supermarket name (e.g. `mercadona`, `ametller`)           | Required |
| `buscador` | Buy-link URL (search page or product page)                | Optional |
| `lugar`    | Zone in the house (e.g. `nevera`, `despensa`, `garaje`)   | Required |
| `comida`   | Item name                                                 | Required |
| `cantidad` | Target quantity to maintain                               | Required |
| `tenemos`  | Current quantity on hand                                  | Required |
| `comprar`  | Auto-calculated: `max(0, cantidad − tenemos)`             | Auto     |

The example fixture at `data/list.example.xlsx` covers all six default
zones (`nevera`, `congelador`, `despensa`, `estante`, `garaje`,
`bajo escalera`) and two supermarkets (`mercadona`, `ametller`). Add /
remove zones and supermarkets freely — the UI reads them dynamically from
the file.

## 📱 Modes

### 🔍 Audit Inventory
Walk through each zone, update current stock with ±1 buttons. Auto-saves
every change. Best from mobile in landscape.

### 🎙️ Audio Audit *(optional, requires `claude-local-calls`)*
Walk the house dictating the inventory in Spanish (*"ahora en la nevera,
dos yogures, un litro de leche…"*). Audio is transcribed by a local
whisper-server (`:8090`) and matched against the list by a local LLM hub
(`:8000`). See [`audio_audit.md`](audio_audit.md).

> Both services are provided by
> [`claude-local-calls`](https://github.com/ferraroroberto/claude-local-calls).
> Without them, the rest of the app still works — Audio Audit just shows a
> clear "service unreachable" banner.

### ✏️ Edit Targets
Adjust target quantities per item. Auto-saves.

### 🔧 Edit Item
Search for any item and edit all its fields, or delete it.

### ➕ Add Item
Form-based creation of new items.

### 🛒 Shopping List
Items to buy, grouped by supermarket, with per-store progress bars,
cart-offset counters (for items already in the cart before opening the
app), and an inline quick-add form.

### 💾 Save / Export
Manual save to Excel, CSV download, summary metrics.

## 🏗️ Project layout

```
.
├── app/                        Streamlit UI (one file per mode)
│   ├── app.py                  Entry point: page config, sidebar, routing
│   ├── audit.py
│   ├── audio_audit.py
│   ├── edit_targets.py
│   ├── edit_item.py
│   ├── add_item.py
│   ├── shopping.py
│   ├── export.py
│   └── ui_helpers.py           CSS, formatters, save-error renderer
├── src/                        Non-UI logic (no streamlit imports)
│   ├── data.py                 Config, XLSX I/O, shared mutators
│   ├── transcribe_client.py    Whisper client
│   └── inventory_extract.py    LLM matching client (anthropic SDK → hub)
├── test_data/
│   ├── smoke_phase1.py         Fixture round-trip
│   ├── smoke_phase2.py         End-to-end LLM smoke (needs hub running)
│   └── smoke_lock_contention.py
├── data/
│   └── list.example.xlsx       Synthetic example inventory
├── .streamlit/config.toml      Theme
├── audio_audit.md              Per-mode docs for the voice flow
├── config.json                 App / data / UI / audio-audit settings
├── launcher.bat                Windows double-click launcher
├── requirements.txt
├── CLAUDE.md / AGENTS.md       Instructions for AI coding agents
└── LICENSE                     MIT
```

## ⚙️ Configuration

Edit `config.json`:

- `data.xlsx_file` — path to your inventory file (relative to repo root, or
  absolute).
- `data.columns` — rename the Excel columns if you want non-Spanish names.
- `ui.modes` — relabel the modes shown in the sidebar.
- `audio_audit.*` — whisper / LLM endpoints, model id, count clamp.

## 🧪 Verification

```powershell
# Compile check
.\.venv\Scripts\python -m py_compile app\app.py app\audio_audit.py src\data.py

# Phase-1 smoke (no external services)
.\.venv\Scripts\python test_data\smoke_phase1.py

# Save-failure rollback test
.\.venv\Scripts\python test_data\smoke_lock_contention.py

# End-to-end audio-audit smoke (requires claude-local-calls running)
.\.venv\Scripts\python test_data\smoke_phase2.py
```

## 📝 License

MIT — see [`LICENSE`](LICENSE).
