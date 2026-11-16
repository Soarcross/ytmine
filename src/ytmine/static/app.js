"use strict";
/* ytmine frontend. Plain JS, no build step.
 *
 * Flow: load a URL -> server returns cleaned lines -> we render them and drive
 * the YouTube IFrame Player. A 100 ms timer finds the line for the current
 * playback time (same rule as line_index_at in subtitles.py) and highlights it.
 */

const $ = (id) => document.getElementById(id);

const LATE_WINDOW_MS = 1500; // "M" mines the previous line if the line changed this recently
const REPLAY_RECENT_MS = 8000; // "R" replays the last mined line if it was mined this recently
const LOOKAHEAD = 0.2; // seconds; highlight slightly early to hide player time jitter
const MAX_SPAN_LINES = 6; // same limit as the server (app.py)

const state = {
  lines: [],
  starts: [],
  lineEls: [],
  currentIdx: -1,
  selectedIdx: null, // line chosen by click / arrow keys; cleared when playback moves on
  span: null, // {from, to}: several lines picked with Shift+arrows, mined as one card
  willMineKey: "",
  videoId: null,
  statusBase: "",
  useLocal: false, // true when YouTube refuses to embed and we play our own audio copy
  lastChangeAt: -Infinity,
  seekGuardUntil: 0,
  autoScroll: true,
  target: null, // {idx, ti, text}: word picked as the cloze target
  lastMined: null, // {idx, at}
  cards: [],
  editingId: null,
  sessionAll: false,
  player: null,
  playerReady: false,
  pendingVideoId: null,
  highlighted: null,
};

// ---------------------------------------------------------------- helpers

function toast(message, isError = false) {
  // In fullscreen only #stage is visible, so the message has to be drawn inside it.
  if (document.fullscreenElement || document.webkitFullscreenElement) {
    overlay.toast(message, isError);
    return;
  }
  const el = $("toast");
  el.textContent = message;
  el.className = "show" + (isError ? " error" : "");
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => (el.className = ""), isError ? 6000 : 2500);
}

async function api(path, options = {}) {
  const init = { ...options };
  if (init.body && typeof init.body !== "string") {
    init.body = JSON.stringify(init.body);
    init.headers = { "Content-Type": "application/json", ...(init.headers || {}) };
  }
  let response;
  try {
    response = await fetch(path, init);
  } catch (e) {
    throw Object.assign(new Error("Could not reach the ytmine server. Is it still running?"), { status: 0 });
  }
  const data = await response.json().catch(() => ({}));
  if (!response.ok) {
    const message = (data.error || `Request failed (${response.status})`) + (data.hint ? `\n${data.hint}` : "");
    throw Object.assign(new Error(message), { status: response.status, data });
  }
  return data;
}

