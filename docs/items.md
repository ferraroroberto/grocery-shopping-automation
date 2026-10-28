# Items: editing, adding and finding store products

## 📦 Items (Items tab)
The **Items** tab has two segments, **Items** and **Prices**. **Items** is every item in one list, grouped by zone (A–Z inside a zone), each row showing its store, what to buy and its in-stock/target figures. The search box above it filters the list by item name, zone or supermarket. Tap any row to open its item sheet: set the target and in-stock count, change the name, zone, store or buy link, see its stores and prices, or delete it. Nothing is written until **Save**, except the store pages and **Fallback if short**, which write when you save them there. **Fallback if short** names the store and product link the cart run sends the missing units to when the item's own product comes up short (a stock cap, out of stock): pick a registered store, paste the product link, **Save fallback**; **Clear fallback** blanks both. They live in two optional spreadsheet columns, `fallback_super` and `fallback_url`, which a sheet gains only when a fallback is first saved. A link that is a search page or lacks the store's product id is saved as typed but flagged, and a fallback equal to the item's own buy link is refused. (The legacy Streamlit app keeps its separate Edit targets / Edit item / Add item modes.)

### 📦 Units per pack
Stock and target are counted in **units** ("14 apples"), but some products are sold by the pack (a 1 kg bag holds about 5 apples). The item sheet's **Units per pack** row (and the same field in the Add item form) holds that conversion: a positive number, decimals allowed, empty meaning 1. The cart run buys `packs = ceil(units needed / units per pack)`, so any shortfall buys at least one whole pack and nothing needed buys 0; an item without a value sends its units unchanged. "Buy" on a converted item reads as units and packs (*14 units → 3 packs*) in the Items, Audit and Shop lists. The conversion lives in an optional spreadsheet column, `units_per_pack`, which a sheet gains only when a value is first saved (an existing sheet without it loads unchanged). It is applied once, where the cart run reads the inventory (`automation/grocery_reader.py`), and each converted item logs one `📦` line (item, units needed, units per pack, packs) so a wrong quantity is diagnosable. Through the API, `PUT /api/items/{id}` with `units_per_pack` sets it, `null` clears it, and omitting the field leaves it alone.

**Add item** (beside the search box) opens the Add item sheet: **Find a store product** first (below), then **Add by hand**, a form for the name, supermarket, zone, target, in-stock count, units per pack and URL. Supermarket and Zone are pickers of the values already in the sheet (no typing on a phone); choose **New supermarket…** / **New zone…** to enter a first-time one. The sheet's **Add item** saves the item and closes the sheet; Esc or × discards. A search's results stay there when you reopen the sheet.

### 🔎 Find a store product (Add item sheet)
Find the store product that fills an item's `buscador` so the cart automation
can buy it. **Speak or type a product in Spanish** (e.g. *"añade sandía"*); the
spoken clip is transcribed by whisper (language auto-detect) and parsed into the
search term the same way the HA voice bridge parses commands — with a short
budget (`audio_audit.product_search_parse_timeout`, default 20 s, no retries),
falling back to searching your raw text if the hub is slow or down. The app
then searches **Mercadona** (its Algolia search endpoint) and **Carrefour** (a
real-Chrome DOM read of its results grid, Cloudflare-gated like its cart
handler), driving the logged-in Chrome profile. Carrefour cards whose add
button is disabled (*Agotado temporalmente*) are skipped: their product page
redirects away, so the cart run could never add them. Ametller is not searched (its cart handler and stored links work as before). Each store's cards
appear **as soon as that store answers**, under one line per store — *waiting
for the browser*, *searching…*, *N results*, *no results*, or *couldn't search*
(session, slow site, or network) — so a slow or failed store never hides the
other's results and never reads as "no results". The candidates are **cards
you validate**:
each card has the product name, store, price and a **link to open the product
and check it yourself**. The stores are always searched with **your own words**
— the results are headed *Results for “boniato”* — never with the
name of an inventory row the text happened to match. A term is tagged **New**,
or, when it matches an item already on the list, shows *Matches your item …*,
and **Use** acts on that:

- **New** — **Use** fills the **Add by hand** form below with the term as the
  name, the card's store and product link, and a target of 1; pick the zone
  (and adjust the quantities) and tap **Add item**. Nothing is saved until
  then, and the card then reads **Added**.
- **Matches your item** — **Use** opens a compact confirm row on the card —
  a **zone** combo (the inventory's existing zones) plus **have** and
  **target** quantities, prefilled from the item's current row. Tapping
  **Update item** writes the product's URL to that item's `buscador` and
  applies the store, zone, and quantities; **Add as new item** instead hands
  the card to the Add by hand form under the term you searched.

**Nothing is auto-picked**; you always choose. A term the
stores don't carry (e.g. *"añade flurbos"*) simply returns no cards. The search
runs on demand with a live elapsed timer and a Cancel button (it drives a real
browser, so it takes seconds-to-minutes) and reuses the same `automation/`
launch helpers and shared-profile serialization as the cart automation.
Each run's phases (parse model + time, browser wait, per-store elapsed and
failures) are appended to `logs/product_search.log` (gitignored) — the webapp
has no console, so that file is where a slow or empty search is diagnosed.

> **Pre-requisites:** the stores must be logged in
> (`python -m automation.bootstrap_session`), and the LLM hub (`:8000`) +
> the hub's speech-to-text route (also `:8000`) answering for the spoken-term parse/transcription.

