# Browser Automation Plan — Mercadona (+ Casa Milliere)

**Date:** 2026-05-05  
**Status:** Assessment / Plan (not yet implemented)  
**Scope:** Playwright-based cart automation triggered from the existing grocery list

---

## 1. Problem statement

The current workflow ends at the shopping list screen: the app tells you what to buy and
from which supermarket, but the last mile — opening Mercadona (or Casa Milliere), finding
each product, and clicking "Añadir al carrito" — is still manual.

Goal: a script that reads the grocery list, opens Mercadona in a real browser via
Playwright, and adds every item that has a `buscador` URL directly to the cart.
Items without a URL are skipped and reported for manual handling.

---

## 2. Data already available

The Excel inventory has everything the automation needs:

| Column     | Example                                             | Role                        |
|------------|-----------------------------------------------------|-----------------------------|
| `super`    | `mercadona`                                         | Filter: which store to open |
| `comida`   | `leche entera`                                      | Human-readable label (logs) |
| `comprar`  | `2`                                                 | Quantity to add to cart     |
| `buscador` | `https://tienda.mercadona.es/product/30917/...`     | Direct product page URL     |

**Key insight:** if `buscador` is populated, the automation navigates directly to the
product page — no search, no ambiguity about variants.

---

## 3. Feasibility assessment

### 3.1 Items with `buscador` URL — high confidence

Flow:
1. Navigate to product URL.
2. Find the quantity input or `+` button.
3. Set quantity to `comprar` value.
4. Click "Añadir" / confirm.

Mercadona product pages are server-rendered HTML with a consistent structure. The same
button and input selectors appear across all product categories. Once mapped for one
product, they work for all.

**Confidence: HIGH**

### 3.2 Items without `buscador` URL — not automated

No URL → the script cannot search reliably (wrong variant, wrong size, wrong brand).
These items are flagged in the run report. After the run you open their pages manually,
paste the URL into the Excel, and they become automated on every future run.

### 3.3 Anti-bot / detection risk

Mercadona.es runs Cloudflare. Key risks and mitigations:

| Risk                              | Mitigation                                               |
|-----------------------------------|----------------------------------------------------------|
| IP fingerprinting on login        | Reuse a real browser session via exported cookies        |
| Headless browser detection        | Run `chromium` in headed mode (`headless=False`)         |
| Rapid sequential requests         | Random human-like delays between actions (0.5–2 s)       |
| JS fingerprint checks             | Use `playwright-stealth` or the real `chrome` channel    |
| Session expiry                    | Cookie refresh procedure before each run                 |

**Recommended approach:** headed Chromium with real cookies from your existing logged-in
session. Avoids login automation entirely.

### 3.4 Casa Milliere

Same Playwright approach applies. The data model already handles multiple supermarkets
via the `super` column — the script just needs a dispatch map `super_name → handler`.
One afternoon to inspect their DOM and write the handler.

---

## 4. Technical architecture

```
system/grocery/
├── automation/
│   ├── __init__.py
│   ├── run_automation.py       # CLI entry point: reads grocery list, dispatches
│   ├── grocery_reader.py       # Reads Excel, returns list[CartItem]
│   ├── browser.py              # Playwright context factory (cookies, stealth, delays)
│   ├── mercadona.py            # Mercadona-specific selectors + add_to_cart(item)
│   ├── casa_milliere.py        # Casa Milliere handler (stub, fill in later)
│   ├── report.py               # Prints / saves run summary
│   └── cookies/
│       ├── mercadona.json      # Exported from real browser (gitignored)
│       └── casa_milliere.json  # Same
└── BROWSER_AUTOMATION_PLAN.md  # This file
```

### CartItem dataclass
```python
@dataclass
class CartItem:
    super_name: str     # "mercadona" | "casa_milliere" | ...
    comida: str         # human-readable name (for logs)
    comprar: int        # quantity to add
    buscador: str       # product URL ("" if missing)
```

