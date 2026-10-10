// The one item row (#254): every list that shows an inventory item draws it
// here, on the vendored action-row (design.md "action-row", "One renderer").
// A row is an optional leading check, the name with one muted line, and one
// trailing item — a stepper, a value or a link. Tapping the row opens the
// item (app.js "open-item"), the hook the item sheet plugs into. Pure markup:
// nothing here reads app state.
import { chip, esc, html, icon } from "./dom.js";

// `lead`: { pressed, action, label } for the leading check (an item got), or
// null. `chip`: { label, tone } closing the meta line, for an exception only.
// `trail`: the markup of the one trailing item (stepper(), rowValue(),
// rowLink(), rowVerb()). `attrs`: the row's data-* hooks. `open: false` keeps
// the row inert (a free-text quick-add has no item to open).
export function itemRow({ title, meta = "", chip: status = null, lead = null, trail = "", attrs = "", open = true, struck = false }) {
  const name = struck ? `<s>${html(title)}</s>` : html(title);
  const metaLine = meta || status
    ? `<span class="action-row-meta">${esc(meta)}${status ? `${meta ? " " : ""}${chip(status.label, status.tone)}` : ""}</span>`
    : "";
  const body = `<span class="action-row-title">${name}</span>${metaLine}`;
  const main = open
    ? `<button type="button" class="action-row-main" data-action="open-item" aria-haspopup="dialog">${body}</button>`
    : `<div class="action-row-main">${body}</div>`;
  const check = lead
    ? `<button type="button" class="icon-button action-row-check" data-action="${lead.action}" aria-pressed="${lead.pressed}" aria-label="${esc(lead.label)}">
        ${icon("circle", "action-row-check-off")}${icon("circle-check", "action-row-check-on")}
      </button>`
    : "";
  return `<li class="action-row item-row" ${attrs}>${check}${main}${trail}</li>`;
}

// The list the rows sit in: a full-bleed action-list card, or bare rows when
// the host is already a card (a disclosure body).
export function itemList(rows, { bare = false } = {}) {
  const list = `<ul class="action-rows">${rows}</ul>`;
  return bare ? list : `<div class="card action-list">${list}</div>`;
}

// The one stepper (#254): − value +, each button a real 44px target, sized so
// the row's name keeps its width (design-review J-01/J-10). `kind` is
// "current" (in stock) or "target"; the actions are app.js's mutate hooks.
const STEPPER_LABEL = { current: "in stock", target: "target" };

export function stepper(kind, value, name) {
  const label = STEPPER_LABEL[kind];
  return `<div class="stepper action-row-trail" role="group" aria-label="${esc(`${name}: ${label}`)}">
    <button type="button" class="icon-button" data-action="${kind}-minus" aria-label="Decrease ${label}" title="Decrease ${label}">${icon("minus")}</button>
    <output class="stepper-value">${esc(value)}</output>
    <button type="button" class="icon-button" data-action="${kind}-plus" aria-label="Increase ${label}" title="Increase ${label}">${icon("plus")}</button>
  </div>`;
}

// A read-only trailing figure (a count, a stock/target pair).
export function rowValue(value) {
  return `<span class="action-row-trail action-row-value">${esc(value)}</span>`;
}

// A trailing link out (a product page), opened in a new tab.
export function rowLink(url, label, glyph = "external-link") {
  return `<a class="icon-button action-row-trail" href="${esc(url)}" target="_blank" rel="noopener noreferrer" aria-label="${esc(label)}" title="${esc(label)}">${icon(glyph)}</a>`;
}

// A trailing glyph verb on the row's own hook (data-action).
export function rowVerb(action, label, glyph) {
  return `<button type="button" class="icon-button action-row-trail" data-action="${action}" aria-label="${esc(label)}" title="${esc(label)}">${icon(glyph)}</button>`;
}
