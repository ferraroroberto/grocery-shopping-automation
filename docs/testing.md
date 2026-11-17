# Tests and the pre-ship gate

- **`tests/`** — `pytest` suite (`test_*.py`: unit + FastAPI `TestClient` + a Playwright e2e that drives the real buttons) plus standalone smoke scripts (`smoke_*.py`, `automation_smoke_*.py`) that drive real stores and stay out of the gate. Run `& .\.venv\Scripts\python.exe -m pytest` for the suite alone.
- **The e2e** stubs the LLM hub by default; set `GROCERY_E2E_LIVE=1` (with the hub running) to exercise the real model. It also feeds the design inventory: each test calls the vendored `surface(page, "<tab>.<surface>")` (`tests/e2e/_surface.py`) once its surface is open, a no-op unless fleet-config's `/design-review measure grocery-shopping-automation --e2e` sets `DESIGN_SURFACE_CAPTURE`; a new surface takes one call in the test that opens it.

## Loopback sockets in the e2e

Every connection a browser closes to a local server leaves an (ephemeral port, server port) pair in TIME_WAIT for minutes, and Windows fails a `connect()` that lands on one as `net::ERR_ADDRESS_IN_USE`. A run that makes many connections, or a second lane gating on the same host, then fails a page load that has nothing wrong with it. The harness keeps its share small in three ways:

- **One context per distinct arguments, reset after each test** (`tests/e2e/_shared_context.py`). The `browser` fixture hands out contexts through `new_page` / `new_context`; a context outlives its test and keeps its sockets and HTTP cache. The viewport, colour scheme and extra headers are applied to the page, so pages that differ only in those share one context. After each test the reset closes its pages, drops routes, cookies and permission grants, and empties the app origin's local and session storage through a routed scratch page (no socket to the app). A page opened after another in the same test starts from empty storage, as in a fresh context. A reset that raises discards the context. A test that needs state the reset cannot undo sets it on the page (`page.add_init_script`), never on the context, and never closes a context it leased; `tests/e2e_harness/test_shared_context.py` fails a file that does.
- **A longer keep-alive on the disposable server** (`HARNESS_KEEP_ALIVE_S` in `tests/e2e/conftest.py`). uvicorn closes an idle connection after 5 s, shorter than the pause between a test's page loads, so the browser reopened one per burst.
- **A named failure** (`tests/e2e/_port_pool.py`). A failure whose text carries `ERR_ADDRESS_IN_USE` or Playwright's `connect ENOBUFS` leads with `PORT POOL EXHAUSTED`, and the run ends with a section of that name listing the tests, so a gate reader sees a host-capacity problem to wait out and re-run. A drained pool is not a page defect.

A full e2e run ends with the line `shared contexts: N created for M requests`. Measured the same way before and after (new loopback TIME_WAIT pairs whose server end is a port the run's servers listened on, `netstat -ano` sampled every 250 ms, other local apps' ports excluded), one full run went from 145 and 153 pairs to 83 and 83. The port range and `TcpTimedWaitDelay` are host-level and never changed to make a suite pass. The WebKit phone checks launch their own engine per test and stay outside the pool.

## The gate

`& .\scripts\verify-before-ship.ps1` is the one pass/fail pipeline, fail-fast, anchored to the repo root so it runs from anywhere. This page is the only full description of its stages; the README, `CLAUDE.md` and the script header each carry a one-line pointer here. The stages run in this order:

1. **Runtime write probe (snapshot)** — `scripts/runtime_write_ratchet.py --snapshot` records the untracked and ignored files already under the code folders.
2. **pytest** — the unit + e2e suite. It never reads live state (Gmail config, processed state, the real spreadsheet), a real cart or a real browser profile.
3. **Runtime write probe** — `--probe` against the snapshot: any untracked or ignored file the test run left under a code folder fails.
4. **Static ratchet** — the stage fails on a copied block (`jscpd`, run through `npx`, so Node must be installed), on an unused import or variable, on an undefined name (`F821`), and on a likely-bug or security pattern from the curated ruff `B` and `S` families, unless `.static-ratchet-baseline.json` lists it. `ruff` comes from `requirements.txt`.
5. **Docs ratchet** — `README.md` past 40 KiB, or a new issue reference, dated heading or changelog heading in `README.md` or `docs/**/*.md` beyond `.docs-ratchet-baseline.json`, fails; docs state what is true now and history stays in git, PRs and issues.
6. **Comment ratchet** — a file's count of `#NNN` issue references (and decision-history phrases) inside comments and docstrings rising above `.comment-ratchet-baseline.json` fails.
7. **Structure ratchet** — a file over 1,000 lines, a function over 100, a directory over 40 modules, a `src/` module importing `app/`, or a root folder outside the shape and `extra_roots` in `.fleet.toml` fails beyond `.structure-ratchet-baseline.json`; new code goes where [`docs/layout.md`](layout.md) says.
8. **Instruction ratchet** — a prompt-audit rule count in `CLAUDE.md`, `AGENTS.md` or a `SKILL.md` rising above `.instruction-ratchet-baseline.json`, or one of them passing its size cap, fails.
9. **Doc-refs ratchet** — a repo path, `python -m` module, relative link or loopback port named in `README.md`, `CLAUDE.md` or `docs/**/*.md` that does not exist fails beyond `.doc-refs-ratchet-baseline.json`; the ports of the other services the docs name on purpose are `allow_refs` in `.fleet.toml`.
10. **Dead-code ratchet** — a Python function, class or method that nothing in the tracked tree references fails beyond `.dead-code-ratchet-baseline.json`; route handlers, tests, dunders and decorated hooks are entry points.
11. **Conventions ratchet** — a file's hit count for an enabled `ast` rule rising above `.conventions-ratchet-baseline.json` fails. The enabled rules and the `must_use` helper routing (pooled HTTP, atomic JSON writes) are the `rules` list and `[[conventions_ratchet.must_use]]` tables in `.fleet.toml`, the single source; a deliberate hit carries a reasoned `# <rule>-exempt: why` comment.
12. **Runtime write ratchet** — a Python write target resolving under a code folder fails beyond `.runtime-write-ratchet-baseline.json`; a target it cannot resolve is NOT CONFIRMED.
13. **Usage track ratchet** — a file's count of untracked controls, untracked sheets or run-time-built tracking ids rising above `.usage-track-ratchet-baseline.json` fails, and so does an app that does not vendor, start and mount its usage log (never baselined).

Every ratchet keeps its existing debt in a baseline that can only shrink: `--update-baseline` in the change that removes debt, `--allow-additions` (visible in review) to add. A detector, scan or tree that cannot be run, read, parsed or measured exits 3 and fails the gate as NOT CONFIRMED, never a pass or a skip. Only the ratchets with their own stage above are in the gate; none is path-routed, because each needs the whole tree.

## The verdict line

The last stdout line of a gate run is `GATE-RESULT: PASS` or `GATE-RESULT: FAIL`, on every path that can still run code (a pass, a failed stage, a missing `.venv`, a box load lock not taken in time, an unhandled terminating error); exit codes stay 0 and 1. Do not pipe the gate when its exit code matters (`... | tail -40` reports `tail`'s status); a caller that cannot avoid a pipe asserts on that line, and a run without it is not a pass. `tests/ratchet_gates/test_gate_result.py` covers it.