### run_automation.py flow
```
1. Read grocery list → List[CartItem]
2. Filter: comprar > 0
3. Group by super_name
4. For each group:
   a. Launch headed browser with that store's cookies
   b. For each item:
      - if buscador == "" → add to skipped list, continue
      - navigate to URL
      - call handler.add_to_cart(page, item)
      - random delay 0.5–2 s
5. Close browser
6. Print / save summary: added ✅, skipped ⚠️, errors ❌
```

---

## 5. Selector map — Mercadona (guesses, must be verified with screenshots)

These are expected selectors based on Mercadona's known DOM structure. They **must be
confirmed** by inspecting the real pages (see Section 11 — Screenshot Guide).

| Action                  | Selector (CSS / text)                             |
|-------------------------|---------------------------------------------------|
| Cookie consent "Aceptar"| `button:has-text("Aceptar")`                      |
| Postal-code modal       | `[data-testid="postal-code-modal"]` (first run)   |
| Quantity `+` button     | `button[class*="product-unit__add"]`              |
| Quantity `–` button     | `button[class*="product-unit__remove"]`           |
| Quantity input field    | `input[class*="product-unit__quantity"]`          |
| "Añadir al carrito"     | `button:has-text("Añadir")`                       |
| Out-of-stock indicator  | `[class*="unavailable"]` or `[disabled]` on button|

**Selector strategy:** prefer `data-testid` attributes and text-based matchers over
positional CSS — they survive redesigns better.

---

## 6. Cookie export — step-by-step

### Export from Chrome/Edge
1. Log in to `tienda.mercadona.es` normally in Chrome or Edge.
2. Install the **Cookie-Editor** extension:
   Chrome → `chrome.google.com/webstore` → search "Cookie-Editor" (by cgagnier)
3. While on any Mercadona page, click the Cookie-Editor icon → **Export** → **Export as JSON**.
4. Save the file to:
   `E:\automation\automation\system\grocery\automation\cookies\mercadona.json`
5. Add to `.gitignore` (see Section 12).

### Verify the export
```python
import json
cookies = json.load(open("automation/cookies/mercadona.json"))
print(len(cookies), "cookies exported")
# expect ~15-30 entries, including session tokens
```

### Load in Playwright
```python
import json
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    browser = p.chromium.launch(headless=False)
    context = browser.new_context()
    context.add_cookies(json.load(open("automation/cookies/mercadona.json")))
    page = context.new_page()
    page.goto("https://tienda.mercadona.es")
    # should land logged in, not on login screen
```

### When to refresh cookies
Refresh when the script redirects to the login page instead of the product page.
Typical Mercadona session life: several weeks.

---

## 7. Test plan

### Phase 1 — Environment setup
- [ ] `& .\.venv\Scripts\pip.exe install playwright playwright-stealth`
- [ ] `& .\.venv\Scripts\python.exe -m playwright install chromium`
- [ ] Export Mercadona cookies → verify count with the snippet above

### Phase 2 — Single product smoke test
- [ ] Write minimal `mercadona.py` with one hardcoded `buscador` URL
- [ ] Run headed, confirm: page loads, item added to cart, correct quantity, no CAPTCHA
- [ ] Confirm selectors match reality (update Section 5 if not)

