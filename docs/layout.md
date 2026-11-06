# Layout

Where new code goes. The structure ratchet (`scripts/structure_ratchet.py`, run by the pre-ship gate) holds this layout: the repo declares `shape = "webapp-tray"` and `extra_roots` in `.fleet.toml`, and a tracked root folder outside both fails the gate by name. [Project structure](project-structure.md) describes what each module does; this page says which folder a new file belongs in.

## Rules the gate holds

- A file stays under 1,000 lines, a Python or JS function under 100, a directory under 40 modules. Existing offenders are in `.structure-ratchet-baseline.json` at their size and may not grow; a new one fails.
- `src/` never imports `app/`: the data layer has no UI dependency.
- A new root folder needs a line here and an entry in `extra_roots`, or the code goes into a folder below.

## Top-level folders

- `app/` — UI layer. `api.py` composes the FastAPI app; `webapp/` is the web layer, where `app/webapp/routers/` holds one `APIRouter` per concern and `app/webapp/static/` is the PWA (`modules/` the ES modules, `_vendored/` the fleet components); `tray/` is the Windows tray and its single-instance primitive. The legacy Streamlit modes (`app.py`, `audit.py`, `shopping.py`, and the other per-mode files) sit beside `api.py`.
- `src/` — UI-free data and business logic. `store_links/` is the per-store links, picks and cost simulator, one module per concern; `benchmark/` is the supermarket price benchmark (basket builder, per-store fetchers, scorer, history and report), whose CLIs run as `python -m src.benchmark.<module>`; `automation/` is the Playwright cart automation per store (`email_parsers/` reads confirmation emails), whose CLIs run as `python -m src.automation.<module>`; `notify/` is the vendored notification primitive; `gmail_readonly/` is the portable read-only Gmail client (OAuth, API client, core), which imports nothing from the project and only relatively within itself, so another repo can copy it; every other module is one concern (`data.py`, `webapp_config.py`, `certs.py`).
- `tests/` — the pytest suite. `e2e/` holds the Playwright stories, one `test_<story>.py` per area, with their shared fixtures and the vendored browser-test helpers; `fixtures/` holds the test data, the sample workbook included; `ratchet_gates/` holds the ratchet gate-wrapper tests, `benchmarks/` the supermarket-benchmark and browser-fetch tests, `store_automation/` the cart-automation, login-check and product-search tests, `email_watch/` the confirmation-email tests; `sign_in/` the password route's lock-out and guessing-alert tests; the remaining `test_*.py` files stay flat, so `tests/` does not grow past its module cap.
- `docs/` — durable reference by topic, indexed in the README.
- `scripts/` — the pre-ship gate, its ratchet wrappers and the vendored ratchets, plus the one-shot tools (token, password, icons, tunnel) and the design-review demo instance.
- `config/` — committed config templates and the alias, product-option and benchmark store-registry tables.
- `assets/` — generated external surfaces: the tray icon and the Stream Deck image.
- `data/` — the sample inventory spreadsheet.
- `webapp/` — the Cloudflare tunnel sample config and the gitignored `certificates/` the launchers resolve; deployment files, not code.

No folder is declared in `extra_roots` today. The gitignored `automation/chrome_user_data/` at the root is the cart automation's shared Chrome profile, where the logged-in store sessions live; it holds no tracked file, so the gate never sees it.

Hidden folders (`.github/`, `.claude/`, `.streamlit/`) are allowed in every shape.