function formatTime(seconds) {
  const s = Math.floor(seconds);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

function store(key, value) {
  try { localStorage.setItem(key, value); } catch (e) { /* storage may be blocked */ }
}
function recall(key) {
  try { return localStorage.getItem(key); } catch (e) { return null; }
}

// ---------------------------------------------------------------- YouTube player

function loadYouTubeApi() {
  if (window.YT && window.YT.Player) return;
  if (document.getElementById("yt-api")) return;
  const tag = document.createElement("script");
  tag.id = "yt-api";
  tag.src = "https://www.youtube.com/iframe_api";
  document.head.appendChild(tag);
}

window.onYouTubeIframeAPIReady = function () {
  if (state.pendingVideoId) showVideo(state.pendingVideoId);
};

function showVideo(videoId) {
  state.pendingVideoId = videoId;
  setLocalMode(false);
  if (!(window.YT && window.YT.Player)) {
    loadYouTubeApi();
    return;
  }
  if (state.player) {
    state.player.loadVideoById(videoId);
    return;
  }
  state.player = new YT.Player("player", {
    host: "https://www.youtube-nocookie.com",
    videoId,
    // fs: 0 hides YouTube's own fullscreen button. It would fullscreen only the iframe and
    // hide our caption overlay, so the page has its own button (and the F key) for #stage.
    playerVars: { rel: 0, playsinline: 1, iv_load_policy: 3, fs: 0, origin: location.origin },
    events: {
      onReady: () => (state.playerReady = true),
      onError: (e) => { if (EMBED_BLOCKED.includes(e.data)) setLocalMode(true); },
    },
  });
}

// YouTube player error codes meaning "the owner does not allow embedding here".
const EMBED_BLOCKED = [101, 150, 153];

// Same few methods as the YouTube player, backed by the audio ytmine downloaded.
const localPlayer = {
  el: $("local-audio"),
  ready: false,
  seekTo(s) { this.el.currentTime = s; },
  playVideo() { this.el.play().catch(() => {}); },
  pauseVideo() { this.el.pause(); },
  getPlayerState() { return this.el.paused ? 2 : 1; },
  getCurrentTime() { return this.el.currentTime; },
};
localPlayer.el.addEventListener("loadedmetadata", () => (localPlayer.ready = true));

function setLocalMode(on) {
  state.useLocal = on;
  $("local-player").hidden = !on;
  localPlayer.pauseVideo();
  if (!on) { localPlayer.ready = false; localPlayer.el.removeAttribute("src"); return; }
  $("local-note").textContent = "The owner of this video does not allow it to play outside YouTube. " +
    "ytmine is playing its downloaded audio instead; transcript, hotkeys and mining work as usual.";
  loadLocalAudio();
}

function loadLocalAudio() {
  if (!state.useLocal || localPlayer.el.getAttribute("src")) return;
  if (state.audioState !== "ready") return; // pollAudio() calls us again once the download is done
  localPlayer.el.src = `/api/audio?v=${encodeURIComponent(state.videoId)}`;
}

function activePlayer() {
  if (state.useLocal) return localPlayer.ready ? localPlayer : null;
  return state.playerReady ? state.player : null;
}

function seekTo(seconds, play) {
  const player = activePlayer();
  if (!player) return;
  player.seekTo(Math.max(0, seconds), true);
  if (play) player.playVideo();
}

function togglePlay() {
  const player = activePlayer();
  if (!player) return;
  const playing = player.getPlayerState() === 1; // YT.PlayerState.PLAYING
  overlay.noteUserToggle(); // a hover pause must not undo what the user just chose
  if (playing) player.pauseVideo();
  else player.playVideo();
}

// ---------------------------------------------------------------- fullscreen

// Fullscreen the wrapper that holds both the player and our overlay. The YouTube iframe's own
// fullscreen button would show only the video, and the captions would disappear.
function toggleFullscreen() {
  const stage = $("stage");
  const current = document.fullscreenElement || document.webkitFullscreenElement;
  if (current) {
    (document.exitFullscreen || document.webkitExitFullscreen).call(document);
  } else {
    const request = stage.requestFullscreen || stage.webkitRequestFullscreen;
    if (!request) { toast("This browser does not allow fullscreen here.", true); return; }
    // The request is refused without a recent click or key press; say so instead of failing silently.
    Promise.resolve(request.call(stage)).catch(() =>
      toast("The browser refused fullscreen. Click the page and try again.", true));
  }
}
for (const type of ["fullscreenchange", "webkitfullscreenchange"]) {
  document.addEventListener(type, () => {
    window.focus(); // keep the hotkeys working after entering or leaving fullscreen
    overlay.refresh();
  });
}

// Keys pressed after clicking inside the video go to the iframe, not to us.
// Taking focus back keeps the hotkeys working.
window.addEventListener("blur", () => {
  setTimeout(() => {
    const active = document.activeElement;
    if (active && active.tagName === "IFRAME") {
      active.blur();
      window.focus();
    }
  }, 0);
});

// ---------------------------------------------------------------- transcript

function indexAt(t) {
  // Last line whose start is <= t (binary search). A line stays current until the next starts.
  let lo = 0, hi = state.starts.length - 1, found = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    if (state.starts[mid] <= t + LOOKAHEAD) { found = mid; lo = mid + 1; } else hi = mid - 1;
  }
  return found;
}

