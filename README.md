# Household Inventory & Shopping Helper

Mobile-responsive web app for managing household grocery inventory with intelligent shopping list generation and real-time purchase tracking. The primary surface is a **FastAPI + vanilla-JS PWA** served on `:8502`; a legacy **Streamlit** app on `:8501` remains available and drives the same modes against the same Excel-backed `src/data.py` layer.

Audit current stock room-by-room (by hand or by dictating), edit target quantities, track shopping in real time, fill the supermarkets' online carts with browser automation, and compare store prices with a quality-locked benchmark.

## Quick start

Prerequisites: Python 3.10+ with the project `.venv`.

```powershell
& .\.venv\Scripts\pip.exe install -r requirements.txt
tray.bat              REM start the webapp under the tray (no-op if already running)
tray.bat --restart    REM stop + start, to pick up new code
```

Open `http://127.0.0.1:8502` (or `https://` once a cert exists in `certificates/`). `webapp.bat` is the manual/no-tray alternative; `launch_app.bat` starts the legacy Streamlit app on `:8501`. The served host/port follows `config/webapp_config.json` (default `0.0.0.0:8502`), so the app is reachable over LAN or Tailscale; the `:8502` quoted throughout the docs is that default. Phone access, HTTPS (required for the microphone), remote access and launch details: [docs/running-and-access.md](docs/running-and-access.md).

**Restart recipe:** the tray owns the webapp and there is no hot-reload across `app/` or `src/` edits, so after changing either, run `tray.bat --restart`, then confirm the new build with `GET /api/version`'s `git_sha` == `git rev-parse --short HEAD` (the footer line in the app shows the same build). The legacy Streamlit app on `:8501` is not tray-managed.

**Email poller:** the confirmation-email poller (Settings → Email watch) runs only in the tray-launched webapp, which sets `GROCERY_EMAIL_POLLER=1`. Every other boot (`webapp.bat`, the tunnel launcher, a worktree, a fixture instance, pytest) leaves it off. To run it from `webapp.bat`: `$env:GROCERY_EMAIL_POLLER = "1"; webapp.bat`.

## Verification gate

```powershell
& .\.venv\Scripts\python.exe -m pytest
```

Unit + FastAPI `TestClient` + a Playwright e2e that drives the real buttons. The whole pre-ship gate (pytest, then the static, docs and comment ratchets) is `& .\scripts\verify-before-ship.ps1`. Details: [docs/testing.md](docs/testing.md).

## Typical workflow

1. **Set targets**: in **Items**, tap an item and set its target.
2. **Audit inventory**: walk through zones and update current stock, by hand or by voice.
3. **Shop**: check what to buy, fill the carts, mark items as bought.
4. Use the **already in cart** counters if items were put in the cart before you opened the app.
5. Use **quick-add** for anything not in the system.

## Certificate and review dates

- **Tailscale HTTPS certificate:** expires after 90 days; re-run `& .\.venv\Scripts\python.exe scripts\gen_tailscale_cert.py` (idempotent) and restart the webapp.
- **Self-signed certificate** (`src\gen_ssl_cert.py`): valid 10 years from generation; regenerate it whenever the PC's IP changes.
- **Supermarket benchmark:** the next price review is due 90 days after the latest run (the app shows *Review due* once it has passed).

## Documentation index

| Topic | Doc |
|---|---|
| Launch, icons, legacy Streamlit, Tailscale + Cloudflare access, phone access, HTTPS certificates, the design-review demo instance | [docs/running-and-access.md](docs/running-and-access.md) |
| The PWA: tabs, header, Settings, item sheet | [docs/pwa-overview.md](docs/pwa-overview.md) |
| Audit: manual counts and the voice audit | [docs/audit.md](docs/audit.md) |
| Items: editing, adding, finding a store product | [docs/items.md](docs/items.md) |
| Prices: choosing each item's store with the benchmark | [docs/prices.md](docs/prices.md) |
| Shop: the Carts card, fill history, the list | [docs/shopping.md](docs/shopping.md) |
| Cart automation: logins, cart modes, the cart guard | [docs/browser-automation.md](docs/browser-automation.md), [docs/browser-automation-build.md](docs/browser-automation-build.md), [automation/README.md](automation/README.md) |
| Supermarket benchmark, per-store links, the store API, overrides | [docs/benchmark-and-store-links.md](docs/benchmark-and-store-links.md), [docs/supermarket-benchmark.md](docs/supermarket-benchmark.md) |
| Config (`src/config.json`), usage log, Telegram, Gmail check, spreadsheet columns | [docs/configuration.md](docs/configuration.md) |
| Repository layout | [docs/project-structure.md](docs/project-structure.md) |
| Tests and the gate | [docs/testing.md](docs/testing.md) |
| Troubleshooting | [docs/troubleshooting.md](docs/troubleshooting.md) |
| Architecture diagram | [docs/architecture.mmd](docs/architecture.mmd) |
