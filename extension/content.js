"use strict";
/* ytmine on youtube.com: draws the caption overlay inside the player and mines from it.
 *
 * The local ytmine server does the real work (subtitles, dictionary, audio clips, history,
 * export). This script only shows the result and relays clicks. It never contacts the server
 * itself: every request goes through the background worker (see background.js).
 */
(() => {
  if (window.__ytmineLoaded) return; // the script can be injected twice after an extension reload
  window.__ytmineLoaded = true;

  const LOOKAHEAD = 0.2; // seconds; show a line slightly early, like the web app
  const LATE_WINDOW_MS = 1500; // the mine key takes the previous line if the line just changed
  const MAX_SPAN_LINES = 6; // same limit as the server
  const OUR_ACTION_MS = 400; // play/pause events this soon after our own call are ours

  let settings = null;
  let session = null; // everything about the video currently shown
  let seq = 0; // bumped on every navigation, so late replies for an old video are dropped

  // ---------------------------------------------------------------- page helpers

  function currentVideoId() {
    if (location.pathname !== "/watch") return null;
    return new URL(location.href).searchParams.get("v");
  }

  function waitFor(find, timeoutMs) {
    return new Promise((resolve) => {
      const start = Date.now();
      const timer = setInterval(() => {
        const found = find();
        if (found || Date.now() - start > timeoutMs) { clearInterval(timer); resolve(found || null); }
      }, 150);
    });
  }

  const getRoot = () => document.getElementById("movie_player");
  const getVideo = () => (getRoot() ? getRoot().querySelector("video.html5-main-video, video") : null);

  // ---------------------------------------------------------------- status banner

  function showStatus(s, message, { retry = false, openYtmine = false, autoHideMs = 0 } = {}) {
    if (!s.statusEl) {
      s.statusEl = document.createElement("div");
      s.statusEl.className = "ytm-banner";
      s.root.appendChild(s.statusEl);
    }
    s.statusEl.textContent = message;
    if (openYtmine) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Open ytmine";
      button.addEventListener("click", () => window.open(ytmLocalServer(settings.server), "_blank"));
      s.statusEl.appendChild(button);
    }
    if (retry) {
      const button = document.createElement("button");
      button.type = "button";
      button.textContent = "Try again";
      button.addEventListener("click", () => { seq++; stop(); start(); });
      s.statusEl.appendChild(button);
    }
    clearTimeout(s.statusTimer);
    if (autoHideMs) s.statusTimer = setTimeout(() => hideStatus(s), autoHideMs);
  }

  function hideStatus(s) {
    if (s.statusEl) { s.statusEl.remove(); s.statusEl = null; }
  }

  // ---------------------------------------------------------------- starting and stopping

  async function start() {
    const id = currentVideoId();
    if (!id) { stop(); return; }
    if (session && session.id === id) return;
    stop();
    const mySeq = ++seq;
    const root = await waitFor(getRoot, 15000);
    if (!root || mySeq !== seq) return;

    const s = { id, root, lines: [], starts: [], currentIdx: -1, selectedIdx: null, lastChangeAt: -Infinity,
      span: null, vocab: { mined: new Set(), known: new Set() }, ourAction: 0, listeners: [] };
    session = s;
    settings = await ytmGetSettings();
    if (mySeq !== seq) return;
    const url = `https://www.youtube.com/watch?v=${id}`;

    // Ask quietly first: on videos that are not in Chinese ytmine shows nothing at all.
    const probe = await ytmRequest("/api/probe", { method: "POST", body: { url } });
    if (mySeq !== seq) return;
    if (probe.ok && probe.data && probe.data.chinese === false) return;
    // Any other answer (Chinese, an old server without /api/probe, an error) goes on to load,
    // which reports real problems such as ytmine not running.

    showStatus(s, "ytmine: loading the captions… (the first time can take 10 to 30 seconds)");
    const reply = await ytmRequest("/api/load", { method: "POST", body: { url } });
    if (mySeq !== seq) return;
    if (!reply.ok && reply.data && reply.data.code === "not_chinese") { hideStatus(s); return; }
    if (!reply.ok) {
      const message = ytmProblem(reply) || (reply.data && reply.data.error) || "ytmine could not load this video.";
      showStatus(s, `ytmine: ${message}`, { retry: true, openYtmine: ytmNeedsPairing(reply) });
      if (ytmNeedsPairing(reply)) retryWhenPaired(s, mySeq);
      return;
    }
    const data = reply.data;
    s.lines = data.lines;
    s.starts = data.lines.map((l) => l.start);
    mount(s, data);
  }

  /** Once the extension pairs itself (from the ytmine page), load this video again. */
  function retryWhenPaired(s, mySeq) {
    const onStorage = (changes) => {
      if (!changes.token && !changes.server) return;
      YTM_API.storage.onChanged.removeListener(onStorage);
      if (mySeq === seq && session === s) { stop(); start(); }
    };
    YTM_API.storage.onChanged.addListener(onStorage);
    s.listeners.push([{ removeEventListener: () => YTM_API.storage.onChanged.removeListener(onStorage) }, "", null, false]);
  }

  function stop() {
    seq++;
    const s = session;
    session = null;
    if (!s) return;
    clearInterval(s.ticker);
    clearTimeout(s.statusTimer);
    for (const [target, type, handler, capture] of s.listeners) target.removeEventListener(type, handler, capture);
    if (s.overlay) s.overlay.destroy();
    hideStatus(s);
    s.root.classList.remove("ytm-hide-yt-captions");
  }

  function listen(s, target, type, handler, capture = false) {
    target.addEventListener(type, handler, capture);
    s.listeners.push([target, type, handler, capture]);
  }

  // ---------------------------------------------------------------- the overlay

  function mount(s, data) {
    const video = () => getVideo();
    const ours = () => { s.ourAction = Date.now(); };

    s.overlay = YtmineOverlay.create({
      root: s.root,
      options: settings.overlay,
      autoSource: /auto/i.test(data.zh_source),
      currentIndex: () => (isAd(s) ? -1 : s.currentIdx),
      getRange: () => s.span,
      getTime: () => (video() ? video().currentTime : null),
      isPlaying: () => (video() ? !video().paused : null),
      pause: () => { ours(); video() && video().pause(); },
      play: () => { ours(); video() && video().play().catch(() => {}); },
      lookup: (q) => ytmJson(`/api/lookup?q=${encodeURIComponent(q)}`),
      mine: async ({ from, to, target }) => {
        const card = await mineRange(s, { from, to }, target);
        return card ? { card } : { duplicate: true };
      },
      extend: () => extendSpan(s, s.span ? s.span.from : s.currentIdx),
      fixLine: (i, text) => ytmJson(`/api/lines/${i}`, { method: "PATCH", body: { text, video_id: s.id } }),
      onLineFixed: (i, line) => { s.lines[i] = line; },
      setKnown: async (text, known) => {
        const v = await ytmJson("/api/known", { method: "POST", body: { word: text, known } });
        s.vocab = { mined: new Set(v.mined), known: new Set(v.known) };
      },
      getStatus: (text) => (s.vocab.known.has(text) ? "known" : s.vocab.mined.has(text) ? "mined" : "new"),
      onOptions: (o) => {
        settings.overlay = o;
        YTM_API.storage.local.set({ overlay: o });
        applyYouTubeCaptionSetting(s);
      },
      extraButtons: [{
        label: "YT", title: "Show or hide YouTube's own captions",
        onClick: () => {
          settings.hideYtCaptions = !settings.hideYtCaptions;
          YTM_API.storage.local.set({ hideYtCaptions: settings.hideYtCaptions });
          applyYouTubeCaptionSetting(s);
          s.overlay.toast(settings.hideYtCaptions ? "YouTube's captions are hidden." : "YouTube's captions are shown.");
        },
      }],
    });
    s.overlay.setLines(s.lines);
    applyYouTubeCaptionSetting(s);
    refreshVocab(s);

    // A play or pause that is not ours is the user's. A hover pause must not undo it.
    const userToggled = () => { if (Date.now() - s.ourAction > OUR_ACTION_MS) s.overlay.noteUserToggle(); };
    const hookVideo = () => {
      const v = video();
      if (!v || v === s.hookedVideo) return;
      s.hookedVideo = v;
      for (const type of ["play", "pause"]) listen(s, v, type, userToggled);
    };

    listen(s, window, "keydown", (event) => onKey(s, event), true);
    listen(s, document, "fullscreenchange", () => s.overlay.refresh());
    // Changing settings in the options page applies right away.
    const onStorage = (changes) => {
      ytmGetSettings().then((fresh) => {
        settings = fresh;
        applyYouTubeCaptionSetting(s);
      });
    };
    YTM_API.storage.onChanged.addListener(onStorage);
    s.listeners.push([{ removeEventListener: () => YTM_API.storage.onChanged.removeListener(onStorage) }, "", null, false]);

    s.ticker = setInterval(() => { hookVideo(); tick(s); }, 100);
    showStatus(s, `ytmine: ${s.lines.length} lines ready. Hover a word, click it to mine.`, { autoHideMs: 4000 });
  }

  const isAd = (s) => s.root.classList.contains("ad-showing");

  function applyYouTubeCaptionSetting(s) {
    s.root.classList.toggle("ytm-hide-yt-captions", !!(settings.hideYtCaptions && settings.overlay.enabled));
  }

  async function refreshVocab(s) {
    try {
      const v = await ytmJson("/api/vocab");
      if (session !== s) return;
      s.vocab = { mined: new Set(v.mined), known: new Set(v.known) };
      s.overlay.refresh();
    } catch (e) { /* the colours stay as they were */ }
  }

  // ---------------------------------------------------------------- following the video

  function indexAt(s, t) {
    let lo = 0, hi = s.starts.length - 1, found = -1;
    while (lo <= hi) {
      const mid = (lo + hi) >> 1;
      if (s.starts[mid] <= t + LOOKAHEAD) { found = mid; lo = mid + 1; } else hi = mid - 1;
    }
    return found;
  }

  function tick(s) {
    const v = getVideo();
    if (!v || session !== s) return;
    if (!isAd(s)) {
      const idx = indexAt(s, v.currentTime);
      if (idx !== s.currentIdx && performance.now() >= (s.seekGuardUntil || 0)) {
        s.currentIdx = idx;
        s.lastChangeAt = performance.now();
        if (s.selectedIdx !== null && s.selectedIdx !== idx) s.selectedIdx = null;
      }
    }
    s.overlay.update();
  }

  function jumpTo(s, idx, play = true) {
    if (idx < 0 || idx >= s.lines.length) return;
    const v = getVideo();
    if (!v) return;
    v.currentTime = s.lines[idx].start;
    if (play) v.play().catch(() => {});
    s.currentIdx = idx;
    s.selectedIdx = idx;
    s.span = null;
    s.lastChangeAt = -Infinity; // an explicit choice is never "late"
    s.seekGuardUntil = performance.now() + 500;
    s.overlay.refresh();
  }

  // ---------------------------------------------------------------- mining

  function lineToMine(s) {
    if (s.selectedIdx !== null) return s.selectedIdx;
    if (s.currentIdx < 0) return -1;
    const recentlyChanged = performance.now() - s.lastChangeAt < LATE_WINDOW_MS;
    return recentlyChanged && s.currentIdx > 0 ? s.currentIdx - 1 : s.currentIdx;
  }

  function extendSpan(s, base) {
    if (base < 0) return;
    const span = s.span ? { ...s.span } : { from: base, to: base };
    span.to = Math.min(span.to + 1, s.lines.length - 1);
    if (span.to - span.from + 1 > MAX_SPAN_LINES) {
      s.overlay.toast(`A card can cover at most ${MAX_SPAN_LINES} lines.`, true);
      return;
    }
    s.span = span;
    s.selectedIdx = null;
    s.overlay.toast(`The card covers lines ${span.from + 1} to ${span.to + 1}. Click a word, or +, to mine it.`);
  }

  /** Mine lines range.from..range.to. Resolves to the card, or null if it was already mined. */
  async function mineRange(s, range, target) {
    const body = { line_index: range.from, line_end: range.to, video_id: s.id };
    if (target && target.line >= range.from && target.line <= range.to) {
      body.target_line = target.line;
      body.target_text = target.text;
      body.target_offset = target.offset;
    }
    const reply = await ytmRequest("/api/mine", { method: "POST", body });
    if (reply.ok) {
      s.span = null;
      refreshVocab(s);
      return reply.data;
    }
    if (reply.status === 409) return null;
    const problem = ytmProblem(reply);
    throw new Error(problem || (reply.data && reply.data.error) || "Could not mine this line.");
  }

  async function mineKey(s) {
    const range = s.span || (lineToMine(s) >= 0 ? { from: lineToMine(s), to: lineToMine(s) } : null);
    if (!range) { s.overlay.toast("Nothing to mine yet. Start the video first."); return; }
    s.overlay.toast("Mining…");
    try {
      const card = await mineRange(s, range, null);
      s.overlay.toast(card ? `Mined: ${card.sentence}` : "Already mined.");
      s.overlay.refresh();
    } catch (e) {
      s.overlay.toast(e.message, true);
    }
  }

  // ---------------------------------------------------------------- hotkeys

  function onKey(s, event) {
    if (!settings || event.isComposing || ytmIsTyping(event) || !s.lines.length) return;
    const keys = settings.keys;
    const action = Object.keys(keys).find((name) => ytmMatchKey(event, keys[name]));
    if (!action) return;
    // Our key: keep it from reaching YouTube's own shortcuts.
    event.preventDefault();
    event.stopImmediatePropagation();
    if (event.repeat) return;
    const here = s.span ? s.span.from : lineToMine(s);
    if (action === "mine") mineKey(s);
    else if (action === "replay") jumpTo(s, here >= 0 ? here : s.currentIdx, true);
    else if (action === "prev") jumpTo(s, Math.max((s.selectedIdx ?? s.currentIdx) - 1, 0));
    else if (action === "next") jumpTo(s, Math.min((s.selectedIdx ?? s.currentIdx) + 1, s.lines.length - 1));
    else if (action === "extend") extendSpan(s, s.span ? s.span.from : s.currentIdx);
  }

  // ---------------------------------------------------------------- YouTube is a single-page app

  let navTimer = 0;
  const onNavigate = () => { clearTimeout(navTimer); navTimer = setTimeout(start, 150); };
  document.addEventListener("yt-navigate-finish", onNavigate);
  window.addEventListener("popstate", onNavigate);
  // A fallback for navigations that fire neither event.
  let lastHref = location.href;
  setInterval(() => {
    if (location.href !== lastHref) { lastHref = location.href; onNavigate(); }
  }, 1000);

  start();
})();
