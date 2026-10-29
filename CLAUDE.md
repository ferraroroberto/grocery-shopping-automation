# Project Instructions

## Streamlit conventions

- `st.set_page_config(layout="wide", page_title="...")` MUST be the first Streamlit call.
- Use `width="stretch"` (and `width="content"` where appropriate) in new and modified code. **Never** introduce new `use_container_width=True` — deprecated; migrate existing uses when you touch that code.
- All mutable state in `st.session_state`. No module-level globals.
- `@st.cache_data` for DataFrames/files; `@st.cache_resource` for DB clients/models.
- Every widget needs a stable, explicit `key=`.
- UI code only in the UI directory (e.g. `app/`); data logic stays in the non-UI package (e.g. `src/`). Never import `streamlit` from non-UI code.
- User feedback via `st.error()` / `st.warning()` / `st.success()`, not `st.write()`.
- **App layout:** main file (e.g. `app.py`) handles only page config, shared state, sidebar, and tab/radio routing. Each tab/mode lives in its own file exposing a `main(...)` (or `render_*`) function. Default to `st.tabs()`; use a sidebar radio only when asked.

## UX surface
*The design-conformance gate the `/issue-{start,finish,yolo}` skills read (convention: `project-scaffolding#83`). Live, parseable block — the product is the FastAPI + static PWA under `app/static/`.*

- design spec applies: yes        # this repo serves a real PWA on :8502; the legacy Streamlit app on :8501 is exempt
- paths:
  - app/static/**/*.css
  - app/static/**/*.{js,html}
- key views:                      # single tabbed SPA served at `/`
  - /          (Home · Shop · Audit · Items tabs, bottom-pill nav, Settings behind the header gear; Home is its This week card (audit, list, carts and prices rows) above a search over every item; Items is Items · Prices, Prices leading with its Monthly cost glance card and the Benchmark sheet, product search in the Add item sheet, Shop leading with its Carts glance card (Fill carts, Mark purchase done, the Options and Fill history sheets) above folded store groups and their store sheet, Audit opening on Voice (its glance with Record, the Match results glance with Apply and Needs you, the transcript menu sheet) beside Manual, Settings as inset groups (Display: text size; Stores: the Store logins and Email watch sheets; Data))

## This repository
Household grocery inventory + shopping list web app, backed by an Excel file. Primary surface: FastAPI + vanilla-JS PWA on `:8502` (`app/api.py`); legacy Streamlit app on `:8501` (`app/app.py`) drives the same modes. Voice-narrated audit mode transcribes and matches through the `local-llm-hub` sibling's LLM hub. Windows + PowerShell. See `README.md`.

