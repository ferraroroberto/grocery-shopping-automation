// The item sheet's "Fallback if short" page (#284): the store and product link
// the cart run sends the missing units to when the item's own product comes up
// short (#283). Two spreadsheet cells, saved together at once like the sheet's
// store pages; the server owns the rules (a registered store, an http(s) link,
// not the item's own buy link) and flags a link that isn't a product page.
import { fetchJson } from "./api.js";
import { state } from "./core.js";
import { esc, icon } from "./dom.js";
import { setDialogStatus } from "./stores/dialog.js";
import { jsonInit, local } from "./stores/state.js";

// A store as the Stores registry names it ("Ametller Origen"), else as typed.
function storeLabel(key) {
  return local.meta?.stores.find((s) => s.key === key)?.name || key;
}

function hostname(url) {
  try {
    return new URL(url).hostname.replace(/^www\./, "");
  } catch (_) {
    return url;
  }
}

// The muted value on the sheet's Fallback row.
export function fallbackSummary(record) {
  const fallback = record?.fallback;
  if (!fallback) return { text: "None" };
  if (fallback.kind !== "product") return { text: `${storeLabel(fallback.store)} · check link`, tone: "attention" };
  return { text: `${storeLabel(fallback.store)} · ${hostname(fallback.url)}` };
}

// A saved link that isn't a product page is said so, not posed as one.
const FLAG_TEXT = {
  search: "This link opens a search page, not the product.",
  suspect: "This link may not open the product page.",
};

function flagMarkup(fallback) {
  if (!fallback || fallback.kind === "product") return "";
  return `<p class="review-warn" role="status">${icon(fallback.kind === "search" ? "search" : "circle-alert")}${FLAG_TEXT[fallback.kind] || FLAG_TEXT.suspect}</p>`;
}

// Fill the page from the item's saved fallback. A store the registry doesn't
// list (or that isn't loaded yet) is still offered, so the picker never drops
// the saved one.
export function paintFallbackPage(dialog, record) {
  const fallback = record?.fallback;
  const stores = [...(local.meta?.stores || [])];
  if (fallback && !stores.some((s) => s.key === fallback.store)) stores.push({ key: fallback.store, name: fallback.store });
  dialog.querySelector('[data-page="fallback"]').innerHTML = `<form class="fallback-form" data-fallback-form novalidate>
      <p class="hint">When this item is short at its store, the cart run sends the missing units here.</p>
      <label class="field-label">Fallback store
        <select class="select-native" name="fallback-store" data-fallback-store>
          <option value="">None</option>
          ${stores.map((s) => `<option value="${esc(s.key)}"${fallback?.store === s.key ? " selected" : ""}>${esc(s.name)}</option>`).join("")}
        </select>
      </label>
      <label class="field-label">Fallback product link
        <input class="field" name="fallback-url" type="url" inputmode="url" autocomplete="off" spellcheck="false"
               placeholder="https://…" value="${esc(fallback?.url || "")}" data-fallback-url>
      </label>
      ${flagMarkup(fallback)}
      <div class="review-edit-actions">
        <button type="submit" class="button-tint">Save fallback</button>
        ${fallback ? `<button type="button" class="button-surface" data-fallback-clear>Clear fallback</button>` : ""}
      </div>
    </form>`;
}

// Save, or with `clear` blank both cells. `after()` repaints the sheet.
async function write(dialog, id, body, done, after) {
  const buttons = [...dialog.querySelectorAll("[data-fallback-form] button")];
  buttons.forEach((b) => { b.disabled = true; });
  setDialogStatus(dialog, "");
  try {
    state.payload = await fetchJson(`/api/items/${id}/fallback`, jsonInit("PUT", body));
    after();
    setDialogStatus(dialog, done, "ok");
  } catch (error) {
    setDialogStatus(dialog, error.message);
    buttons.forEach((b) => { b.disabled = false; });
  }
}

// The sheet's delegated submit and click; false when the event isn't ours.
export function onFallbackSubmit(event, dialog, id, after) {
  const form = event.target.closest("[data-fallback-form]");
  if (!form) return false;
  const store = form.elements["fallback-store"].value;
  const url = form.elements["fallback-url"].value.trim();
  write(dialog, id, { store, url }, store || url ? "Fallback saved." : "Fallback cleared.", after);
  return true;
}

export function onFallbackClick(event, dialog, id, after) {
  if (!event.target.closest("[data-fallback-clear]")) return false;
  write(dialog, id, { store: "", url: "" }, "Fallback cleared.", after);
  return true;
}
