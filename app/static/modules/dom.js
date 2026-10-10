// Pure presentation helpers: escaping, formatting, and the small markup
// fragments every view builds strings out of. Nothing here reads app state,
// so this module sits at the bottom of the graph and imports only vendored
// components.
import { switchEl } from "../_vendored/switch/switch.js";

// The fleet's one icon helper (vendored); re-exported so view modules keep a single import site.
export { icon } from "../_vendored/icons/icons.js";

export function text(value) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

// Raw HTML escape: null/undefined become "", never the "-" placeholder. Use it
// for editable `value=` attributes, where a "-" would be posted back as data
// (#228); `html()` is for read-only display text.
export function esc(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

export function html(value) {
  return esc(text(value));
}

// The one status chip (design.md "status chip", #254): one class, the tone in
// data-tone — neutral (a plain fact), accent (work in progress), attention
// (needs you soon) or danger (broken). Exceptions only: a normal state gets no
// chip, and success is never one. `title` carries the longer reason, if any.
export function chip(label, tone = "neutral", title = "") {
  return `<span class="chip" data-tone="${tone}"${title ? ` title="${esc(title)}"` : ""}>${esc(label)}</span>`;
}

// A pick-from-a-list field with a "New…" escape hatch, for values that live in
// the sheet (zone, supermarket) so nobody types them on a phone (#241). The
// named text input is the one source of truth the form posts; the <select>
// only fills it, and stays hidden until "New…" is chosen. A required input
// that is hidden would silently block submit, so `required` follows visibility.
const PICKER_NEW = "__new__";

export function pickerMarkup(name, caption, values, selected, newLabel) {
  const known = values.includes(selected) ? selected : (values[0] ?? PICKER_NEW);
  const options = values.map((v) => `<option value="${esc(v)}"${v === known ? " selected" : ""}>${html(v)}</option>`).join("")
    + `<option value="${PICKER_NEW}"${known === PICKER_NEW ? " selected" : ""}>${html(newLabel)}</option>`;
  const isNew = known === PICKER_NEW;
  return `<div class="picker" data-picker>
    <label class="field-label">${caption}<select class="field" aria-label="${esc(caption)}">${options}</select></label>
    <input class="field" name="${name}" value="${isNew ? "" : esc(known)}" placeholder="${esc(newLabel)}" aria-label="${esc(newLabel)}"${isNew ? " required" : " hidden"} />
  </div>`;
}

// Mirror the <select> into its text input: a listed value is copied in, "New…"
// empties the input and reveals it for typing.
export function syncPicker(picker) {
  const select = picker.querySelector("select");
  const input = picker.querySelector("input");
  const isNew = select.value === PICKER_NEW;
  input.hidden = !isNew;
  input.required = isNew;
  input.value = isNew ? "" : select.value;
  if (isNew) input.focus();
}

// Programmatic fill (a product-search pick): select the matching option, or
// fall through to "New…" with the value typed in when the list doesn't have it.
export function setPickerValue(picker, value) {
  const select = picker.querySelector("select");
  const input = picker.querySelector("input");
  const match = [...select.options].find((o) => o.value === value && o.value !== PICKER_NEW);
  select.value = match ? match.value : PICKER_NEW;
  input.hidden = !!match;
  input.required = !match;
  input.value = value;
}

// String-building renderers can't attach listeners, so switches render as
// canonical vendored markup (switchEl → outerHTML) and one delegated click
// handler on .app flips them via setSwitch — the single write path.
export function switchMarkup(on, label, attrs = {}) {
  const btn = switchEl(on, { label });
  for (const [key, value] of Object.entries(attrs)) btn.setAttribute(key, value);
  return btn.outerHTML;
}

export function switchOn(selector) {
  return document.querySelector(selector)?.getAttribute("aria-checked") === "true";
}

export function formatElapsed(seconds) {
  return seconds < 60 ? `${seconds}s` : `${Math.floor(seconds / 60)}m ${String(seconds % 60).padStart(2, "0")}s`;
}

export function formatBytes(n) {
  if (n < 1024) return `${n} B`;
  if (n < 1024 * 1024) return `${(n / 1024).toFixed(1)} KB`;
  return `${(n / (1024 * 1024)).toFixed(2)} MB`;
}

export function fmtBuildTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return String(iso).replace("T", " ").slice(0, 16);
  const pad = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}