function renderTranscript() {
  const box = $("transcript");
  box.textContent = "";
  state.lineEls = state.lines.map((line) => {
    const el = document.createElement("div");
    el.className = "line" + (line.mined ? " mined" : "");
    el.dataset.idx = line.index;

    const time = document.createElement("div");
    time.className = "time";
    time.textContent = formatTime(line.start);

    const body = document.createElement("div");
    const zh = document.createElement("div");
    zh.className = "zh";
    line.tokens.forEach(([text, reading, isWord], ti) => {
      if (!isWord) { zh.appendChild(document.createTextNode(text)); return; }
      const word = document.createElement("span");
      word.className = "w";
      word.dataset.ti = ti;
      if (reading) {
        const ruby = document.createElement("ruby");
        ruby.appendChild(document.createTextNode(text));
        const rt = document.createElement("rt");
        rt.textContent = reading;
        ruby.appendChild(rt);
        word.appendChild(ruby);
      } else {
        word.textContent = text;
      }
      zh.appendChild(word);
    });
    body.appendChild(zh);
    if (line.english) {
      const en = document.createElement("div");
      en.className = "en";
      en.textContent = line.english;
      body.appendChild(en);
    }
    el.append(time, body);
    box.appendChild(el);
    return el;
  });
  state.highlighted = null;
  updateHighlight();
}

function updateHighlight() {
  if (state.highlighted) state.highlighted.classList.remove("current");
  const el = state.lineEls[state.currentIdx] || null;
  if (el) el.classList.add("current");
  state.highlighted = el;
}

function updateWillMine() {
  const range = rangeToMine();
  const key = range ? `${range.from}-${range.to}` : "";
  if (key === state.willMineKey) return; // runs every 100 ms; only touch the DOM on change
  state.willMineKey = key;
  document.querySelectorAll(".line.will-mine").forEach((el) => el.classList.remove("will-mine", "span"));
  if (!range) return;
  for (let i = range.from; i <= range.to; i++) {
    state.lineEls[i]?.classList.add("will-mine");
    if (range.to > range.from) state.lineEls[i]?.classList.add("span");
  }
}

function scrollToLine(idx) {
  const el = state.lineEls[idx];
  const box = $("transcript");
  if (!el) return;
  box.scrollTo({ top: el.offsetTop - box.clientHeight / 2 + el.clientHeight / 2, behavior: "smooth" });
}

function setAutoScroll(on) {
  state.autoScroll = on;
  $("resume-scroll").hidden = on;
  if (on) scrollToLine(state.currentIdx);
}

// A manual scroll (wheel, touch, or dragging the scrollbar) pauses auto-scroll.
const transcriptBox = $("transcript");
transcriptBox.addEventListener("wheel", () => setAutoScroll(false), { passive: true });
transcriptBox.addEventListener("touchmove", () => setAutoScroll(false), { passive: true });
transcriptBox.addEventListener("pointerdown", (e) => { if (e.target === transcriptBox) setAutoScroll(false); });
$("resume-scroll").addEventListener("click", () => setAutoScroll(true));

function tick() {
  const player = activePlayer();
  if (!player || !state.lines.length) return;
  let t;
  try { t = player.getCurrentTime(); } catch (e) { return; }
  const idx = indexAt(t);
  if (performance.now() >= state.seekGuardUntil && idx !== state.currentIdx) {
    state.currentIdx = idx;
    state.lastChangeAt = performance.now();
    if (state.selectedIdx !== null && state.selectedIdx !== idx) state.selectedIdx = null;
    updateHighlight();
    if (state.autoScroll) scrollToLine(idx);
  }
  updateWillMine();
  overlay.update();
}
setInterval(tick, 100);

// ---------------------------------------------------------------- navigation