**Restart recipe:** the FastAPI/PWA webapp is owned by the **tray** (`tray.bat` → `launcher.py tray`, `:8502`, HTTPS when `certificates/cert.pem` exists). No hot-reload across `app/`/`src/` edits: after changing either, run **`tray.bat --restart`** (kills the old tray subtree, reclaims `:8502` scoped to this repo's `.venv` by CommandLine, starts a fresh tray). Confirm the new build is live via `GET /api/version`'s `git_sha` == `git rev-parse --short HEAD` — `/healthz` 200 alone is not enough (a stale process passes). The port (default `:8502`) follows `config/webapp_config.json` — `tray.bat` reads it for the reclaim list too; `.fleet.toml`'s `port` documents the default. `webapp.bat` is the manual/no-tray alternative (shares cert-resolution and uvicorn-invocation logic with `app/tray/manager.py`). The legacy Streamlit app on `:8501` is unaffected: separate manual launch via `launch_app.bat`, not tray-managed.

## Internal architecture

[`docs/architecture.mmd`](docs/architecture.mmd) is a hand-authored Mermaid diagram of this repo's internal structure (FastAPI/PWA and legacy Streamlit front ends, UI-free `src/` data layer, `automation/` Playwright cart automation, external `local-llm-hub`/`voice-transcriber` dependencies). Update it in the same PR as any material structural change (new mode/route, moved module, new external dependency) — same anti-staleness contract as `.fleet.toml`'s `description`. Not auto-generated, not covered by pytest.

## CI expectations
- Workflow `.github/workflows/ci.yml`, job `gate`, on every PR and every push to `main`. **Advisory, not required** (no branch protection) — the local gate is the contract: `& .\scripts\verify-before-ship.ps1` = `pytest` (unit + e2e) then the **static, docs, comment, structure, instruction, doc-refs, dead-code and conventions ratchets**. Each ratchet is a `scripts/<name>_ratchet.py` vendored byte-for-byte from project-scaffolding and never edited here, with a `.fleet.toml` `[<name>_ratchet]` table and a `.<name>-ratchet-baseline.json`; it reads the whole tree and is never path-routed. The gate calls it only through `python -m scripts.<name>_ratchet_gate`, which fails exit 3 (a detector could not run, the tree or docs could not be scanned, measured, parsed or linted) as NOT CONFIRMED, never a pass or a skip. Shrink a baseline with `--update-baseline` in the PR that removes the debt; adding to one needs `--allow-additions` and shows in review. Per ratchet:
  - **static** — fails on a duplicate block (jscpd via `npx`, so **Node is a prerequisite**), an unused import/variable (ruff `F401,F841`) or a likely-bug or security pattern (the curated ruff `B` and `S` families, with `S113` also flagging `timeout=None`; ruff runs in the `.venv`) that is not in the baseline.
  - **docs** — fails when `README.md` passes 40 KiB or when `README.md` / `docs/**/*.md` gains an issue reference, dated heading or changelog heading above its baseline; state the present-tense why in docs.
  - **comment** — fails when a file's count of `#NNN` issue references inside comments rises above its baseline; comments state the present-tense why, decision history stays in git, PRs and issues.
  - **structure** — top-level `shape = "webapp-tray"` and `extra_roots` plus `[structure_ratchet]`; fails when a file passes 1,000 lines, a Python/JS function 100 lines or a directory 40 modules above its baseline, when a `src/` module imports `app/`, or when a tracked root folder is outside the shape and `extra_roots`. New code goes where [`docs/layout.md`](docs/layout.md) puts it, so split rather than grow a baselined file; a new root folder needs a `docs/layout.md` line plus an `extra_roots` entry.
  - **instruction** — runs the weekly prompt audit's deterministic rules over `CLAUDE.md`, `AGENTS.md` and the `SKILL.md` files and fails when a rule count rises above its baseline or a file passes its size cap; reword a new hit rather than baselining it; the baseline is empty today.
  - **doc-refs** — fails when `README.md`, `CLAUDE.md` or `docs/**/*.md` names a repo path, `python -m` module, relative link or loopback port that does not exist; point a stale reference at what exists, or list a deliberate pointer (another service's port) in `allow_refs`; the baseline is empty today.
  - **dead-code** — fails when a Python function, class or method that nothing in the tracked tree references is not in the baseline; delete the definition or call it; a real framework hook the rules miss goes in `allow` or `entry_decorators` in `[dead_code_ratchet]`; the baseline is empty today.
  - **conventions** — three fleet conventions as `ast` rules; fails when a file's hit count for a rule rises above its baseline: `no-window` (a `subprocess` spawn with no `creationflags=`), `logging-not-print` (a `print` in `app/` or `src/` library code) and `no-drive-path` (a string literal starting with a drive letter); `secret-compare` and `git-wrapper` stay off because the repo has no helper to route through. Fix a new hit, or mark a deliberate one with a reasoned `# <rule>-exempt: why` comment.
- Typical green: **~4 min**. Investigate at **>8 min**; treat as wedged at **>16 min** (`timeout-minutes: 16`).
- Flaky leg: the e2e step's WebKit iPhone 13 check (`test_four_tab_nav_and_relocated_sections`) can wedge on the hosted runner. A wedge is a flake, not the diff.
- CI's only signal beyond the local gate is a clean machine (fresh `.venv` from `requirements.txt`, only the committed `config/*.sample.*` seeded) plus the e2e suite on the hosted runner. Its e2e surface = `app/static/`, `app/api.py`, `app/routers/`, `app/middleware.py`, `src/`, `tests/e2e/`, `tests/conftest.py`. A diff touching **none** of these gains nothing from CI.
- Tests never read live state (Gmail config, processed state, the real spreadsheet); the `smoke_*.py` / `automation_smoke_*.py` scripts drive real stores and stay out of CI.
