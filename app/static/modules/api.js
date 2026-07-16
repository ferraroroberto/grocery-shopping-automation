// The HTTP seam: bearer-token fetch, the mandatory login handshake, and the
// two calls that refresh app-wide state (inventory payload, build identity).
import { emptyStateEl } from "../_vendored/empty-state/empty-state.js";
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
import { fmtBuildTime } from "./dom.js";

export function authFetch(input, init = {}) {
  const token = storedToken();
  const options = { ...init };
  const headers = new Headers(options.headers || {});
  if (token && !headers.has("Authorization")) headers.set("Authorization", `Bearer ${token}`);
  options.headers = headers;
  return fetch(input, options);
}

export async function fetchJson(url, init = {}) {
  const response = await authFetch(url, init);
  if (response.status === 401) {
    await promptForPassword();
    return fetchJson(url, init);
  }
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.detail || `HTTP ${response.status}`);
  return body;
}

// Concurrent 401s (e.g. the parallel inventory + access fetches) must share
// one login prompt: every caller gets the same pending promise, resolved once
// on a successful unlock. The dialog is mandatory — Esc/cancel is blocked and
// there is no × close; the fleet nav hides itself while it is open.
let loginPending = null;
let loginResolve = null;

function promptForPassword() {
  if (loginPending) return loginPending;
  loginPending = new Promise((resolve) => {
    loginResolve = resolve;
  });
  el.loginPassword.value = "";
  el.loginError.textContent = "";
  el.loginDialog.showModal();
  window.setTimeout(() => el.loginPassword.focus(), 60);
  return loginPending;
}

export async function onLoginSubmit(event) {
  event.preventDefault();
  const password = el.loginPassword.value;
  if (!password) return;
  const button = el.loginForm.querySelector("button[type='submit']");
  button.disabled = true;
  el.loginError.textContent = "";
  try {
    const response = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password }),
    });
    if (response.status === 401) {
      el.loginError.textContent = "Wrong password";
      el.loginPassword.select();
      return;
    }
    const body = await response.json().catch(() => ({}));
    if (!response.ok) {
      el.loginError.textContent = body.detail || `Login failed: ${response.status}`;
      return;
    }
    localStorage.setItem(TOKEN_KEY, body.token);
    el.loginDialog.close();
    const resolve = loginResolve;
    loginPending = null;
    loginResolve = null;
    resolve?.(true);
  } finally {
    button.disabled = false;
  }
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
  state.payload = await fetchJson(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  render();
  setStatus("Saved");
}

// --------------------------------------------------- build identity
// Ported from home-automation main.js — visible proof of which build the PWA
// is running (footer "Build: <sha> · <time>" line), plus a one-shot reload
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
    const ts = fmtBuildTime(body.built_at || "");
    el.buildReadout.textContent = ts ? `Build: ${sha} · ${ts}` : `Build: ${sha}`;
  } catch (_) {
    el.buildReadout.textContent = "";
  }
}