function jumpTo(idx, play = true) {
  if (idx < 0 || idx >= state.lines.length) return;
  seekTo(state.lines[idx].start, play);
  selectLine(idx);
  state.seekGuardUntil = performance.now() + 500; // ignore stale player time right after a seek
}

function selectLine(idx) {
  state.currentIdx = idx;
  state.selectedIdx = idx;
  state.span = null;
  state.lastChangeAt = -Infinity; // an explicit choice is never "late"
  updateHighlight();
  if (state.autoScroll) scrollToLine(idx);
}

function stepLine(delta) {
  const base = state.selectedIdx ?? state.currentIdx;
  jumpTo(Math.min(Math.max(base + delta, 0), state.lines.length - 1));
}

// Shift+Right adds the next line to the card, Shift+Left adds the previous one
// (or takes back the last line added). Captions often split one sentence over
// several lines; this lets one card hold the whole sentence and its audio.
function extendSpan(delta, baseIdx) {
  if (!state.lines.length) return;
  let span = state.span;
  if (!span) {
    const base = baseIdx ?? lineToMine();
    if (base < 0) return;
    span = { from: base, to: base };
  }
  const last = state.lines.length - 1;
  if (delta > 0) span = { ...span, to: Math.min(span.to + 1, last) };
  else if (span.to > span.from) span = { ...span, to: span.to - 1 };
  else span = { ...span, from: Math.max(span.from - 1, 0) };
  if (span.to - span.from + 1 > MAX_SPAN_LINES) { toast(`A card can cover at most ${MAX_SPAN_LINES} lines.`); return; }
  state.span = span;
  state.selectedIdx = null;
  updateWillMine();
}

function spanTo(idx) {
  const base = state.span ? state.span.from : lineToMine();
  if (base < 0) return;
  const from = Math.min(base, idx), to = Math.max(base, idx);
  if (to - from + 1 > MAX_SPAN_LINES) { toast(`A card can cover at most ${MAX_SPAN_LINES} lines.`); return; }
  state.span = { from, to };
  state.selectedIdx = null;
  updateWillMine();
}

$("transcript").addEventListener("click", (event) => {
  const lineEl = event.target.closest(".line");
  if (!lineEl) return;
  const idx = Number(lineEl.dataset.idx);
  if (event.shiftKey) { spanTo(idx); return; } // Shift+click: card from the chosen line to this one
  const wordEl = event.target.closest(".w");
  if (wordEl) {
    toggleTarget(idx, Number(wordEl.dataset.ti), wordEl);
    if (idx !== state.currentIdx) jumpTo(idx); else selectLine(idx);
  } else {
    jumpTo(idx);
  }
});

function toggleTarget(idx, ti, wordEl) {
  const same = state.target && state.target.idx === idx && state.target.ti === ti;
  clearTarget();
  if (same) return;
  const token = state.lines[idx].tokens[ti];
  state.target = { idx, ti, text: token[0] };
  wordEl.classList.add("target-word");
}

function clearTarget() {
  document.querySelectorAll(".w.target-word").forEach((el) => el.classList.remove("target-word"));
  state.target = null;
}

// ---------------------------------------------------------------- mining

function lineToMine() {
  if (state.selectedIdx !== null) return state.selectedIdx;
  if (state.currentIdx < 0) return -1;
  // We usually press M a beat late: if the line only just changed, mine the one before it.
  const recentlyChanged = performance.now() - state.lastChangeAt < LATE_WINDOW_MS;
  return recentlyChanged && state.currentIdx > 0 ? state.currentIdx - 1 : state.currentIdx;
}

function rangeToMine() {
  if (state.span) return state.span;
  const idx = lineToMine();
  return idx < 0 ? null : { from: idx, to: idx };
}

function markMined(range, on = true) {
  for (let i = range.from; i <= range.to; i++) state.lineEls[i]?.classList.toggle("mined", on);
}

