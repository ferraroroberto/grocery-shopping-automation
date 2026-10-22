# Supermarket benchmark and per-store links

The `benchmark/` package answers *"is our Mercadona + Ametller split still the
cheapest way to buy this basket without dropping quality?"* It prices the
household's **average monthly basket** (from `purchase_logs/`) at every online
supermarket in `benchmark/stores.json`, with quality locked per item (brand,
meat %, ingredients) and every product compared per kg, litre or piece, and scores status
quo vs single-store vs the best 1–5-store splits including delivery fees. Each
run is recorded in a history (`benchmark_runs/_state/`), so the report shows
how prices and the recommendation move over time.

Re-run it with the project skill **`/supermarket-benchmark`** (Claude Code) —
it rebuilds the basket, fans out one research agent per store, scores and
publishes a recommendation. The building blocks run standalone too:

```powershell
& .\.venv\Scripts\python.exe -m benchmark.build_basket                 # benchmark_runs/<today>/basket.json
& .\.venv\Scripts\python.exe -m benchmark.results status --run benchmark_runs/<date> --store dia
& .\.venv\Scripts\python.exe -m benchmark.score benchmark_runs/<date>   # scenarios.json + report.md
& .\.venv\Scripts\python.exe -m benchmark.score benchmark_runs/<date> --promote   # finalise: mappings + history
& .\.venv\Scripts\python.exe -m benchmark.report benchmark_runs/<date>  # report.html (history included)
& .\.venv\Scripts\python.exe -m benchmark.history show                 # one line per past run
```

`benchmark_runs/` is gitignored (prices, basket and product mappings stay
local). Methodology, quality tiers and schemas:
[`docs/supermarket-benchmark.md`](supermarket-benchmark.md).

## Per-store links, store picks and the cost simulator

Each inventory row can hold one product URL per supermarket in optional
`url_<store>` columns of `list.xlsx` (store keys from `benchmark/stores.json`;
a sheet without them loads unchanged). `super` + `buscador` stay the chosen
store and its URL, so the cart automation keeps working. `src/store_links/`
does the work; the API (`app/routers/stores.py`) exposes it:

| Route | Does |
|---|---|
| `POST /api/stores/import-latest` | Seeds `url_<store>` from `benchmark_runs/_state/mappings/` (and each row's own `buscador`), matched by `item_key(comida)`. Never overwrites a non-empty cell; returns `seeded` / `skipped_existing` / `unmatched`. Re-run after every benchmark. |
| `PUT /api/items/{id}/store-url` | Sets or clears one store's URL for one item. |
| `POST /api/items/{id}/pick` | Makes a store the item's chosen one (`super` + `buscador` together). |
| `GET /api/stores` | Store registry, which stores have a cart handler, frequency presets, and the benchmark status: latest `run_date`, `age_days`, `next_due` (run date + 90 days), `stores_covered` and the `overrides` count. |
| `GET /api/stores/recommended` | The latest run's recommended plan as item → store picks. |
| `POST /api/stores/simulate` | Monthly goods + delivery (normal and fee-optimised) per store and overall for a set of picks vs today's stores, at a chosen frequency (weekly default). Unpriced picks are listed and excluded, never counted as 0 €. |
| `POST /api/stores/apply-preview` | Per changed item: from/to store, old/new pack and product name (`old_name` / `new_name`), the converted target `cantidad`, and the stock `tenemos` converted by the same pack factor (rounded to nearest, next to `tenemos_from`). |
| `POST /api/stores/apply` | Writes `super`, `buscador` and `cantidad` — and `tenemos` when a change carries it — for the given changes in one save. |
| `GET /api/items/{id}/store-detail` | Everything the app believes about one item: the list row, the basket product it was benchmarked against (store, pack, price, run-time target), monthly use, spec/tier, one entry per store (product, pack, price, €/unit, monthly €, status, confidence, quality, evidence, link and its kind, and whether the values are the benchmark's, your override or link-only), the before → now quantity maths in real units, review flags and the checked date. |
| `PUT` / `DELETE /api/items/{id}/store-override` | Save your own `name` / `pack_size` / `unit` (kg, l, ud, m) / `pack_price` / `note` for one basket item at one `store`, or drop it (`?store=`) to go back to the benchmark. Returns the store detail. |
| `PUT /api/items/{id}/checked` | Marks an item reviewed (`{"checked": true}`) or not. |
| `GET /api/stores/checks` | Items that need checking and why: `moved`, `pack_x2`, `unit_mismatch`, `search_link` / `suspect_link`, `override`, `benchmark_changed`, `stock_unconverted`; per-flag counts; checked dates. |

Item ids are row positions, re-numbered on every load, so a delete on one
device shifts the ids another device still holds. Every `/api/items/{id}`
write (these routes and the inventory ones: `current-delta`,
`current`, `PUT`, `DELETE`) takes the item's name as `?name=`; when the row at
that id is no longer that item (compared by `item_key`), the server answers
`409` with an `X-Inventory-Stale: 1` header instead of writing, and the PWA
says so and reloads the list. Without `?name=` the write goes through
unchecked (scripts, tests).

The two batch writes that carry ids in the body get the same check: `POST
/api/audio/apply` takes `names` (id → the name the client showed) beside
`updates`, and each `POST /api/stores/apply` change takes an optional `name`.
If any named row has moved, the whole batch is refused with the same `409` and
nothing is written (no inventory change, no audit log). Ids or changes sent
without a name are not checked.

Prices come from the newest *scored* run (one with `scenarios.json`); the fee
maths is `benchmark.score`'s own. The PWA's **Items → Prices** view
([Prices](prices.md)) is the front end for all of it.

**Your corrections (overrides).** When your own research disagrees with the
benchmark — a different pack, a new price, another product — save it as an
override instead of editing the benchmark. Overrides and the review's
*checked* marks live in `benchmark_runs/_state/overrides.json` (gitignored,
like the rest of `benchmark_runs/`). An override replaces only the fields you
give; the rest stay the benchmark's. The simulator, the per-item prices and
the pack conversion on apply all use it. The benchmark's values at the time
are kept with it, so when a later run changes them the item is flagged
`benchmark_changed`. A corrupt file is ignored (with a warning) and moved
aside to `overrides.corrupt-<time>.json` on the next write. `stock_unconverted`
is a best guess, since nothing records whether stock was recounted: the item
moved store, the packs differ, you have stock, and you haven't checked it yet.

