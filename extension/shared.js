"use strict";
/* Helpers shared by the content script, the options page and the popup. */

const YTM_API = globalThis.browser || globalThis.chrome;

const YTM_DEFAULTS = {
  server: "http://127.0.0.1:8787",
  token: "",
  hideYtCaptions: true, // hide YouTube's own captions while ytmine's are showing
  overlay: { enabled: true, pinyin: false, english: true, colors: false },
  // YouTube uses Space, K, J, L, F, M, C, T, I, arrows, digits and a few punctuation keys.
  // These letters are free on YouTube. Each can also be a combination such as "alt+q".
  keys: { mine: "q", replay: "r", prev: "a", next: "d", extend: "e" },
};

const YTM_KEY_LABELS = {
  mine: "Mine the current line",
  replay: "Replay the current line",
  prev: "Previous line",
  next: "Next line",
  extend: "Add the next line to the card",
};

async function ytmGetSettings() {
  const stored = await YTM_API.storage.local.get(null);
  return {
    ...YTM_DEFAULTS,
    ...stored,
    overlay: { ...YTM_DEFAULTS.overlay, ...(stored.overlay || {}) },
    keys: { ...YTM_DEFAULTS.keys, ...(stored.keys || {}) },
  };
}

// ---- hotkeys

/** "alt+shift+q" -> {alt, shift, ctrl, meta, key} */
function ytmParseCombo(combo) {
  const parts = String(combo || "").toLowerCase().split("+").map((p) => p.trim()).filter(Boolean);
  const out = { alt: false, shift: false, ctrl: false, meta: false, key: "" };
  for (const part of parts) {
    if (part in out && part !== "key") out[part] = true;
    else out.key = part;
  }
  return out;
}

function ytmMatchKey(event, combo) {
  const want = ytmParseCombo(combo);
  if (!want.key) return false;
  const key = event.key === " " ? "space" : event.key.toLowerCase();
  return (
    key === want.key &&
    event.altKey === want.alt && event.shiftKey === want.shift &&
    event.ctrlKey === want.ctrl && event.metaKey === want.meta
  );
}

function ytmComboFromEvent(event) {
  if (["Shift", "Control", "Alt", "Meta"].includes(event.key)) return "";
  const key = event.key === " " ? "space" : event.key.toLowerCase();
  return [event.ctrlKey && "ctrl", event.altKey && "alt", event.shiftKey && "shift", event.metaKey && "meta", key]
    .filter(Boolean).join("+");
}

/** True when the key press is going into a text field, where hotkeys must stay out of the way. */
function ytmIsTyping(event) {
  const el = event.target;
  if (!el || !el.tagName) return false;
  const tag = el.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || el.isContentEditable;
}

// ---- talking to the server (always through the background service worker)

class YtmApiError extends Error {
  constructor(message, status, data) {
    super(message);
    this.status = status;
    this.data = data;
  }
}

/** Ask the background worker to call the ytmine server. Resolves to {ok, status, data, ...}. */
async function ytmRequest(path, { method = "GET", body, binary = false } = {}) {
  try {
    const reply = await YTM_API.runtime.sendMessage({ type: "api", path, method, body, binary });
    if (!reply) throw new Error("no reply");
    return reply;
  } catch (e) {
    return { ok: false, status: 0, error: "reloaded" };
  }
}

/** The message to show when the server could not be reached or refused us, or "" if it answered. */
function ytmProblem(reply) {
  if (reply.error === "reloaded") return "The ytmine extension was reloaded. Refresh this page.";
  if (reply.error === "no-token") return "Not connected yet. Open the ytmine page once (it opens when you start ytmine) and the extension connects by itself.";
  if (reply.error === "bad-server") return "The server address in the ytmine extension options must be on this computer (127.0.0.1 or localhost).";
  if (reply.error === "unreachable") return "Start ytmine on your computer, then try again. (Run `ytmine --browser-start on` once and the extension starts it for you.)";
  if (reply.status === 403) return "ytmine has a new pairing token. Open the ytmine page once and the extension reconnects by itself.";
  return "";
}

/** The saved server address if it is on this computer, else the default one. */
function ytmLocalServer(server) {
  try {
    const url = new URL(server);
    if (url.protocol === "http:" && ["127.0.0.1", "localhost"].includes(url.hostname)) return url.origin;
  } catch (e) { /* fall through */ }
  return YTM_DEFAULTS.server;
}

/** True when the fix is to open the ytmine page so the extension can pair itself. */
function ytmNeedsPairing(reply) {
  return reply.error === "no-token" || (reply.status === 403 && reply.error === undefined);
}

/** Like ytmRequest, but resolves to the JSON data and throws a readable error on any failure. */
async function ytmJson(path, options) {
  const reply = await ytmRequest(path, options);
  if (reply.ok) return reply.data;
  const problem = ytmProblem(reply);
  const message = problem || (reply.data && reply.data.error) || `Request failed (${reply.status})`;
  const hint = !problem && reply.data && reply.data.hint ? `\n${reply.data.hint}` : "";
  throw new YtmApiError(message + hint, reply.status, reply.data);
}

function ytmBlobFromReply(reply) {
  const bytes = Uint8Array.from(atob(reply.base64), (c) => c.charCodeAt(0));
  return new Blob([bytes], { type: reply.contentType || "application/octet-stream" });
}