// Mine lines range.from..range.to. `target` picks the word the card is about:
// {line, text, index} for a word picked in the transcript (index = token number), or
// {line, text, offset} for a word or expression picked on the video captions.
// Resolves to the card, or null if the sentence was already mined; throws on other errors.
async function mineRange(range, target) {
  const body = { line_index: range.from, line_end: range.to };
  if (target && target.line >= range.from && target.line <= range.to) {
    body.target_line = target.line;
    body.target_text = target.text;
    if (target.index !== undefined) body.target_index = target.index;
    else body.target_offset = target.offset;
  }
  let card;
  try {
    card = await api("/api/mine", { method: "POST", body });
  } catch (e) {
    if (e.status === 409) { markMined(range); return null; }
    throw e;
  }
  state.cards.unshift(card);
  renderCards();
  markMined(range);
  state.lastMined = { range, at: Date.now() };
  state.span = null;
  clearTarget();
  refreshVocab(); // the word is now "mined" on the captions
  return card;
}

async function mine() {
  const range = rangeToMine();
  if (!range) { toast("Nothing to mine yet. Start the video, or click a line."); return; }
  const t = state.target;
  toast("Mining…");
  try {
    const card = await mineRange(range, t ? { line: t.idx, text: t.text, index: t.ti } : null);
    toast(card ? `Mined: ${card.sentence}` : "Already mined.");
  } catch (e) {
    toast(e.message, true);
  }
}

function replay() {
  const recent = state.lastMined && Date.now() - state.lastMined.at < REPLAY_RECENT_MS;
  const range = recent ? state.lastMined.range : rangeToMine();
  if (!range) return;
  const keep = state.span; // replaying a picked span should not drop the selection
  jumpTo(range.from, true);
  if (keep) { state.span = keep; state.selectedIdx = null; }
}

window.addEventListener("keydown", (event) => {
  const tag = event.target.tagName;
  if (tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || event.target.isContentEditable) return;
  if (event.ctrlKey || event.metaKey || event.altKey) return;
  const shift = event.shiftKey;
  const handlers = {
    m: () => { if (!event.repeat) mine(); },
    r: replay,
    f: toggleFullscreen,
    " ": togglePlay,
    ArrowLeft: () => (shift ? extendSpan(-1) : stepLine(-1)),
    ArrowRight: () => (shift ? extendSpan(1) : stepLine(1)),
    Escape: () => { state.span = null; clearTarget(); updateWillMine(); },
  };
  const handler = handlers[event.key.length === 1 ? event.key.toLowerCase() : event.key];
  if (handler) { event.preventDefault(); handler(); }
});

// ---------------------------------------------------------------- cards panel

function renderCards() {
  const list = $("cards");
  list.textContent = "";
  $("card-count").textContent = state.cards.length;
  for (const card of state.cards) {
    const li = document.createElement("li");
    li.appendChild(card.id === state.editingId ? editForm(card) : cardView(card));
    list.appendChild(li);
  }
}

function cardView(card) {
  const box = document.createElement("div");
  const zh = document.createElement("div");
  zh.className = "c-sentence";
  if (card.parts) {
    const mark = document.createElement("mark");
    mark.textContent = card.parts.target;
    zh.append(card.parts.before, mark, card.parts.after);
  } else {
    zh.textContent = card.sentence;
  }
  const pinyin = document.createElement("div");
  pinyin.className = "c-pinyin";
  pinyin.textContent = card.pinyin;
  const en = document.createElement("div");
  en.className = "c-english";
  en.textContent = card.english;

  const buttons = document.createElement("div");
  buttons.className = "c-buttons";
  buttons.append(
    button("▶", () => new Audio(card.audio_url).play().catch(() => toast("Could not play the clip.", true)), !card.audio_url, "Play clip"),
    button("Edit", () => { state.editingId = card.id; renderCards(); }),
    button("Delete", () => deleteCard(card)),
  );
  box.append(zh, pinyin, en, buttons);
  return box;
}

