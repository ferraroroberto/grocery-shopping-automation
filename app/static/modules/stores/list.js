// Stores: the item list — review badges, per-item rows, the status and store filters, and the
// repaint helpers the rest of the view calls after a change.
import { emptyStateEl } from "../../_vendored/empty-state/empty-state.js";
import { c, filteredItems, state } from "../core.js";
import { chip, esc, icon } from "../dom.js";
import { FILTERS, STORE_FILTER_KEY, currentStore, eur, itemById, local, pickFor, plural, storeName, writeStored } from "./state.js";

// Review flags (src/store_links.CHECK_FLAGS): badge wording + the longer reason.
export const FLAG_LABELS = {
  moved: ["Moved store", "Your list buys it at a different store than the benchmarked one"],
  pack_x2: ["Pack ×2+", "The new pack is at least twice, or at most half, the old one"],
  unit_mismatch: ["Unit mismatch", "The old and new packs are in different units"],
  search_link: ["Search link", "The list's link opens a search page, not the product"],
  suspect_link: ["Suspect link", "The list's link may not open the product page"],
  override: ["Your override", "You saved your own values for a store"],
  benchmark_changed: ["Benchmark changed", "A newer run changed the values your override was made against"],
  stock_unconverted: ["In stock not converted", "Your in-stock count may still be in the old packs"],
};

// Stores the list buys from today; the rest are only shown on request so a
// row carries its three real options, not nine benchmark ones.
function storesInUse() {
  return new Set((state.payload?.items || []).map(currentStore).filter(Boolean));
}

// "Only items I buy" (#172): a target of 0 keeps the item in the list but it
// is never ordered, so it is noise when reviewing where things are bought.
function inScope(item) {
  return !local.targetOnly || Number(item[c().cantidad]) > 0;
}

// The Store filter's selection, minus any store the list no longer buys from
// (an Apply moved its last item away) — a stale pick must not hide every row.
function selectedStores(inUse = storesInUse()) {
  if (!state.payload) return local.stores;
  const kept = local.stores.filter((key) => inUse.has(key));
  if (kept.length !== local.stores.length) {
    local.stores = kept;
    writeStored(STORE_FILTER_KEY, kept);
  }
  return kept;
}

export const LINK_NOTE = {
  search: "opens a search page, not the product",
  suspect: "link may not open the product page",
};

// "checked" | "needs" (flagged, not checked) | "" — from the last checks read.
function reviewState(item) {
  const id = String(item.id);
  if (local.checks?.checked?.[id]) return "checked";
  return local.checks?.checks?.[id] ? "needs" : "";
}

export function flagLabel(flag) {
  return FLAG_LABELS[flag]?.[0] || flag;
}

// The reason a row needs checking, as the attention status chip (#254). A
// checked row is the normal state: no chip (success is never one) — the
// Checked filter still lists them.
function reviewBadge(item) {
  const flags = reviewState(item) === "needs" ? local.checks.checks[String(item.id)] : [];
  if (!flags.length) return "";
  return chip(`${flagLabel(flags[0])}${flags.length > 1 ? ` +${flags.length - 1}` : ""}`, "attention", flags.map(flagLabel).join(", "));
}

function rowMarkup(item, inUse = storesInUse()) {
  const cols = c();
  const today = currentStore(item);
  const pick = pickFor(item);
  const shown = (key) => local.showAll || inUse.has(key) || key === pick || key === today;
  const urls = Object.fromEntries(Object.entries(item.urls || {}).filter(([key]) => shown(key)));
  const kinds = item.url_kinds || {};
  const prices = local.sim?.item_prices?.[String(item.id)] || {};
  const name = item[cols.comida] ?? "";
  const options = [...new Set([today, ...Object.keys(urls)].filter(Boolean))];
  const meta = !options.length || !Object.keys(item.urls || {}).length
    ? `${today ? `${esc(storeName(today))} · ` : ""}No store links yet`
    : pick !== today ? `What-if · in list ${esc(storeName(today))}` : `Bought at ${esc(storeName(today))}`;
  const select = options.length > 1
    ? `<select class="field store-pick" data-stores-pick aria-label="Buy ${esc(name)} at">${options.map((key) =>
        `<option value="${esc(key)}"${key === pick ? " selected" : ""}>${esc(storeName(key))}${key === today ? " (in list)" : ""}</option>`).join("")}</select>`
    : "";
  // The picked store leads, then the cheapest: with up to nine links on one
  // swipeable line, what matters must not start off-screen (LAYOUT-03,
  // accepted in .fleet.toml — issue #194: one-tap product links per store
  // are how the list is used, so each chip opens that store's product page).
  const rank = ([key]) => (key === pick ? -1 : prices[key] ?? Number.MAX_VALUE);
  const chips = Object.entries(urls).sort((a, b) => rank(a) - rank(b)).map(([key, url]) => {
    const price = prices[key];
    const picked = key === pick;
    const kind = kinds[key];
    const label = `Open ${name} at ${storeName(key)}${price !== undefined ? `, ${eur(price)} a month` : ""}${kind ? ` (${LINK_NOTE[kind]})` : ""}`;
    const lead = picked ? icon("check") : kind === "search" ? icon("search") : kind === "suspect" ? icon("circle-alert") : "";
    const cls = `store-chip${picked ? " is-picked" : ""}${kind ? ` is-${kind}` : ""}`;
    const body = `${lead}<span>${esc(storeName(key))}</span>${price !== undefined ? `<span class="store-chip-price">${eur(price)}/mo</span>` : ""}`;
    return url
      ? `<a class="${cls}" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(label)}"${kind ? ` title="${esc(LINK_NOTE[kind])}"` : ""}>${body}</a>`
      : `<span class="${cls}"${kind ? ` title="${esc(LINK_NOTE[kind])}"` : ""}>${body}</span>`;
  }).join("");
  return `<li class="store-row" data-item-id="${item.id}">
    <div class="store-row-head">
      <div class="store-row-text">
        <span class="store-row-title">${esc(name)}</span>
        <span class="store-row-line">${reviewBadge(item)}<span class="store-row-meta">${meta}</span></span>
      </div>
      ${select}
      <button type="button" class="icon-button" data-stores-action="detail" aria-label="Review ${esc(name)}: stores, packs and prices">${icon("pencil")}</button>
    </div>
    ${chips ? `<div class="store-chips">${chips}</div>` : ""}
  </li>`;
}

