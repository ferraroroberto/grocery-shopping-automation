# Layout

Where new code goes. The structure ratchet (`scripts/structure_ratchet.py`, run by the pre-ship gate) holds this layout: the repo declares `shape = "webapp-tray"` and `extra_roots` in `.fleet.toml`, and a tracked root folder outside both fails the gate by name. [Project structure](project-structure.md) describes what each module does; this page says which folder a new file belongs in.

## Rules the gate holds

- A file stays under 1,000 lines, a Python or JS function under 100, a directory under 40 modules. Existing offenders are in `.structure-ratchet-baseline.json` at their size and may not grow; a new one fails.
- `src/` never imports `app/`: the data layer has no UI dependency.
- A new root folder needs a line here and an entry in `extra_roots`, or the code goes into a folder below.

## Top-level folders

- `app/` — UI layer. `api.py` composes the FastAPI app; `routers/` holds one `APIRouter` per concern; `tray/` is the Windows tray and its single-instance primitive; `static/` is the PWA (`modules/` the ES modules, `_vendored/` the fleet components). The legacy Streamlit modes (`app.py`, `audit.py`, `shopping.py`, and the other per-mode files) sit beside `api.py`.
- `src/` — UI-free data and business logic. `store_links/` is the per-store links, picks and cost simulator, one module per concern; `notify/` is the vendored notification primitive; every other module is one concern (`data.py`, `webapp_config.py`, `certs.py`).
- `tests/` — the pytest suite. `e2e/` holds the vendored browser-test helpers and `fixtures/` the test data; the `test_*.py` files stay flat.
- `docs/` — durable reference by topic, indexed in the README.
- `scripts/` — the pre-ship gate, its ratchet wrappers and the vendored ratchets, plus the one-shot tools (token, password, icons, tunnel) and the design-review demo instance.
- `config/` — committed config templates and the alias and product-option tables.
- `assets/` — generated external surfaces: the tray icon and the Stream Deck image.
- `data/` — the sample inventory spreadsheet.

## Declared exceptions

Each is in `extra_roots`; it is a folder the `webapp-tray` preset does not list, kept on purpose.

- `automation/` — the Playwright cart automation per store (`email_parsers/` reads confirmation emails). A browser-driving pipeline that runs as its own CLI, not a UI layer and not a data module.
- `benchmark/` — the supermarket price benchmark: basket builder, per-store fetchers, scorer, history and report. It is also imported by `src/store_links/`, so it is a sibling package rather than a `src/` subpackage.
- `gmail_readonly/` — a portable read-only Gmail client package (OAuth, API client, core); it has no project imports, so it stays a root package that another repo can copy.
- `webapp/` — the Cloudflare tunnel sample config and the gitignored `certificates/` the launchers resolve; deployment files, not code.

Hidden folders (`.github/`, `.claude/`, `.streamlit/`) are allowed in every shape.