function button(label, onClick, disabled = false, ariaLabel = null) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  if (ariaLabel) { b.setAttribute("aria-label", ariaLabel); b.title = ariaLabel; }
  b.disabled = disabled;
  b.addEventListener("click", onClick);
  return b;
}

function editForm(card) {
  const form = document.createElement("form");
  const inputs = {};
  for (const [key, label] of [["sentence", "Sentence"], ["target", "Blanked word (optional)"], ["pinyin", "Pinyin"], ["english", "English"]]) {
    const wrap = document.createElement("label");
    wrap.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.value = card[key] || "";
    wrap.appendChild(input);
    form.appendChild(wrap);
    inputs[key] = input;
  }
  const buttons = document.createElement("div");
  buttons.className = "c-buttons";
  const save = button("Save", () => {}); save.type = "submit";
  buttons.append(save, button("Cancel", () => { state.editingId = null; renderCards(); }));
  form.appendChild(buttons);
  form.addEventListener("submit", async (e) => {
    e.preventDefault();
    const changes = {};
    for (const key of Object.keys(inputs)) if (inputs[key].value !== (card[key] || "")) changes[key] = inputs[key].value;
    try {
      if (Object.keys(changes).length) {
        const updated = await api(`/api/cards/${card.id}`, { method: "PATCH", body: changes });
        Object.assign(card, updated);
      }
      state.editingId = null;
      renderCards();
    } catch (err) { toast(err.message, true); }
  });
  return form;
}

async function deleteCard(card) {
  try {
    await api(`/api/cards/${card.id}`, { method: "DELETE" });
    state.cards = state.cards.filter((c) => c.id !== card.id);
    renderCards();
    // A card can span several lines, so unmark every line of this video that is part of it.
    if (card.video_id === state.videoId) {
      state.lines.forEach((line, i) => {
        if (line.start >= card.start - 0.01 && line.end <= card.end + 0.01) state.lineEls[i].classList.remove("mined");
      });
    }
  } catch (e) { toast(e.message, true); }
}

async function refreshCards() {
  const scope = state.sessionAll ? "all" : "new";
  try { state.cards = (await api(`/api/cards?scope=${scope}`)).cards; renderCards(); } catch (e) { toast(e.message, true); }
}

$("all-sessions").addEventListener("change", (e) => { state.sessionAll = e.target.checked; refreshCards(); });

$("export-btn").addEventListener("click", async () => {
  const scope = state.sessionAll ? "all" : "new";
  try {
    const response = await fetch(`/api/export.apkg?scope=${scope}`);
    if (!response.ok) throw new Error((await response.json()).error || "Export failed.");
    const name = /filename="?([^";]+)/.exec(response.headers.get("Content-Disposition") || "")?.[1] || "ytmine.apkg";
    const url = URL.createObjectURL(await response.blob());
    const a = Object.assign(document.createElement("a"), { href: url, download: name });
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
    toast(`Saved ${name}. Open it with Anki to import.`);
    refreshCards();
  } catch (e) { toast(e.message, true); }
});

$("anki-btn").addEventListener("click", async () => {
  try {
    const r = await api("/api/anki/send", { method: "POST", body: { scope: state.sessionAll ? "all" : "new" } });
    toast(`Anki imported ${r.notes} note${r.notes === 1 ? "" : "s"}. Notes it already had were updated.`);
    refreshCards();
  } catch (e) { toast(e.message, true); }
});

// ---------------------------------------------------------------- settings

const CARD_TYPE_BOXES = {
  listening: "set-listening", reading: "set-reading",
  vocab_recognition: "set-recognition", vocab_production: "set-production",
};

async function loadSettings() {
  try {
    const cfg = await api("/api/settings");
    for (const [key, id] of Object.entries(CARD_TYPE_BOXES)) $(id).checked = !!cfg.card_types[key];
    $("set-split").value = cfg.deck_split;
  } catch (e) { /* the settings panel just keeps its defaults */ }
}

