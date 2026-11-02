// Settings: inset groups under overline headers
// (design.md "settings group") — Display, Stores, Data — every row showing its
// value. Store logins opens its sheet here; Email watch is email.js's sheet;
// the Data rows act on tap.
//
// The login check reports into its sheet, one row per store, never the
// error toast: a logged-out store is an exception (an attention chip), not a
// failure of the command (COLOR-02). The last result is kept on the device, so
// the row's value survives a reload.
import { showToast } from "../_vendored/toast/toast.js";
import { fetchBlob, fetchJson } from "./api.js";
import { storeLabel } from "./automation.js";
import { copyText } from "./clipboard.js";
import { state } from "./core.js";
import { chip, esc, icon } from "./dom.js";
import { refreshEmailWatch } from "./email.js";
import { dialogShell, setDialogStatus } from "./stores/dialog.js";

const LOGINS_ID = "store-logins-sheet";
const LOGINS_KEY = "grocery.loginCheck";

let checking = false;

// ------------------------------------------------------------ the pane
// On every Settings render: each row's value, then the email watch's status.
export function renderSettings() {
  paintLoginsValue();
  const access = state.access || {};
  setText("#copy-link-value", access.cloudflare ? "Tunnel link" : access.lan ? "Local network link" : "This page");
  const total = state.payload?.summary.total_items;
  setText("#export-csv-value", total === undefined ? "" : `${total} items`);
  refreshEmailWatch();
}

function setText(selector, value) {
  const node = document.querySelector(selector);
  if (node) node.textContent = value;
}

export function openSpreadsheet() {
  fetchJson("/api/actions/open-spreadsheet", { method: "POST" })
    .then(() => showToast("Spreadsheet opened on the computer"))
    .catch((error) => showToast(error.message, "error"));
}

export async function copyLink() {
  const url = state.access?.cloudflare || state.access?.lan || window.location.href;
  const copied = await copyText(url);
  showToast(copied ? "Link copied" : "Couldn't copy — copy the address bar instead", copied ? undefined : "error");
}

// Fetched, not navigated to: a navigation carries no bearer token, so off the PC
// it landed on a 401 page.
export async function exportCsv() {
  try {
    const url = URL.createObjectURL(await fetchBlob("/api/export.csv"));
    const link = Object.assign(document.createElement("a"), { href: url, download: "inventory.csv" });
    link.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000); // after the browser has taken the file
  } catch (error) {
    showToast(error.message, "error");
  }
}

// ------------------------------------------------------- store logins
let latest = null; // this session's check, ahead of the saved one

function lastCheck() {
  if (latest) return latest;
  try {
    return JSON.parse(localStorage.getItem(LOGINS_KEY) || "null");
  } catch (_) {
    return null;
  }
}

function loggedOut(check) {
  return Object.values(check?.stores || {}).filter((r) => r.state === "logged_out").length;
}

function paintLoginsValue() {
  const node = document.querySelector("#store-logins-value");
  if (!node) return;
  const check = lastCheck();
  const out = loggedOut(check);
  node.textContent = checking ? "Checking…" : !check ? "Not checked" : out ? `${out} logged out` : "All logged in";
  if (out && !checking) node.dataset.tone = "attention";
  else delete node.dataset.tone;
}

function loginsSheet() {
  return dialogShell(LOGINS_ID, "Store logins", "Done", (shell) => {
    shell.classList.add("store-logins-sheet");
    const done = shell.querySelector(".detail-save-btn");
    done.disabled = false;
    done.addEventListener("click", () => shell.close());
    shell.addEventListener("click", (event) => {
      const id = event.target.closest("button")?.id;
      if (id === "check-logins") checkLogins();
      if (id === "bootstrap-session") bootstrapSession();
    });
    shell.addEventListener("close", () => document.querySelector("#open-store-logins")?.focus());
  });
}

export function openStoreLogins() {
  const d = loginsSheet();
  if (!checking) setDialogStatus(d, ""); // a check in flight keeps its line
  paintLogins();
  if (!d.open) d.showModal();
}

// A store's state: logged in is the normal case (plain text); logged out needs
// you (attention); unknown is a plain fact (neutral). The check's own detail
// stays in its log.
function loginRow([store, result]) {
  const trail = result.state === "logged_in"
    ? `<span class="action-row-trail action-row-value login-ok">Logged in</span>`
    : `<span class="action-row-trail">${result.state === "logged_out" ? chip("Logged out", "attention") : chip("Unknown", "neutral")}</span>`;
  return `<li class="action-row">
      <div class="action-row-main"><span class="action-row-title">${esc(storeLabel(store))}</span></div>
      ${trail}
    </li>`;
}

function paintLogins() {
  const d = document.getElementById(LOGINS_ID);
  if (!d) return;
  const check = lastCheck();
  const when = check ? new Date(check.at).toLocaleString([], { dateStyle: "medium", timeStyle: "short" }) : "";
  d.querySelector(".stores-dialog-body").innerHTML = `
    <section class="sheet-section" aria-labelledby="logins-last">
      <h3 class="overline" id="logins-last">Last check</h3>
      ${check
        ? `<p class="meta">${esc(when)}</p><ul class="action-rows login-rows">${Object.entries(check.stores).map(loginRow).join("")}</ul>`
        : `<p class="meta">Not checked on this device yet.</p>`}
    </section>
    <div class="logins-actions">
      <button id="check-logins" class="button-tint btn-block" type="button"${checking ? " disabled" : ""}>${icon("circle-check")}Check logins</button>
      <button id="bootstrap-session" class="button-surface btn-block" type="button">${icon("external-link")}Log in to stores</button>
    </div>`;
}

// Read-only: a Chrome window opens briefly, no cart is touched.
function checkLogins() {
  const d = loginsSheet();
  checking = true;
  paintLogins();
  paintLoginsValue();
  setDialogStatus(d, "Checking store logins — a Chrome window opens briefly…", "");
  fetchJson("/api/actions/check-logins", { method: "POST" })
    .then(({ stores }) => {
      latest = { at: new Date().toISOString(), stores };
      try {
        localStorage.setItem(LOGINS_KEY, JSON.stringify(latest));
      } catch (_) {
        // private mode — the result lasts this session only
      }
      setDialogStatus(d, "");
      const out = loggedOut({ stores });
      showToast(out ? `${out} ${out === 1 ? "store" : "stores"} logged out` : "Every store is logged in");
    })
    .catch((error) => setDialogStatus(d, `Login check failed: ${error.message}`))
    .finally(() => {
      checking = false;
      paintLogins();
      paintLoginsValue();
    });
}

function bootstrapSession() {
  const d = loginsSheet();
  fetchJson("/api/actions/bootstrap-session", { method: "POST" })
    .then(() => setDialogStatus(d, "Chrome opened on the computer — log into each store, then close it completely.", ""))
    .catch((error) => setDialogStatus(d, `Could not open Chrome: ${error.message}`));
}
