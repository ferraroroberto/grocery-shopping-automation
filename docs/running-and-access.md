# Running the app and reaching it

## Launch the app (FastAPI/PWA — recommended)

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

## Regenerate application icons

All external icon surfaces derive from the canonical Lucide `shopping-basket` master in the sibling `project-scaffolding` repository. The generated files are committed and used by the PWA, Windows tray, shortcuts, and Stream Deck:

```powershell
& .\.venv\Scripts\python.exe scripts\gen_icons.py
```

The command writes the PWA files under `app/static/`, `assets/tray/grocery-shopping-automation.ico`, and `assets/stream-deck/grocery-shopping-automation-144.png`. Set `PROJECT_SCAFFOLDING_ROOT` if the sibling repository is not checked out next to this one.

## Design-review demo instance

`scripts/design_review_synthetic.py` boots a throwaway copy of this checkout on **invented data** (a temp workbook and two benchmark runs, made-up products and `demo.invalid` links) so the design review and the redesign can click through every tab, mode and dialog without touching the household's list. It is what fleet-config's `design_review measure grocery-shopping-automation --synthetic` runs (declared in `.fleet.toml` under `[design.review.synthetic]`); to look at it yourself:

```powershell
& .\.venv\Scripts\python.exe scripts\design_review_synthetic.py   # prints ROOT= and URL=; stops (and deletes its temp tree) when stdin closes
```

Nothing real is reachable from it: the copy leaves out the gitignored real data, `config` points at the temp tree and at a closed port, the cart automation, product search, store logins, Chrome launch, "open spreadsheet", the email poll, the voice transcriber, whisper and the LLM hub are all replaced by invented answers (`synthetic_demo_app.py`), and a runtime audit-hook guard (`synthetic_demo_guard.py`) refuses any process launch, non-loopback or real-service connection, open of the real data, or write outside the temp tree, logging it to `<root>/guard.log`. It copies the working tree (tracked and untracked-unignored files), so a redesign in progress is what gets walked. `tests/test_design_review_synthetic.py` boots it and proves all of the above, with a canary file standing in for the real spreadsheet.

## Legacy Streamlit app

The original Streamlit UI is **intentionally retained**, not dead code: it's a fully working fallback over the same `src/data.py` layer, handy for quick desktop access without the PWA's HTTPS-cert / bearer-token setup, and a reference for the per-mode logic. The FastAPI/PWA app on `:8502` is the recommended surface — reach for Streamlit only when you specifically want the old interface.

It's still available on `:8501` and drives the same modes. Double-click `launch_app.bat`, or from the repo root:

```powershell
& .\.venv\Scripts\python.exe -m streamlit run app/app.py
```

## FastAPI remote access: Tailscale + Cloudflare

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

App-level auth matches the sibling pattern. `auth_token` is off by default, which leaves this PC and the LAN open but refuses every API call that arrives through the Cloudflare tunnel, so set a token before using `webapp_tunnel_named.bat`. When set, non-loopback API calls need `Authorization: Bearer <token>` or a `?token=...` bootstrap URL. The page stores the token in `localStorage` and removes it from the visible URL. A password can also be set so a fresh phone can unlock by typing the password, then the server hands back the bearer token.

```powershell
& .\.venv\Scripts\python.exe scripts\gen_token.py
& .\.venv\Scripts\python.exe scripts\set_password.py PW
```

Re-run `scripts\gen_token.py --force` to rotate the token, `scripts\gen_token.py --clear` to disable bearer auth, and `scripts\set_password.py --clear` to remove the password prompt.

## Mobile Access (same Wi-Fi network)
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

## HTTPS setup (required for microphone access on mobile)

Mobile browsers block microphone access on plain HTTP. The app serves over HTTPS using a certificate stored in `certificates/` (gitignored). `webapp.bat` picks the cert up automatically and starts uvicorn with TLS on `:8502`.

There are two certificate options — pick one:

### Option A: Tailscale HTTPS cert (recommended — no cert installation on any device)

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

### Option B: self-signed cert (LAN / localhost access)

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

