# Cart-fill run state

Shop → Fill carts starts a child process (`python -m src.automation.run_automation`) and reports on it. The run has a lifecycle, and it is declared in one place: `src/automation/run_state.py` holds the `RunState` type, the exit-code constants and `derive_run_state`, the only function that turns what can be observed into a state. It is registered in `.fleet.toml` as the `cart-fill-run` state machine, following the fleet method in project-scaffolding `docs/implicit-state-machines.md`.

## Why it is declared

The server can observe only a few facts: whether there is a process, whether it is still running, the exit code it left, whether the user asked to stop it, whether it was a dry run, and whether it printed its end-of-run summary. The exit code alone cannot say how a run ended. Stopping a run (`Popen.terminate()` on Windows) leaves `1`, an uncaught crash leaves `1`, and `run_automation.py` itself returns `1` when an item is not in the cart. Read from the exit code, a press of Stop looked like "items are NOT in the cart", a dry run looked like "every item is verified in the cart", and a Chrome profile that was never signed in looked like an unknown code.

Two facts that the exit code cannot carry are recorded separately:

- **`stop_requested`** is written on the run record by `POST /api/automation/stop`, only when the process is still running at that moment, and cleared by every start. The legacy Streamlit mode keeps the same fact in `st.session_state`.
- **The summary line** (`── Cart automation summary ──`, `SUMMARY_HEADER`) is printed by the run only once it has judged the carts. Exit `1` with the summary is "something is missing"; exit `1` without it is a crash.

## States

| State | Meaning | Reason |
|---|---|---|
| `idle` | No run is registered. | none |
| `running` | The process is alive. | none |
| `stopped` | The user stopped the run. A stop wins over any non-zero exit code. | none |
| `succeeded` | Exit `0`: every item verified in the cart. For a dry run: it listed what it would add and changed nothing. | `dry-run` for a dry run |
| `missing` | Exit `1` after the summary: some item is not in the cart. | none |
| `unconfirmed` | Exit `3`: items were added but the store cart could not be read. | none |
| `failed` | The run crashed or could not finish. | `exited-before-summary` (exit `1`, no summary), `profile-not-initialised` (exit `2`), `unexpected-exit` (any other code) |

An observation that cannot happen (a running process with an exit code, a finished process without one, no process with any activity) raises `ValueError` instead of picking a state.

The state is held in memory with the rest of the run, so a tray restart forgets a run in flight; the per-store fill record under `purchase_logs/runs/` stays the source for the Shop glance chips.

## Who reads it

- `GET /api/automation/status` and the `GET /api/automation/events` stream carry `state` and `reason`. `running` and `returncode` stay for older clients and are computed from the same single reading of the process.
- The PWA (`app/webapp/static/modules/automation.js`) words each ending and sets the Fill carts, Stop and Dismiss buttons from `state`; it never maps an exit code to text.
- The legacy Streamlit mode (`app/shopping.py`) renders its finished message from the same derived state.
- Both go through `automation_runner.run_state`, which polls the process once and calls `derive_run_state`.

A new reader calls the derive function (through `automation_runner.run_state`). It does not re-derive a mode from the process handle or the exit code; a fact the state lacks joins the observation and the derive function.

## Tests

`tests/store_automation/test_run_state.py` holds the table (one row per reachable observation, plus the impossible ones that must be refused) and drives each ending through the API with a stand-in child process, so no store is touched. The tests marked `state-machine-proof: cart-fill-run` are the illegal-transition proof the state scoreboard counts.
