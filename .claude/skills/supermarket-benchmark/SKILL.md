---
name: supermarket-benchmark
description: Re-run the quality-locked supermarket price benchmark (issue #145) — rebuild the monthly basket from list.xlsx + purchase_logs, fan out one Sonnet research agent per store, score split scenarios deterministically, review, and publish a recommendation. E.g. "/supermarket-benchmark", "/supermarket-benchmark carrefour dia" (only those stores), "re-run the supermarket benchmark".
---

# supermarket-benchmark

Answers: *is our Mercadona + Ametller split still the cheapest way to buy this household's groceries **without dropping quality**, or should we consolidate / re-split?* Methodology and schemas: `docs/supermarket-benchmark.md`. Everything under `benchmark_runs/` is gitignored (the repo is public).

**Roles.** The orchestrator (this session, Opus) owns the basket review, the spec decisions, blocked-store recovery, the scoring review and the recommendation. Store research is fanned out to **Sonnet** `general-purpose` sub-agents, one per store, all in parallel (Sonnet is exempt from the fleet's 3-concurrent-Opus cap). Never run research agents on Opus.

## Arguments

Optional store keys (from `benchmark/stores.json`) to limit the fan-out; default = every store in the registry.

## Steps

### 1. Build the basket

```
& .\.venv\Scripts\python.exe -m benchmark.build_basket
```

Writes `benchmark_runs/<today>/basket.json`. Plain HTTP only (Mercadona public API, Ametller via the `benchmark.ametller_guest` guest token) — no Chrome. Check the log for `no current product/price resolved` lines: each is a stale inventory URL or an out-of-season item. Fix via `benchmark_runs/_state/quality_specs.json` (`current_url` to price a replacement, `exclude` + `reason` to drop), then rebuild.

### 2. Review tiers and specs (orchestrator)

Print every item's tier, current product and ingredients. For any **tier B** item or risky tier C item with `spec_source: auto`, read the ingredient list and write a real spec into `quality_specs.json` (see the existing entries for the pattern: a MATCH bar = no worse than current, and for deli an UPGRADE bar = the household's stated standard, ≥98% meat, clean label). Fix wrong auto-tiers (e.g. a Hacendado sub-brand misread as a brand → C). Rebuild after editing.

**Show the user the tier-B spec table and get confirmation before step 3** — a wrong spec poisons every store.

### 3. Fan out research (Sonnet, parallel)

For each store, spawn one `general-purpose` agent with `model: "sonnet"` in a **single message** (so they run concurrently), using the prompt template below with `{store}`, `{run_dir}` filled in. Mercadona and Ametller are baselines: their agents research only the items **not** currently bought there (the prompt handles this).

If an agent returns early, check `python -m benchmark.results status --run {run_dir} --store {store}` and resume it (SendMessage) or spawn a fresh one — it continues from the missing keys.

### 4. Recover blocked stores (orchestrator)

A store whose `access.blocked` is true or with many `unknown` items gets handled by the orchestrator **sequentially** — `benchmark.browser_fetch` first, then a Claude-in-Chrome tab if needed.

### 5. Score and review

```
& .\.venv\Scripts\python.exe -m benchmark.score benchmark_runs/<date>
```

Writes `scenarios.json` + `report.md`: status quo, re-split of today's stores, the best plan for every store count up to 5 (ranked fee-optimised), the **maximum-savings** plan and the **recommended** plan (a store joins only if it saves ≥ €10/month). Every candidate (a record and its `alternatives`) is compared per kg / litre / piece and the cheapest counts; food matches at low confidence ("unverified") never count. Then audit before trusting it:
- **outliers**: any item whose store €/unit is < 0.5× or > 2× the baseline €/unit → open the URL, check pack size, per-piece vs per-kg pricing and unit conversion;
- **count packs** (`ud` items: bags, rolls, cloths, eggs) → pack_size must be the piece count;
- **never overwrite a qualifying match with a pricier one** — add it with `benchmark.results alt` instead, so no priced candidate is lost;
- every tier-B match that lands in the recommended plan → re-read the evidence (ingredients) and confirm it clears the spec;
- stores with `delivers: unknown` → resolve or exclude.
Fix records via `benchmark.results item ...` (keep a per-run `benchmark_runs/<date>/audit_fixes.py` of every correction — idempotent, and skipping records a later follow-up already replaced — so the audit is reviewable and safe to re-run) and re-score.

### 6. Write the findings, render, record, publish

1. Write `benchmark_runs/<date>/report_notes.json` — the judgment the code can't derive: `{"findings": [{"title", "text"}], "caveats": [{"title", "text"}]}` (e.g. a spec surprise, produce taste not measured, format changes such as fresh dough for a pre-baked base, stores that need a new cart handler). Optional `"headline"` overrides the generated one.
2. Finalise the run: `python -m benchmark.score benchmark_runs/<date> --promote` — promotes verified matches to `_state/mappings/` (next run re-verifies instead of re-searching) **and records the run** in `_state/history.jsonl` (one summary line per run) and `_state/prices.csv` (every store × item offer, long format — price trends across runs).
3. Render: `python -m benchmark.report benchmark_runs/<date>` → `<date>/report.html` (fleet design tokens, light/dark, phone-safe; includes the plan store by store, what moves, quality evidence, store scorecard and an **Over time** section fed by the history).
4. Publish it as the **one living report**: read `benchmark_runs/_state/report.json` → if it holds an `artifact_url`, first `Artifact action:"read"` that URL, then publish `report.html` with `url` set to it (same link, new version); otherwise publish fresh. After publishing, copy `report.html` to `_state/report_published.html` and write `{"artifact_url", "published_run", "published_version", "published_file"}` to `_state/report.json`, so the live page survives locally (gitignored, never committed) even if the Artifact is lost; each run's own `report.html` stays in its run folder. Check it once at 390 px width before publishing (`document.documentElement.scrollWidth` must equal the viewport width).
5. Record a dated decision bullet on issue #145 (or its successor) with the headline numbers.

## Research agent prompt template

````
You are pricing a household's monthly grocery basket at ONE online supermarket: **{store}** ({store_name}, {shop_url}), for home delivery to postal code {postal_code}. Work from the repo root E:\automation\grocery-shopping-automation. Use `./.venv/Scripts/python.exe` for Python.

## Inputs
- `{run_dir}/basket.json` — items[]: key, comida (Spanish item name), store (where bought today), tier, spec, current {name, brand, ean, pack_size, unit, pack_price, unit_price, ingredients, url}, monthly_packs.
- `benchmark/stores.json` → stores.{store}: hints verified earlier (start there).
- `benchmark_runs/_state/mappings/{store}.json` — if it exists, last run's verified matches, each with any `alternatives` (other qualifying sizes): RE-VERIFY those (price, availability, still meets spec) before searching fresh, and write each surviving alternative back with `alt`.

## Your job
For EVERY basket item whose `store` is not "{store}", find the best qualifying product at {store} and record it. (Items already bought at {store} are the baseline — skip them.) Also record the store's delivery conditions for {postal_code}.

## Quality rules (hard — never trade quality for price)
- Tier A (brand-locked): same brand AND same variant (flavour, fat %, size class). Match EAN when the site exposes it. Different brand = not_found (you may list it under `rejected`).
- Tier B (deli/meat/fish/eggs): read the spec. Must meet the MATCH bar: meat/fish % ≥ spec, additive list no worse, same cut/format, same eco/free-range class, fresh vs frozen as specified. Quote the product's ingredient list verbatim in `evidence` — no ingredients visible = confidence low. If the spec has an UPGRADE bar, ALSO look for the cheapest product meeting it and record it under `upgrade`.
- Tier C (key specs): the defining specs in the spec/current ingredients must hold (100% peanut, 99% cacao, natural not oil, same fruit variety when named, category I or better). Brand free.
- Tier D (commodity): cheapest product with the same function and comparable size/format.
- Pick the CHEAPEST product (by €/unit) that clears the bar — not the first hit, not the premium one.
- A near miss is `not_found` with the candidate in `rejected` + reason. Never mark `equivalent` something you would not happily eat in its place.

## Compare per kg, litre or piece — any pack size
The household buys in volume, so every product is compared on €/kg, €/l or €/piece whatever its pack size: a 1 kg bag at a lower €/kg is a real saving against today's 300 g. Record the cheapest-per-unit qualifying product as the item, and if you also saw another qualifying size (e.g. a normal pack next to a bulk one), add it with `benchmark.results alt` — the scorer uses whichever is cheapest per unit, and the report can show both. Never let a bigger pack lower the quality bar. For fresh fish and meat a bulk piece (a whole or half fish cut at the counter) is fine when it is the same fresh product.

## Units (the #1 source of scoring errors)
Write `pack_size` in the basket item's `current.unit` (kg, l, ud, m): 200 g → 0.2 (kg); 330 ml → 0.33 (l); a 12-egg box → 12 (ud); fruit sold per kg → pack_size 1, pack_price = €/kg. `pack_price` = what one pack costs today. If on promo, set promo true and `regular_price`. The writer rejects a unit mismatch — convert, don't fight it.

## How to access the site
1. Plain HTTP first (curl / python requests with a desktop Chrome User-Agent, JSON APIs the storefront itself calls, WebFetch). Look at stores.json hints.
2. If blocked (403/challenge), use real Chrome on this store's own profile: `./.venv/Scripts/python.exe -m benchmark.browser_fetch --store {store} --url "<url>" [--mode text|html|api] [--settle 6]`. It opens a visible Chrome window each call — batch work (e.g. crawl a category page listing many products) rather than one call per tiny lookup.
3. Do NOT use the Claude-in-Chrome browser tools or the shared profile `automation/chrome_user_data` (other agents and the user's cart automation use them).
4. If the store is genuinely unreachable, record `access` with `"blocked": true` and the verbatim error, mark the remaining items `unknown`, and stop. Don't guess prices.
5. Set the delivery postal code where the site needs it (prices/assortment can differ by warehouse).

## Writing results (only through the validator — incremental, one item at a time)
- `./.venv/Scripts/python.exe -m benchmark.results status --run {run_dir} --store {store}` — shows what's missing (use it to resume).
- `./.venv/Scripts/python.exe -m benchmark.results item --run {run_dir} --store {store} --key <key> --json-file <tmp.json>`
- `./.venv/Scripts/python.exe -m benchmark.results delivery --run {run_dir} --store {store} --json '<...>'`
- `./.venv/Scripts/python.exe -m benchmark.results access --run {run_dir} --store {store} --json '{"method": "http|chrome", "blocked": false, "notes": "which endpoints worked"}'`
Record schema: see the docstring of benchmark/results.py (read it first). Write each item as soon as it's decided. Write temp JSON files ONLY under `<your scratchpad>/{store}/` — never the scratchpad root or the repo (other store agents share the scratchpad and will overwrite root-level files).

## Delivery conditions
Record for {postal_code}: delivers yes/no/unknown, fee tiers by order value (e.g. [{min_order: 0, fee: 7.9}, {min_order: 120, fee: 0}]), minimum order, source URL. If the store does not deliver to {postal_code}, record that FIRST, then still price the basket (it may deliver soon / click&collect) but say so in `notes`.

## Finish
When `status` shows 0 missing, reply with: access method that worked, delivery conditions, counts by status, the 5 items with the biggest price differences vs the baseline €/unit, anything suspicious you could not resolve, and endpoint/URL tips worth adding to stores.json for next time. Keep the reply under 400 words.
````
