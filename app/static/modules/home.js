// Home: the landing page. The This week card answers
// "what does this week still need?" with one row each for the audit, the
// list, the carts and the prices; a row tap opens the tab that owns the fact
// (one home per fact). Under it, a search over every item, its rows opening
// the item sheet.
//
// The card is painted once with all four rows and then only its values
// change: a value that arrives late (the last audit, the last fill, the
// benchmark) fills its row in place, so nothing shifts the layout.
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { lastAudit, lastAuditText, loadLastAudit } from "./audio.js";
import { fillMissingCount, lastFillValue } from "./automation.js";
import { activePaneBody, c, filteredItems, state } from "./core.js";
import { icon } from "./dom.js";
import { valueRow } from "./inventory.js";
import { itemList } from "./rows.js";
import { loadPricesReview, pricesReviewValue } from "./stores/index.js";

// Each row: its value's id, the mode its tap opens, its glyph and its label.
const WEEK_ROWS = [
  { key: "audit", mode: "audio", glyph: "list-checks", label: "Audit" },
  { key: "list", mode: "shopping", glyph: "shopping-basket", label: "Shopping list" },
  { key: "carts", mode: "shopping", glyph: "shopping-cart", label: "Carts" },
  { key: "prices", mode: "stores", glyph: "tag", label: "Prices" },
];

function host() {
  return document.querySelector("#this-week-host");
}

function weekRow({ key, mode, glyph, label }) {
  return `<li class="action-row">
      <button type="button" class="action-row-main sheet-link" data-track="home.goto-${mode}" data-goto="${mode}">
        ${icon(glyph, "sheet-link-icon")}
        <span class="action-row-title">${label}</span>
        <span class="sheet-link-value" id="week-${key}">–</span>
        ${icon("chevron-right", "sheet-link-chevron")}
      </button>
    </li>`;
}

export function renderHome() {
  const node = host();
  if (node && !node.firstElementChild) {
    node.innerHTML = `<section id="this-week" class="card this-week" aria-labelledby="this-week-title">
        <div class="card-head"><h2 id="this-week-title" class="card-title">${icon("house")}This week</h2></div>
        <ul class="action-rows this-week-rows">${WEEK_ROWS.map(weekRow).join("")}</ul>
      </section>`;
  }
  paintThisWeek();
  renderSearch();
}

// Tab entry: read what the rows show that the inventory doesn't carry. The
// last fill is read at boot and on every return to the foreground (app.js).
export function loadThisWeek() {
  loadLastAudit();
  loadPricesReview();
}

// "Audited 6 days ago": Home's page-header line when nothing needs you, in
// calendar days. Empty until the last audit has been read.
export function auditAgeText() {
  const last = lastAudit();
  if (last === undefined) return "";
  if (!last) return "No audit yet";
  const day = (d) => new Date(d.getFullYear(), d.getMonth(), d.getDate());
  const days = Math.round((day(new Date()) - day(new Date(last.timestamp))) / 86400000);
  if (Number.isNaN(days)) return ""; // a log with no readable timestamp
  if (days <= 0) return "Audited today";
  return days === 1 ? "Audited yesterday" : `Audited ${days} days ago`;
}

function listValue() {
  const s = state.payload.summary;
  if (!s.shopping_items) return { text: "Nothing to buy" };
  return { text: `${s.shopping_items} items · ${s.shopping_units} units` };
}

function paintValue(key, { text, tone = "" }) {
  const node = document.querySelector(`#week-${key}`);
  if (!node) return;
  node.textContent = text;
  if (tone) node.dataset.tone = tone;
  else delete node.dataset.tone;
}

// The values only, in place; also called through core's updateHeaders() when
// a late read (the last audit, the last fill, the benchmark) lands.
export function paintThisWeek() {
  const card = document.querySelector("#this-week");
  if (!card || !state.payload) return;
  paintValue("audit", { text: lastAudit() === undefined ? "–" : lastAuditText() });
  paintValue("list", listValue());
  paintValue("carts", lastFillValue());
  paintValue("prices", pricesReviewValue());
  card.dataset.state = "ready";
}

// The search: nothing under the box until something is typed, then every
// item that matches, one row each, a tap opening the item sheet.
function renderSearch() {
  const body = activePaneBody();
  if (!state.query) {
    body.replaceChildren();
    return;
  }
  const cols = c();
  const rows = filteredItems().map((item) => valueRow(item, cols)).join("");
  body.innerHTML = rows ? itemList(rows) : emptyStateEl("search", "No matching items.").outerHTML;
}

// Home's page-header line: the last fill's misses, else when you last counted.
export function homeContext() {
  return { exceptions: [[fillMissingCount(), "not in cart", "danger"]], fallback: auditAgeText() };
}