### Phase 3 — Full list dry run
- [ ] Read real grocery list, filter `super == "mercadona"`, `comprar > 0`
- [ ] Run with `DRY_RUN=True` (navigate + log, don't click Añadir)
- [ ] Review report: URLs found vs missing

### Phase 4 — Live run
- [ ] Run on real cart with 3–5 items
- [ ] Verify cart on Mercadona website
- [ ] Tune delays if detection triggers

### Phase 5 — Casa Milliere handler
- [ ] Take Casa Milliere screenshots (see Section 11)
- [ ] Write `casa_milliere.py` handler
- [ ] Smoke test same as Phase 2

### Phase 6 — Streamlit integration (optional, later)
- [ ] Add "▶ Run Automation" button to Shopping List screen
- [ ] Calls `subprocess.Popen(["python", "-m", "automation.run_automation"])`
- [ ] Shows live output in `st.text_area`

---

## 8. Risks and open questions

| Risk / Question                            | Notes                                               |
|--------------------------------------------|-----------------------------------------------------|
| Mercadona changes DOM / selectors          | All selectors in one file — one-line fix            |
| Session cookie expires mid-run             | Detect login redirect, abort with clear error       |
| Product unavailable / URL returns 404      | Log as error, continue with next item               |
| Postal-code modal on first load            | Handle once at browser context startup              |
| Cart has leftovers from a previous run     | Decide: merge or clear first? (ask before Phase 4) |
| Casa Milliere URL structure unknown        | Need screenshots before writing handler             |
| Different machine / IP                     | Cookies are account-scoped, not IP-scoped — fine   |

---

## 9. Effort estimate

| Phase                       | Estimate  |
|-----------------------------|-----------|
| Env setup + cookie export   | 30 min    |
| Single-product smoke test   | 1–2 h     |
| Full list dry run           | 1 h       |
| Live run + tuning           | 1–2 h     |
| Casa Milliere handler       | 1–2 h     |
| Streamlit integration       | 1 h       |
| **Total**                   | **5–8 h** |

---

## 10. .gitignore entries to add

Add these lines to the repo's `.gitignore` (or to
`system/grocery/automation/.gitignore`):

```
# Browser automation — sensitive session data
automation/cookies/
*.json.bak
```

---

## 11. Screenshot guide — what to capture before coding

The LLM implementing this needs to see the real DOM, not guess at selectors.
Before starting the coding session, take these screenshots and **attach them directly
to the conversation** (paste into the chat).

For each screenshot, **also open Chrome DevTools → Elements tab**, hover over the
relevant element so its HTML is highlighted, and include that in the screenshot or
copy-paste the raw HTML snippet.

### Mercadona — screenshots to take

Open `tienda.mercadona.es` while logged in.

| # | What to capture                                           | How                                                    |
|---|-----------------------------------------------------------|--------------------------------------------------------|
| 1 | **Product page — full view**                              | Open any product URL from your `buscador` column. Scroll so the "Añadir" button and quantity controls are visible. Screenshot the whole screen. |
| 2 | **"Añadir" button — DevTools HTML**                       | Right-click the "Añadir" (add to cart) button → Inspect. Screenshot the Elements panel showing the button's `<button>` tag and its `class`, `data-*`, and `id` attributes. |
| 3 | **Quantity `+` / `–` controls — DevTools HTML**           | Same: right-click the `+` button → Inspect. Screenshot the HTML for both `+` and `–` and the number display between them. |
| 4 | **Postal-code modal** (if it appears on first visit)      | Screenshot the full modal with DevTools open on the confirm button's HTML. |
| 5 | **Cookie consent banner** (if it appears)                 | Screenshot the banner and the "Aceptar" button's HTML. |
| 6 | **Cart page — item listed**                               | After adding one item manually, open the cart. Screenshot the item row so we know what "success" looks like. |
| 7 | **Out-of-stock state** (if you can find one)              | Screenshot a product that is unavailable — shows what the disabled/greyed button looks like so the script can detect it. |

### Casa Milliere — screenshots to take

Same set as above, applied to Casa Milliere's website while logged in.

| # | What to capture                         |
|---|-----------------------------------------|
| 1 | Product page — full view                |
| 2 | "Añadir" / add-to-cart button HTML      |
| 3 | Quantity controls HTML                  |
| 4 | Any login/address modal on first load   |
| 5 | Cart page with one item                 |

### What to copy from DevTools (paste as text, not screenshot)

Screenshots of the Elements panel are helpful, but **copy-pasting the raw HTML** is
even better. For each button of interest:

1. Right-click element → Inspect.
2. In the Elements panel, right-click the highlighted `<button>` or `<input>` tag.
3. Select **Copy → Copy outerHTML**.
4. Paste into the chat as a code block.

This gives the LLM the exact `class` names, `data-testid`, `aria-label`, and `id`
values — no guessing required.

---

## 12. Pre-flight checklist — everything to prepare before the coding session

Work through this list **before** opening a new conversation to implement the code.
When all boxes are checked, you can hand the LLM this document + the screenshots and
get a working implementation in one shot.

### A. Environment
- [ ] Python venv exists at `E:\automation\automation\.venv`
- [ ] `playwright` installed: `& .\.venv\Scripts\pip.exe install playwright playwright-stealth`
- [ ] Chromium installed: `& .\.venv\Scripts\python.exe -m playwright install chromium`
- [ ] `automation/cookies/` folder exists (create manually or let the LLM create it)

### B. Cookies
- [ ] Logged in to `tienda.mercadona.es` in Chrome/Edge
- [ ] Cookie-Editor extension installed
- [ ] Mercadona cookies exported to `automation/cookies/mercadona.json`
- [ ] Cookie count verified (>10 entries)
- [ ] (When ready) Casa Milliere cookies exported to `automation/cookies/casa_milliere.json`

### C. Screenshots and HTML snippets
- [ ] Mercadona screenshots 1–6 taken (see Section 11)
- [ ] "Añadir" button outerHTML copied from DevTools
- [ ] Quantity `+` button outerHTML copied from DevTools
- [ ] (When ready) Casa Milliere equivalents

### D. Grocery list
- [ ] At least 2–3 Mercadona items in Excel have a real `buscador` URL filled in
- [ ] Those items have `comprar > 0` (i.e. they actually need buying today)
- [ ] You know the path to the Excel file (check `config.json → data.xlsx_file`)

### E. Existing code context to attach to the new conversation
- [ ] This file: `system/grocery/BROWSER_AUTOMATION_PLAN.md`
- [ ] `system/grocery/data.py` (shows how Excel is read/written — for `grocery_reader.py`)
- [ ] `system/grocery/config.json` (shows Excel path and column names)

---

## 13. LLM briefing template — paste this to start the implementation session

Copy the block below and paste it as your first message in a new Claude Code session.
Then attach/paste: this document, `data.py`, `config.json`, the screenshots, and the
copied HTML snippets.

---

```
I want to implement Playwright browser automation for my grocery app.
Read BROWSER_AUTOMATION_PLAN.md first — it has the full context, architecture,
file layout, and selector guesses.

The existing app lives at E:\automation\automation\system\grocery\.
It reads a grocery list from an Excel file (see data.py and config.json).
The Excel has columns: super, comida, comprar, buscador.
Items with comprar > 0 and a buscador URL need to be added to the online cart.

I'm attaching:
- BROWSER_AUTOMATION_PLAN.md   ← full plan
- data.py                      ← existing Excel reader (for reference)
- config.json                  ← Excel path and column names
- [screenshots of Mercadona product page, add-to-cart button, quantity controls]
- [HTML snippets copied from DevTools for the relevant buttons]

Please implement the automation/ folder as described in the plan (Section 4).
Start with Phase 1 environment setup verification and Phase 2 — a single-product
smoke test for Mercadona. Use the real HTML I've attached to write accurate selectors.
Run headed (headless=False). Load cookies from automation/cookies/mercadona.json.
Use playwright-stealth. Add random delays of 0.5–2 s between actions.
Python venv is at .venv — invoke via & .\.venv\Scripts\python.exe.
```

---

## 14. Next action

1. Work through the pre-flight checklist (Section 12) — especially cookies and screenshots.
2. Open a new conversation, paste the briefing template (Section 13).
3. Attach this file + `data.py` + `config.json` + screenshots + HTML snippets.
4. The LLM implements Phase 1 + Phase 2 smoke test.
5. Come back to this document, tick off the test plan boxes as phases complete.