async function saveSettings() {
  const card_types = {};
  for (const [key, id] of Object.entries(CARD_TYPE_BOXES)) card_types[key] = $(id).checked;
  try {
    await api("/api/settings", { method: "PUT", body: { card_types, deck_split: $("set-split").value } });
    toast("Card settings saved.");
  } catch (e) { toast(e.message, true); }
}

$("settings").addEventListener("change", saveSettings);
loadSettings();

// ---------------------------------------------------------------- browser extension

// The extension's helper script marks this page once it has paired itself with the server.
function showExtensionState() {
  const state = document.documentElement.dataset.ytmineExtension;
  const el = $("ext-state");
  el.textContent = state === "connected" ? "(connected)" : "";
  el.className = state === "connected" ? "ok" : "";
}
document.addEventListener("ytmine-extension", showExtensionState);
showExtensionState();

// Started from a shortcut or at login, ytmine has no window to close, so the page can stop it.
$("quit-btn").addEventListener("click", async () => {
  if (!confirm("Quit ytmine? Your cards are saved. Start it again with the ytmine shortcut.")) return;
  try {
    const { token } = await api("/api/pairing");
    await api("/api/shutdown", { method: "POST", headers: { "X-Ytmine-Token": token } });
  } catch (e) { toast(e.message, true); return; }
  const banner = $("banner");
  banner.textContent = "ytmine has stopped. You can close this tab. Start it again with the ytmine shortcut, or run ytmine.";
  banner.hidden = false;
  $("quit-btn").disabled = true;
});

$("copy-token").addEventListener("click", async () => {
  try {
    const { token } = await api("/api/pairing");
    await navigator.clipboard.writeText(token);
    toast("Pairing token copied. Paste it in the extension's options, under Manual setup.");
  } catch (e) { toast(e.message || "Could not copy the token.", true); }
});

// ---------------------------------------------------------------- loading

$("pinyin-toggle").addEventListener("change", (e) => {
  document.body.classList.toggle("show-pinyin", e.target.checked);
  store("ytmine.pinyin", e.target.checked ? "1" : "0");
});

$("load-form").addEventListener("submit", async (e) => {
  e.preventDefault();
  const button = $("load-btn");
  button.disabled = true;
  $("status").textContent = "Fetching subtitles… (this can take 10 to 30 seconds)";
  try {
    const data = await api("/api/load", { method: "POST", body: { url: $("url").value } });
    state.lines = data.lines;
    state.starts = data.lines.map((l) => l.start);
    state.currentIdx = -1;
    state.selectedIdx = null;
    state.span = null;
    state.willMineKey = "";
    state.lastMined = null;
    state.videoId = data.video_id;
    clearTarget();
    renderTranscript();
    setAutoScroll(true);
    overlay.setLines(state.lines);
    overlay.setAutoSource(/auto/i.test(data.zh_source));
    refreshVocab();
    state.statusBase =
      `${data.title}: ${data.lines.length} lines. Chinese from ${data.zh_source}; ` +
      `English: ${data.en_source || "not available"}.` +
      (data.unpunctuated
        ? " These subtitles have no punctuation, so ytmine joined fragments into sentences. " +
          "If a card still cuts a sentence, use Shift+←/→ to add lines."
        : "") +
      (data.dictionary && data.dictionary.state !== "ready"
        ? " The dictionary is not available (it downloads once, then works offline), " +
          "so there are no definitions or idiom highlights."
        : "");
    state.audioState = "downloading";
    pollAudio();
    showVideo(data.video_id);
    refreshCards();
  } catch (err) {
    $("status").textContent = "";
    toast(err.message, true);
  } finally {
    button.disabled = false;
  }
});

function showStatus() {
  const audio = {
    downloading: "Audio is downloading in the background…",
    ready: "Audio ready.",
    error: `Audio failed: ${state.audioError || "unknown error"}`,
  }[state.audioState] || "";
  $("status").textContent = `${state.statusBase} ${audio}`.trim();
}