// All · Needs checking (n) · Checked (n) — composes with the search box and
// the show-all switch; remembered per viewer.
function filterMarkup() {
  const counts = local.checks?.counts;
  const count = { needs: counts?.needs_checking, checked: counts?.checked };
  return `<div class="pills stores-filter" role="radiogroup" aria-label="Show items">${FILTERS.map(([key, label]) => {
    const on = key === local.filter;
    const n = count[key];
    return `<button type="button" class="pill${on ? " active" : ""}" role="radio" aria-checked="${on}" data-stores-filter="${key}">${label}${n !== undefined ? ` <span class="pill-count">${n}</span>` : ""}</button>`;
  }).join("")}</div>`;
}

// Store (#170): one toggle per store the list buys from, with its item count
// over the whole list — or, with "Only items I buy" on, over the items bought
// (#172), so the count matches the rows. Several can be on at once; none on =
// every store. Composes with the status pills and the search box.
function storeFilterMarkup(inUse = storesInUse()) {
  const counts = {};
  for (const item of (state.payload?.items || []).filter(inScope)) {
    const key = currentStore(item);
    if (key) counts[key] = (counts[key] || 0) + 1;
  }
  const selected = selectedStores(inUse);
  const keys = [...inUse].sort((a, b) => storeName(a).localeCompare(storeName(b)));
  if (!keys.length) return "";
  return `<div class="pills stores-filter" role="group" aria-labelledby="stores-store-label"><span class="stores-filter-label" id="stores-store-label">Store</span>${keys.map((key) => {
    const on = selected.includes(key);
    return `<button type="button" class="pill${on ? " active" : ""}" aria-pressed="${on}" data-stores-store="${esc(key)}">${esc(storeName(key))} <span class="pill-count">${counts[key] || 0}</span></button>`;
  }).join("")}</div>`;
}

export function paintFilter() {
  const host = document.querySelector("#stores-filter");
  if (!host) return;
  const focused = document.activeElement?.closest?.("[data-stores-filter], [data-stores-store]");
  const refocus = focused?.dataset.storesFilter !== undefined
    ? `[data-stores-filter="${focused.dataset.storesFilter}"]`
    : focused ? `[data-stores-store="${CSS.escape(focused.dataset.storesStore)}"]` : "";
  host.innerHTML = filterMarkup() + storeFilterMarkup();
  if (refocus) host.querySelector(refocus)?.focus();
}

// Why the list is empty; a store selection is named after it ("… at Ametller."),
// and "Only items I buy" when it hid rows the other filters would show.
const FILTER_EMPTY = {
  all: ["search", "No matching items"],
  needs: ["circle-check", "Nothing needs checking"],
  checked: ["list-checks", "No items checked yet"],
};

const storeList = new Intl.ListFormat("en", { type: "disjunction" });

export function paintList() {
  const list = document.querySelector("#stores-list");
  if (!list) return;
  const cols = c();
  const filter = local.checks ? local.filter : "all";
  const inUse = storesInUse();
  const stores = selectedStores(inUse);
  const matched = filteredItems()
    .filter((item) => filter === "all" || reviewState(item) === filter)
    .filter((item) => !stores.length || stores.includes(currentStore(item)));
  const source = matched.filter(inScope)
    .sort((a, b) => String(a[cols.comida] ?? "").localeCompare(String(b[cols.comida] ?? "")));
  const linked = source.filter((item) => Object.keys(item.urls || {}).length).length;
  document.querySelector("#stores-list-count").textContent = `${plural(source.length, "item")} · ${linked} with links`;
  const [glyph, message] = FILTER_EMPTY[state.query ? "all" : filter] || FILTER_EMPTY.all;
  const at = stores.length ? ` at ${storeList.format(stores.map(storeName))}` : "";
  const bought = matched.length ? " that you buy (target above 0)" : "";
  list.innerHTML = source.map((item) => rowMarkup(item, inUse)).join("")
    || `<li>${emptyStateEl(glyph, `${message}${at}${bought}.`).outerHTML}</li>`;
}

export function repaintRow(id) {
  const row = document.querySelector(`.store-row[data-item-id="${id}"]`);
  const item = itemById(id);
  if (!row || !item) return;
  const hadFocus = row.contains(document.activeElement);
  row.outerHTML = rowMarkup(item);
  // The picker was just used — keep keyboard focus on it across the repaint.
  if (hadFocus) document.querySelector(`.store-row[data-item-id="${id}"] [data-stores-pick]`)?.focus();
}
