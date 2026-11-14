"use strict";
/* The only place that talks to the ytmine server.
 *
 * The server refuses requests from web pages, youtube.com included, so the content script
 * cannot call it: a request from there would carry youtube.com as its origin. This worker's
 * requests carry the extension's own origin, and the pairing token (set once in the options
 * page) in a header. The server accepts that combination and nothing else.
 */

const api = globalThis.browser || globalThis.chrome;
const TOKEN_HEADER = "X-Ytmine-Token";
const LOCAL_HOSTS = new Set(["127.0.0.1", "localhost", "[::1]"]);
const DEFAULT_SERVER = "http://127.0.0.1:8787";

/** The server URL from the options, or null if it is not on this computer. */
function serverBase(stored) {
  let url;
  try { url = new URL(stored || DEFAULT_SERVER); } catch (e) { return null; }
  if (url.protocol !== "http:" || !LOCAL_HOSTS.has(url.hostname)) return null;
  return url.origin;
}

function toBase64(buffer) {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) binary += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(binary);
}

async function callServer({ path, method = "GET", body, binary = false }) {
  // Only ytmine's own API. A page cannot use this worker to fetch anything else.
  if (typeof path !== "string" || !path.startsWith("/api/") || path.includes("..")) {
    return { ok: false, status: 0, error: "bad-path" };
  }
  const { server, token } = await api.storage.local.get({ server: DEFAULT_SERVER, token: "" });
  const base = serverBase(server);
  if (!base) return { ok: false, status: 0, error: "bad-server" }; // never send the token elsewhere
  if (!token) return { ok: false, status: 0, error: "no-token" };

  const headers = { [TOKEN_HEADER]: token };
  const init = { method, headers };
  if (body !== undefined) {
    headers["Content-Type"] = "application/json";
    init.body = JSON.stringify(body);
  }
  let response;
  try {
    response = await fetch(base + path, init);
  } catch (e) {
    return { ok: false, status: 0, error: "unreachable" };
  }
  if (binary && response.ok) {
    return {
      ok: true,
      status: response.status,
      base64: toBase64(await response.arrayBuffer()),
      contentType: response.headers.get("Content-Type") || "",
      filename: /filename="?([^";]+)/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "",
    };
  }
  const data = await response.json().catch(() => ({}));
  return { ok: response.ok, status: response.status, data };
}

/** Save the token that pair.js read from ytmine's own page, after checking it works there. */
async function pair(message, sender) {
  // The address comes from the tab the message was sent from, never from the message itself.
  let origin;
  try { origin = new URL(sender.url).origin; } catch (e) { return { ok: false }; }
  const base = serverBase(origin);
  const token = message.token;
  if (!base || typeof token !== "string" || token.length < 20) return { ok: false };
  try {
    const response = await fetch(base + "/api/info", { headers: { [TOKEN_HEADER]: token } });
    if (!response.ok) return { ok: false };
  } catch (e) {
    return { ok: false };
  }
  const saved = await api.storage.local.get({ server: DEFAULT_SERVER, token: "" });
  if (saved.server !== base || saved.token !== token) await api.storage.local.set({ server: base, token });
  return { ok: true };
}

api.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== api.runtime.id || !message) return false;
  const fail = () => sendResponse({ ok: false, status: 0, error: "unreachable" });
  if (message.type === "api") callServer(message).then(sendResponse, fail);
  else if (message.type === "pair") pair(message, sender).then(sendResponse, fail);
  else return false;
  return true; // the answer is sent asynchronously
});

// A first install opens the options page, which says how to connect.
api.runtime.onInstalled.addListener((details) => {
  // It can fail if no browser window is open yet; the toolbar popup links to the options too.
  if (details.reason === "install") Promise.resolve(api.runtime.openOptionsPage()).catch(() => {});
});