async function pollAudio() {
  const videoId = state.videoId;
  clearTimeout(pollAudio.timer);
  showStatus();
  let info;
  try { info = await api("/api/audio-status"); } catch (e) { info = { state: "downloading" }; }
  if (videoId !== state.videoId) return; // another video was loaded meanwhile
  state.audioState = info.state;
  state.audioError = info.error;
  showStatus();
  if (info.state === "downloading") pollAudio.timer = setTimeout(pollAudio, 2000);
  else if (info.state === "ready") loadLocalAudio();
}

async function init() {
  if (recall("ytmine.pinyin") === "1") {
    $("pinyin-toggle").checked = true;
    document.body.classList.add("show-pinyin");
  }
  try {
    const info = await api("/api/info");
    if (!info.ffmpeg) {
      const banner = $("banner");
      banner.textContent = info.ffmpeg_help;
      banner.hidden = false;
    }
  } catch (e) { toast(e.message, true); }
  refreshCards();
}
init();

// ---------------------------------------------------------------- caption overlay

// Words already mined or marked known, for the "mined" marks and the status colours.
state.vocab = { mined: new Set(), known: new Set() };

async function refreshVocab() {
  try {
    const v = await api("/api/vocab");
    state.vocab = { mined: new Set(v.mined), known: new Set(v.known) };
    overlay.refresh();
  } catch (e) { /* the colours just stay as they were */ }
}

function wordStatus(text) {
  if (state.vocab.known.has(text)) return "known";
  if (state.vocab.mined.has(text)) return "mined";
  return "new";
}

function overlayOptions() {
  try { return JSON.parse(recall("ytmine.overlay") || "{}"); } catch (e) { return {}; }
}

function playerState() {
  const player = activePlayer();
  if (!player) return null;
  try { return player.getPlayerState(); } catch (e) { return null; }
}

const overlay = YtmineOverlay.create({
  root: $("overlay-root"),
  options: overlayOptions(),
  currentIndex: () => state.currentIdx,
  getRange: () => state.span,
  getTime: () => {
    const player = activePlayer();
    try { return player ? player.getCurrentTime() : null; } catch (e) { return null; }
  },
  // 1 = playing, 2 = paused. Anything else (buffering, ended, not started) counts as unknown.
  isPlaying: () => { const st = playerState(); return st === 1 ? true : st === 2 ? false : null; },
  pause: () => activePlayer()?.pauseVideo(),
  play: () => activePlayer()?.playVideo(),
  lookup: (q) => api(`/api/lookup?q=${encodeURIComponent(q)}`),
  mine: async ({ from, to, target }) => {
    const card = await mineRange({ from, to }, target);
    return card ? { card } : { duplicate: true };
  },
  extend: () => {
    const base = state.span ? state.span.from : state.currentIdx;
    if (base < 0) return;
    extendSpan(1, base);
    const r = state.span;
    if (r) toast(`The card covers lines ${r.from + 1} to ${r.to + 1}. Click a word, or +, to mine it.`);
  },
  fixLine: (i, text) => api(`/api/lines/${i}`, { method: "PATCH", body: { text } }),
  onLineFixed: (i, line) => {
    state.lines[i] = line;
    const top = $("transcript").scrollTop;
    renderTranscript();
    $("transcript").scrollTop = top;
  },
  setKnown: async (text, known) => {
    const v = await api("/api/known", { method: "POST", body: { word: text, known } });
    state.vocab = { mined: new Set(v.mined), known: new Set(v.known) };
  },
  // The YouTube iframe swallows pointer events. While a popup is open, let them through to
  // the page so it can see the pointer leave the word.
  setShield: (on) => $("stage").classList.toggle("shield", on),
  getStatus: wordStatus,
  onOptions: (o) => store("ytmine.overlay", JSON.stringify(o)),
  extraButtons: [{ label: "⛶", title: "Fullscreen (F)", onClick: toggleFullscreen }],
});
