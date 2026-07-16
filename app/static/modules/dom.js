// Pure presentation helpers: escaping, formatting, and the small markup
// fragments every view builds strings out of. Nothing here reads app state,
// so this module sits at the bottom of the graph and imports only vendored
// components.
import { switchEl } from "../_vendored/switch/switch.js";

export function text(value) {
  return value === null || value === undefined || value === "" ? "-" : String(value);
}

export function html(value) {
  return text(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

// Colour-coded current/target, mirroring app/ui_helpers.qty_html:
// green when stocked (current ≥ target), amber when low, red when empty.
export function qtyMarkup(current, target) {
  const cur = Number(current) || 0;
  const tgt = Number(target) || 0;
  const cls = cur >= tgt ? "qty-ok" : (cur > 0 ? "qty-low" : "qty-zero");
  return `<span class="${cls}">${html(current)}</span><span class="meta">/${html(target)}</span>`;
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
