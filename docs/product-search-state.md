# Product search state

The product search in the Add item sheet (typed or spoken) starts a child process that looks a term up in every store and streams what it finds. Two lifecycles ride on it, and both are declared in one place: `src/automation/search_state.py` holds the `SearchState` and `StoreState` types, the failure reasons, and the derive functions that are the only code mapping observed facts to a state. It is registered in `.fleet.toml` as the `product-search-run` state machine, following the fleet method in project-scaffolding `docs/implicit-state-machines.md`.

## Why it is declared

The child process, the status route and the PWA each spelled the same state words (`waiting`, `searching`, `done`, `failed`, `running`, `idle`). A store still in flight when a run crashed or was cancelled had to be settled to "failed, stopped" by the route, and the PWA re-derived "in flight" and "failed" from the strings on its own. Two readers could drift; now none of them names a state.

## Run states

| State | Meaning | Error |
|---|---|---|
| `idle` | No run is registered yet (the start call is still parsing the utterance). | none |
| `running` | The process is alive. A result line printed a moment before the process exits does not end the run early. | none |
| `done` | The process ended and printed a result. | none |
| `error` | The process ended with no result line, or the result reported an error (for example a store is not logged in). | the message the UI shows |

An observation that cannot happen (no run with a live process or a result, a result error without a result line) raises `ValueError`.

## Store states

| State | Meaning | Reason |
|---|---|---|
| `waiting` | The shared Chrome profile is busy or the browser is not up yet. | none |
| `searching` | The store's search is in flight. | none |
| `done` | The store answered; an empty list is a genuine no-match. | none |
| `failed` | The store could not be searched. | `session` (login expired), `timeout`, `error`, or `stopped` |

A store still `waiting` or `searching` once the run has ended is derived as `failed` with reason `stopped`, so it never reads as still searching. A state string outside the set raises `ValueError`.

## Who reads it

- The child process (`src/automation/product_search.py`) emits each store event with a `StoreState` and a failure reason from the module.
- `GET /api/product-search/status` (and the start and cancel responses) carries `state`, plus `idle`, `running` and `failed` for the run, and `state`, `in_flight`, `waiting` and `failed` for each store row, all from the same reading.
- The PWA (`app/webapp/static/modules/search.js`) branches on those booleans and never compares a state string.

A new reader calls the derive functions through the status route. It does not re-derive a state from the process handle or the stream.

## Tests

`tests/store_automation/test_search_state.py` holds the derive tables, the impossible observations that must be refused and the status route's derived flags. The tests marked `state-machine-proof: product-search-run` are the illegal-transition proof the state scoreboard counts.
