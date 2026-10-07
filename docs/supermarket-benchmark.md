# Supermarket benchmark — methodology

Reference for `benchmark/` and the `/supermarket-benchmark` project skill (issue #145). The skill (`.claude/skills/supermarket-benchmark/SKILL.md`) owns the step-by-step recipe and the research-agent prompt; this document owns the *why* and the data contracts.

## Question it answers

For the household's real basket, delivered to the configured postal code: is today's split (Mercadona + Ametller Origen) the cheapest option **at equal or better quality**, or should we consolidate into one store or re-split across two or three? Quality is a hard constraint — a cheaper product that fails its spec is not an option at any price.

## Pipeline

```
list.xlsx + purchase_logs/ ──build_basket──▶ basket.json ──(1 research agent per store)──▶ stores/<store>.json
                                   ▲                                   │  via benchmark.results (validated)
          _state/quality_specs.json│                                   ▼
          (human-reviewed tiers/specs)                        score ──▶ scenarios.json + report.md
                                                                 │
                                                  --promote ──▶ _state/mappings/<store>.json (next run's head start)
                                                                 ├─▶ _state/history.jsonl  (one summary per run)
                                                                 └─▶ _state/prices.csv     (every offer, every run)
                        report_notes.json (orchestrator's findings) ──▶ report ──▶ report.html ──▶ one living Artifact
                                                                                   (URL in _state/report.json)
```

All of `benchmark_runs/` is gitignored — the repo is public, and prices, basket contents and product mappings are household data.

## The basket

- **Items:** every inventory row bought at least once in `purchase_logs/`, plus rows with a target > 0 that were never bought (priced at their target quantity).
- **Monthly quantity:** Σ `comprar` across all logs ÷ months spanned. The span is first-to-last order date **plus one median gap between orders**, because the last order feeds the household for the days after it.
- **Current product:** the product at the store we buy from today, fetched live:
  - Mercadona: public `/api/products/{id}/?wh=<warehouse>`. The warehouse comes from the delivery postal code, and some products 404 without it. When a product has no `unit_size`, the pack size is recovered as price ÷ reference price.
  - Ametller: SCAPI Shopper Products over plain HTTP with a guest token (`benchmark/ametller_guest.py`; the storefront's own public client, PKCE guest flow). It carries `c_ao_ingredientes`, `unitQuantity` and `unitMeasure`. The shared logged-in profile's token went stale (HTTP 401) mid-run, so it is not used.
- **Count packs are priced per piece.** Mercadona's "1 ud" for a package of 40 bin bags or 8 toilet rolls becomes 40 or 8 `ud`, using `total_units`. A challenger's 20-bag or 24-roll pack then compares fairly. The first run's agents recorded whole packs as `1` until this rule was added, which made some stores look 30× cheaper.
- **Overrides** (`_state/quality_specs.json`, keyed by item key):
  - `tier` / `spec` replace the auto-suggestion.
  - `current_url` prices a replacement for a dead or plain-text `buscador`.
  - `current_fields` corrects a store-misreported pack size. Ametller reports a 6-egg box as "3 dz", for example.
  - `exclude` + `reason` drops an item, e.g. out of season.

## Quality tiers

| Tier | Applies to | Rule |
|---|---|---|
| A | Real brands (not store own-labels) | Same brand **and** variant; EAN when available. A different brand is `not_found`. |
| B | Deli, meat, fish, eggs | Spec-locked. The **MATCH** bar is "no worse than today": meat/fish % ≥ today's, additive list no worse, same cut and format, same eco or free-range class, same fresh vs frozen. For deli, an extra **UPGRADE** bar (the household's stated standard: ≥98% meat, clean label) is priced separately and reported, but never used for the headline comparison. |
| C | Pantry, dairy, produce | The defining specs must hold: 100% peanut, 99% cacao, natural not in oil, same fruit variety when named, category I or better. Brand is free. |
| D | Cleaning, paper | Cheapest product with the same function and a comparable format. |

Auto-suggestion order: a non-own-label brand → A; category `droguería` → D; meat, fish and deli keywords or category `carne y pescado` → B; everything else → C. The orchestrator reviews every tier-B spec against the real ingredient list and confirms it with the user before any research runs.

## Research records

Written only through `python -m benchmark.results`; its docstring is the schema. What the validator enforces:
- `pack_size` is in the **basket item's unit**, which removes the most common scoring error (grams vs kg).
- A match (`exact` / `equivalent`) carries a price, a size, a URL and verbatim `evidence`.
- The file is written atomically, one item at a time, so a research agent can crash and resume from `status`.

The baseline store's own items are never researched: the scorer uses `basket.json` for them.

## Scoring

- **Unverified matches don't count:** a food match (tier A/B/C) recorded at `confidence: low` could not prove it clears its quality bar. It is excluded from every scenario and listed per store as "unverified"; commodity (tier D) matches still count. Some stores (Consum, Condis) publish no ingredient lists, so their coverage is understated rather than their quality overstated — which is the safe direction.
- **Per kg, litre or piece, any pack size.** The household buys in volume, so a bigger pack at a lower €/kg is a real saving, not a distortion. A store record may carry `alternatives` (other qualifying products, e.g. the same-size pack next to a bulk one); every candidate is compared per unit and the cheapest counts. Agents add alternatives with `benchmark.results alt` rather than overwriting a match, so no priced candidate is ever lost. (A same-pack-size rule was tried on the first run and dropped at the user's request: it discarded real bulk savings.)
- **Item cost:** pro-rata, i.e. monthly base quantity ÷ store pack size × pack price — the €/unit times what the household uses. There is no pack rounding: over a month of repeated orders pack sizes even out, and rounding every item up would bias every challenger store upward.
- **Delivery:** each store in a scenario is ordered from `orders_per_month` times, the household's observed frequency. The per-order fee comes from `fee_tiers` at the per-order value.
  - Below the store's minimum order, orders are merged into fewer, larger orders, and this is flagged.
  - Each scenario also gets a **fee-optimised** total. Every store may be ordered from less often, never below 2 orders a month (fresh food), when that lowers its monthly delivery cost, e.g. by clearing a free-delivery tier. The status quo gets the same treatment, so the deltas compare like with like. Treat it as "what adjusting the ordering rhythm is worth", not as a prediction.
  - An unknown fee or unconfirmed delivery coverage is **flagged, never silently treated as fine**.
- **Scenarios:**
  - the status quo;
  - each store alone: coverage %, a common-subset Δ vs today, and a total with the missing items filled in at today's store;
  - the cheapest fully-covering combinations of 1 to 5 stores (`MAX_COMBO_STORES`), where each item goes to its cheapest store in the combination, ranked by the fee-optimised total;
  - **maximum savings**: the cheapest of those at any store count;
  - **recommended**: grow one store at a time and accept a bigger plan only when it saves at least **€10 a month per extra store** (`EXTRA_STORE_MIN_SAVING`; a 5-store plan beating a 3-store one must save €20) — every extra store is another account, delivery slot and checkout;
  - a re-split of today's two stores;
  - a no-fees lower bound.
- **Audit aids:** outliers (store €/unit < 0.5× or > 2× today's, usually a unit or pack error); tier-B upgrades with their monthly Δ; and a diff vs the previous run (price moves > 5%, offers gone).

## History and the living report

- `python -m benchmark.score <run> --promote` finalises a run. It writes the carried-forward product mappings and records the run through `benchmark/history.py`:
  - `_state/history.jsonl`: one line per run with the status-quo, re-split, recommended and maximum-savings totals, plus per-store coverage, Δ, unverified count and delivery terms;
  - `_state/prices.csv`: one row per run × store × item, covering today's product and every store record, with pack size, pack price, €/unit, status and confidence. A price trend for any item is a filter away.
  - Re-recording a run replaces its rows, so recording is idempotent.
- `python -m benchmark.report <run>` renders `report.html` from the scored run and the history. The page has an **Over time** table with one row per run, and lists the biggest price moves since the previous run.
- Judgment the code can't derive lives in the run's `report_notes.json` (findings and caveats), written by the orchestrator.
- The report is published as **one living Artifact**: its URL is kept in `_state/report.json`, and each run republishes to the same link. A copy of the last published page is kept in `_state/report_published.html` (with the run and Artifact version in `report.json`), and every run keeps its own `report.html`, so the history of reports stays local and uncommitted.

## Store access

- Plain HTTP first. Some stores expose clean JSON: Mercadona products and categories, and Consum search.
- Stores behind a bot manager (Carrefour on Cloudflare, Dia on Akamai) go through `python -m benchmark.browser_fetch`. It runs real Chrome with the repo's stealth launch config (`automation/browser.py::_open_context`), on a **per-store throwaway profile** under `benchmark_runs/_state/chrome/<store>/`.
  - That lets parallel research agents never collide with each other.
  - It also keeps them off the shared store-login profile used by the cart automation.
- What each store needed on the last run lives in `benchmark/stores.json` `hints`. Update it after every run.

## Practical costs the numbers don't include

Mercadona, Ametller and Carrefour have a cart-automation handler (`automation/`); Ametller and Carrefour also have a confirmation-email parser (`automation/email_parsers/`). Any other store has neither, so switching means either building both or going back to filling the cart by hand — see `automation/README.md` for the current per-store coverage. The recommendation has to weigh that against the monthly saving.
