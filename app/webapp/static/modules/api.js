// The HTTP seam: bearer-token fetch, the mandatory login handshake, and the
// two calls that refresh app-wide state (inventory payload, build identity).
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
import { requestSignIn } from "../_vendored/sign-in/sign-in.js";
import { showToast } from "../_vendored/toast/toast.js";
import {
  activePaneBody,
  defaultZone,
  el,
  render,
  setStatus,
  state,
  storedToken,
  TOKEN_KEY,
} from "./core.js";
import { buildReadoutText } from "../_vendored/page-foot/page-foot.js";

// A JSON request init; `extra` carries fetch options such as an abort `signal`.
export function jsonInit(method, body, extra = {}) {
  return { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), ...extra };
}

export function authFetch(input, init = {}) {
  const token = storedToken();
  const options = { ...init };
  const headers = new Headers(options.headers || {});
  if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
  options.headers = headers;
  return fetch(input, options);
}

// authFetch, but a 401 raises the login prompt and retries once unlocked.
async function fetchUnlocked(url, init = {}) {
  const response = await authFetch(url, init);
  if (response.status !== 401) return response;
  await promptForPassword();
  return fetchUnlocked(url, init);
}

export async function fetchJson(url, init = {}) {
  const response = await fetchUnlocked(url, init);
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(body.detail || `HTTP ${response.status}`);
    if (response.headers.get("X-Inventory-Stale")) onStaleList(error);
    throw error;
  }
  return body;
}

// An item route that names the item it means. Ids are row positions,
// so the server checks the name and refuses (409, X-Inventory-Stale) once
// another device's delete has moved the list under this one.
export function itemUrl(id, name, path = "") {
  const sep = path.includes("?") ? "&" : "?";
  return `/api/items/${id}${path}${sep}name=${encodeURIComponent(name ?? "")}`;
}

// Every open sheet was built from the stale list: close them, say why, and
// reload so the next tap targets the right row. `stale` lets a caller skip
// its own error toast.
function onStaleList(error) {
  error.stale = true;
  document.querySelectorAll("dialog[open]").forEach((dialog) => dialog.close());
  showToast(error.message, "error");
  loadInventory();
}

// A file download: the bearer header a plain navigation can't carry.
export async function fetchBlob(url, init = {}) {
  const response = await fetchUnlocked(url, init);
  if (!response.ok) {
    const body = await response.json().catch(() => ({}));
    throw new Error(body.detail || `HTTP ${response.status}`);
  }
  return response.blob();
}

// Concurrent 401s (e.g. the parallel inventory + access fetches) share the
// vendored sign-in sheet's one pending promise; the token key is the app's own.
function promptForPassword() {
  return requestSignIn({
    endpoint: "/api/login",
    hint: "Enter the password to use Grocery.",
    onToken: (token) => localStorage.setItem(TOKEN_KEY, token),
  });
}

export async function loadInventory() {
  setStatus("Loading inventory...");
  try {
    state.payload = await fetchJson("/api/inventory", { headers: { Accept: "application/json" } });
    state.access = await fetchJson("/api/access").catch(() => null);
    if (!state.zone) state.zone = defaultZone();
    render();
  } catch (error) {
    setStatus(error.message);
    activePaneBody()?.replaceChildren(emptyStateEl("circle-alert", "Inventory unavailable."));
  }
}

export async function mutate(url, payload, method = "POST") {
  setStatus("Saving...");
  try {
    state.payload = await fetchJson(url, jsonInit(method, payload));
  } catch (error) {
    // The server's detail (a 423 "spreadsheet open in Excel" is the common one);
    // callers are bare click handlers, so a rethrow would only go unhandled.
    setStatus(error.message);
    if (!error.stale) showToast(error.message, "error");
    return;
  }
  render();
  showToast("Saved");
}

// --------------------------------------------------- build identity
// Ported from home-automation main.js — visible proof of which build the PWA
// is running (the page-foot line), plus a one-shot reload
// when the served asset hash changed since the last visit (iOS standalone
// PWAs can cling to an old shell even with stamped asset URLs).
const ASSET_HASH_KEY = "grocery.assetHash";
const ASSET_RELOAD_KEY = "grocery.assetReloadedFor";

export async function fetchVersion() {
  try {
    const body = await fetchJson("/api/version");
    const sha = body.git_sha || "unknown";
    const assetHash = body.asset_hash || "";
    const previousHash = localStorage.getItem(ASSET_HASH_KEY) || "";
    if (
      assetHash && previousHash && previousHash !== assetHash &&
      sessionStorage.getItem(ASSET_RELOAD_KEY) !== assetHash
    ) {
      localStorage.setItem(ASSET_HASH_KEY, assetHash);
      sessionStorage.setItem(ASSET_RELOAD_KEY, assetHash);
      window.location.reload();
      return;
    }
    if (assetHash) localStorage.setItem(ASSET_HASH_KEY, assetHash);
    el.buildReadout.textContent = buildReadoutText(sha, body.built_at || "");
  } catch (_) {
    el.buildReadout.textContent = "";
  }
}
