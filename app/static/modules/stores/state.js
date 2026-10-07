// Shared Stores view state: the `local` what-if state (picks, frequency, filters, caches),
// its localStorage persistence, and the small formatters/lookups every part of the view uses.
import { c, state } from "../core.js";

export const PICKS_KEY = "grocery.storePicks";

export const FREQ_KEY = "grocery.storeFrequency";

export const SHOW_ALL_KEY = "grocery.storesShowAll";

export const FILTER_KEY = "grocery.storesFilter";

export const STORE_FILTER_KEY = "grocery.storesStoreFilter";

export const TARGET_ONLY_KEY = "grocery.storesTargetOnly";

export const FILTERS = [["all", "All"], ["needs", "Needs checking"], ["checked", "Checked"]];

const money = new Intl.NumberFormat("en-IE", { style: "currency", currency: "EUR" });

const decimal = new Intl.NumberFormat("en-IE", { maximumFractionDigits: 3 });

// Module-local view state. `picks` (item id → store) and the frequency are the
// what-if; localStorage only remembers them per viewer, best-effort.
export const local = {
  meta: null,         // GET /api/stores
  metaState: "idle",  // idle | loading | ready | error
  checks: null,       // GET /api/stores/checks — review flags + checked dates
  sim: null,          // last POST /api/stores/simulate response
  simState: "loading", // loading | ready | empty | stale | error
  simUpdated: null,
  picks: readStored(PICKS_KEY, {}),
  frequency: readStored(FREQ_KEY, ""),
  showAll: readStored(SHOW_ALL_KEY, false), // chips/picker for every benchmarked store
  filter: storedFilter(),                   // all | needs | checked
  stores: storedStores(),                   // list stores the rows are narrowed to; none = every store
  targetOnly: readStored(TARGET_ONLY_KEY, false) === true, // only rows with a target (cantidad) above 0
  note: null,         // { kind: "ok" | "error", text, warning? } — header feedback
  actionNote: null,   // { kind, text } — actions card feedback
  timer: 0,
  seq: 0,
  preview: [],        // apply-preview changes shown in the apply dialog
  detailId: null,     // item the detail dialog is showing
  detail: null,       // GET /api/items/{id}/store-detail
  detailError: "",
  editing: null,      // store whose edit form is open in the detail dialog
  qtyDraft: {},       // unsaved target / in-stock count typed in the detail dialog
};

function readStored(key, fallback) {
  try {
    const raw = localStorage.getItem(key);
    return raw ? JSON.parse(raw) : fallback;
  } catch (_) {
    return fallback;
  }
}

export function writeStored(key, value) {
  try {
    localStorage.setItem(key, JSON.stringify(value));
  } catch (_) {
    // private mode / blocked storage — the what-if still works for this visit
  }
}

function storedFilter() {
  const value = readStored(FILTER_KEY, "all");
  return FILTERS.some(([key]) => key === value) ? value : "all";
}

function storedStores() {
  const value = readStored(STORE_FILTER_KEY, []);
  return Array.isArray(value) ? value.filter((key) => typeof key === "string") : [];
}

export function eur(value) {
  return value === null || value === undefined ? "–" : money.format(value);
}

export function num(value) {
  return decimal.format(value);
}

export function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"}`;
}

export function storeName(key) {
  return local.meta?.stores.find((s) => s.key === key)?.name || key || "?";
}

export function hasHandler(key) {
  return !!local.meta?.stores.find((s) => s.key === key)?.has_handler;
}

export function itemById(id) {
  return (state.payload?.items || []).find((item) => item.id === id);
}

export function currentStore(item) {
  return String(item?.[c().super] ?? "").trim().toLowerCase();
}

export function pickFor(item) {
  return local.picks[item.id] || currentStore(item);
}

// Only picks that change something — stale entries (the item was deleted or
// already moved to that store) are dropped as a side effect.
export function activePicks() {
  const out = {};
  for (const [id, store] of Object.entries(local.picks)) {
    const item = itemById(Number(id));
    if (item && store && store !== currentStore(item)) out[id] = store;
  }
  local.picks = out;
  return out;
}

export function frequency() {
  const options = local.meta?.frequencies || {};
  return local.frequency && options[local.frequency] ? local.frequency : (local.meta?.default_frequency || "weekly");
}

export function setPick(id, store) {
  const item = itemById(id);
  if (!item) return;
  if (!store || store === currentStore(item)) delete local.picks[id];
  else local.picks[id] = store;
  writeStored(PICKS_KEY, local.picks);
}

export function jsonInit(method, body) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) };
}
