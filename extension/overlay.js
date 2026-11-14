"use strict";
/* ytmine caption overlay: captions drawn over a video, a hover dictionary, click to mine.
 *
 * This file has no dependency on the rest of the page. The web app mounts it over its own
 * player, and the browser extension mounts it inside YouTube's #movie_player. Everything it
 * needs from the outside comes in through `opts`:
 *
 *   root            element to draw in (absolutely positioned children are placed inside it)
 *   currentIndex()  index of the line to show, or -1
 *   getRange()      {from, to} when several lines are picked for one card, else null
 *   getTime()       playback position in seconds, or null
 *   isPlaying()     true / false / null (unknown); pause(); play()
 *   lookup(q)       Promise of {entries, dictionary}
 *   mine({from, to, target})  Promise of {card} or {duplicate: true}; target is
 *                   {line, text, offset} or null
 *   extend()        add the next line to the card (Shift+click)
 *   fixLine(i, text) Promise of the corrected line
 *   setKnown(text, known)  Promise
 *   getStatus(text) "new" | "mined" | "known"
 *   setShield(on)   optional. Called with true while a popup is open. A host whose player is a
 *                   cross-origin iframe uses it to stop the iframe swallowing pointer events,
 *                   which would hide the pointer leaving the word.
 *   onOptions(o)    called when a toggle in the control bar changes
 *   extraButtons    [{label, title, onClick}] added to the control bar
 */
(function (global) {
  const HOVER_DELAY = 150; // ms to rest on a word before the video pauses
  const LEAVE_DELAY = 150; // ms grace when the pointer moves between a word and its popup
  const HIDE_AFTER = 1.5; // seconds after a line ends before it is hidden
  const MAX_DEFINITIONS = 5;

  const CSS = `
.ytm-cap, .ytm-bar, .ytm-pop, .ytm-toast { position: absolute; box-sizing: border-box; z-index: 2147483000;
  font-family: system-ui, "Segoe UI", "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif; }
.ytm-cap { left: 50%; transform: translateX(-50%); bottom: 14%; max-width: 92%; width: max-content;
  display: flex; align-items: flex-end; gap: 8px; padding: 6px 12px; border-radius: 8px;
  background: rgba(10, 12, 18, 0.8); color: #fff; font-size: var(--ytm-size, 26px); line-height: 1.9;
  text-align: center; user-select: none; }
.ytm-cap, .ytm-bar, .ytm-pop { pointer-events: auto; }
.ytm-cap[hidden], .ytm-pop[hidden], .ytm-bar[hidden] { display: none; }
.ytm-main { min-width: 0; }
.ytm-line { position: relative; }
.ytm-en { font-size: 0.55em; line-height: 1.3; color: #d1d5db; margin-top: 2px; }
.ytm-w { border-radius: 4px; cursor: pointer; }
.ytm-w:hover, .ytm-w.ytm-hl { background: rgba(96, 165, 250, 0.35); }
.ytm-x { border-bottom: 2px dotted #fbbf24; }
.ytm-x.ytm-k-suyu { border-bottom-color: #c4b5fd; }
.ytm-x.ytm-k-xiehouyu { border-bottom-color: #f9a8d4; }
.ytm-w.ytm-mined-now { color: #86efac; }
.ytm-colors .ytm-st-new { border-bottom: 2px solid #60a5fa; }
.ytm-colors .ytm-st-mined { color: #86efac; }
.ytm-colors .ytm-st-known { opacity: 0.6; }
.ytm-cap rt { font-size: 0.42em; color: #cbd5e1; }
.ytm-cap:not(.ytm-pinyin) rt { display: none; }
.ytm-tools { display: flex; flex-direction: column; gap: 4px; padding-bottom: 6px; }
.ytm-cap button, .ytm-bar button, .ytm-pop button { font: inherit; font-size: 14px; line-height: 1.2;
  padding: 3px 8px; border-radius: 6px; border: 1px solid rgba(255,255,255,0.35);
  background: rgba(255,255,255,0.1); color: #fff; cursor: pointer; }
.ytm-cap button:hover, .ytm-bar button:hover, .ytm-pop button:hover { background: rgba(255,255,255,0.25); }
.ytm-edit { position: absolute; right: -2px; top: 0; font-size: 12px !important; opacity: 0; }
.ytm-line:hover .ytm-edit, .ytm-edit:focus { opacity: 1; }
.ytm-input { font: inherit; font-size: 0.8em; width: min(60vw, 40em); padding: 2px 8px; border-radius: 6px;
  border: 1px solid #60a5fa; background: #0b1020; color: #fff; }
/* Faint until used. It cannot rely on a mouse-move reveal: a cross-origin iframe player
   swallows the pointer events that would trigger it. */
.ytm-bar { top: 8px; right: 8px; display: flex; gap: 6px; opacity: 0.4; transition: opacity 0.2s; }
.ytm-bar:hover, .ytm-bar.ytm-show, .ytm-bar:focus-within { opacity: 1; }
.ytm-bar button[aria-pressed="true"] { background: rgba(96, 165, 250, 0.55); border-color: #93c5fd; }
.ytm-badge { align-self: center; font-size: 12px; padding: 2px 8px; border-radius: 10px; color: #fde68a;
  background: rgba(10, 12, 18, 0.7); border: 1px solid rgba(253, 230, 138, 0.5); }
.ytm-pop { width: min(420px, 92%); max-height: 60%; overflow-y: auto; padding: 8px 10px; border-radius: 8px;
  background: #111827; color: #f3f4f6; font-size: 14px; line-height: 1.4; text-align: left;
  box-shadow: 0 6px 24px rgba(0, 0, 0, 0.5); }
.ytm-entry + .ytm-entry { margin-top: 8px; padding-top: 8px; border-top: 1px solid #374151; }
.ytm-head { display: flex; flex-wrap: wrap; align-items: baseline; gap: 8px; }
.ytm-word { font-size: 22px; }
.ytm-read { color: #93c5fd; }
.ytm-kind { font-size: 12px; padding: 0 8px; border-radius: 10px; border: 1px solid #9ca3af; color: #d1d5db; }
.ytm-status { font-size: 12px; color: #9ca3af; margin-left: auto; }
.ytm-def { margin: 3px 0 0; padding-left: 0; }
.ytm-def .ytm-r { color: #93c5fd; margin-right: 6px; }
.ytm-note { color: #9ca3af; font-size: 13px; }
.ytm-act { display: flex; gap: 6px; margin-top: 6px; }
.ytm-toast { top: 12%; left: 50%; transform: translateX(-50%); padding: 8px 14px; border-radius: 8px;
  background: rgba(17, 24, 39, 0.95); color: #fff; font-size: 16px; opacity: 0; pointer-events: none;
  transition: opacity 0.2s; max-width: 90%; white-space: pre-wrap; }
.ytm-toast.ytm-on { opacity: 1; }
.ytm-toast.ytm-err { background: rgba(185, 28, 28, 0.95); }
`;

  function el(doc, tag, cls, text) {
    const node = doc.createElement(tag);
    if (cls) node.className = cls;
    if (text !== undefined) node.textContent = text;
    return node;
  }

  function create(opts) {
    const root = opts.root;
    const doc = root.ownerDocument;
    if (!doc.getElementById("ytm-style")) {
      const style = el(doc, "style");
      style.id = "ytm-style";
      style.textContent = CSS;
      doc.head.appendChild(style);
    }

    const options = { enabled: true, pinyin: false, english: true, colors: false, ...(opts.options || {}) };
    const s = {
      lines: [],
      version: 0, // bumped whenever what is drawn could change
      drawnKey: null,
      editing: null, // line index being edited
      open: false, // popup is showing
      hoverEl: null,
      seq: 0,
      pausedByHover: false,
      userTouched: false,
      autoSource: !!opts.autoSource,
    };
    const cache = new Map(); // lookup results by query
    let hoverTimer = 0, leaveTimer = 0, toastTimer = 0, barTimer = 0;

    // ---- DOM
    const bar = el(doc, "div", "ytm-bar");
    const cap = el(doc, "div", "ytm-cap");
    const main = el(doc, "div", "ytm-main");
    const tools = el(doc, "div", "ytm-tools");
    const pop = el(doc, "div", "ytm-pop");
    const toastEl = el(doc, "div", "ytm-toast");
    cap.hidden = true;
    pop.hidden = true;
    const plus = el(doc, "button", "ytm-plus", "+");
    plus.type = "button";
    plus.title = "Mine this line with no target word";
    tools.appendChild(plus);
    cap.append(main, tools);
    root.append(bar, cap, pop, toastEl);

    function button(label, title, onClick, pressed) {
      const b = el(doc, "button", "", label);
      b.type = "button";
      b.title = title;
      if (pressed !== undefined) b.setAttribute("aria-pressed", String(pressed));
      b.addEventListener("click", onClick);
      return b;
    }
    function buildBar() {
      bar.textContent = "";
      if (s.autoSource) bar.append(el(doc, "span", "ytm-badge", "auto-captions: check the words"));
      const toggle = (key, label, title) =>
        bar.appendChild(button(label, title, () => {
          options[key] = !options[key];
          s.version++;
          buildBar();
          render();
          if (opts.onOptions) opts.onOptions({ ...options });
        }, options[key]));
      toggle("enabled", "CC", "Show or hide the ytmine captions");
      toggle("pinyin", "拼", "Show pinyin above the words");
      toggle("english", "EN", "Show the English line");
      toggle("colors", "●", "Colour words by status: new, mined, known");
      for (const extra of opts.extraButtons || []) bar.appendChild(button(extra.label, extra.title, extra.onClick));
    }
    buildBar();

    function showBar() {
      bar.classList.add("ytm-show");
      clearTimeout(barTimer);
      barTimer = setTimeout(() => bar.classList.remove("ytm-show"), 2500);
    }
    root.addEventListener("pointermove", showBar);

    // Text size follows the player size, so it stays readable when the player is resized
    // (theater mode, fullscreen).
    const resize = () => {
      const w = root.clientWidth || 640;
      cap.style.setProperty("--ytm-size", `${Math.max(16, Math.min(44, w / 26))}px`);
    };
    resize();
    const observer = global.ResizeObserver ? new global.ResizeObserver(resize) : null;
    if (observer) observer.observe(root);

    // ---- drawing the caption
    function displayedRange() {
      const r = opts.getRange && opts.getRange();
      if (r) return r;
      const i = opts.currentIndex();
      return i >= 0 ? { from: i, to: i } : null;
    }

    function drawLine(i) {
      const line = s.lines[i];
      const wrap = el(doc, "div", "ytm-line");
      wrap.dataset.i = i;
      const exprs = line.expressions || [];
      let offset = 0;
      line.tokens.forEach(([text, reading, isWord], ti) => {
        const start = offset, end = offset + text.length;
        offset = end;
        if (!isWord) { wrap.appendChild(doc.createTextNode(text)); return; }
        const w = el(doc, "span", "ytm-w");
        Object.assign(w.dataset, { i, s: start, e: end, ti });
        const hits = [];
        exprs.forEach((x, xi) => { if (x.start < end && x.end > start) hits.push(xi); });
        if (hits.length) {
          w.classList.add("ytm-x");
          w.dataset.x = hits.join(",");
          const longest = hits.reduce((a, b) => (exprs[b].end - exprs[b].start > exprs[a].end - exprs[a].start ? b : a));
          w.classList.add(`ytm-k-${exprs[longest].kind}`);
        }
        if (options.colors && opts.getStatus) w.classList.add(`ytm-st-${opts.getStatus(text)}`);
        if (reading) {
          const ruby = el(doc, "ruby");
          ruby.append(doc.createTextNode(text), el(doc, "rt", "", reading));
          w.appendChild(ruby);
        } else {
          w.textContent = text;
        }
        wrap.appendChild(w);
      });
      const edit = el(doc, "button", "ytm-edit", "✎");
      edit.type = "button";
      edit.title = "Fix this caption";
      edit.dataset.i = i;
      wrap.appendChild(edit);
      if (line.edited) wrap.title = "You corrected this caption";
      return wrap;
    }

    function render(force) {
      if (s.open || s.editing !== null) return; // never redraw under the pointer or the text box
      const range = options.enabled && s.lines.length ? displayedRange() : null;
      let visible = !!range;
      if (range && !(opts.getRange && opts.getRange())) {
        const t = opts.getTime ? opts.getTime() : null;
        const last = s.lines[range.to];
        if (t != null && last && t > last.end + HIDE_AFTER) visible = false;
      }
      const key = visible ? `${range.from}-${range.to}|${s.version}` : `hidden|${s.version}`;
      if (!force && key === s.drawnKey) return;
      s.drawnKey = key;
      cap.hidden = !visible;
      if (!visible) { main.textContent = ""; return; }
      cap.classList.toggle("ytm-pinyin", options.pinyin);
      cap.classList.toggle("ytm-colors", options.colors);
      main.textContent = "";
      const english = [];
      for (let i = range.from; i <= range.to; i++) {
        main.appendChild(drawLine(i));
        if (s.lines[i].english) english.push(s.lines[i].english);
      }
      if (options.english && english.length) main.appendChild(el(doc, "div", "ytm-en", english.join(" ")));
    }

    // ---- pausing while a word is hovered
    function pauseForHover() {
      if (s.pausedByHover) return;
      if (opts.isPlaying && opts.isPlaying() === true) {
        opts.pause();
        s.pausedByHover = true;
        s.userTouched = false;
      } // already paused (Space, or the video is stopped): leave it alone
    }
    function resumeAfterHover() {
      const ours = s.pausedByHover && !s.userTouched;
      s.pausedByHover = false;
      // Only resume if our pause is still what is holding the video.
      if (ours && opts.isPlaying && opts.isPlaying() === false) opts.play();
    }

    // ---- the popup
    function candidatesFor(wordEl) {
      const i = Number(wordEl.dataset.i), line = s.lines[i];
      const start = Number(wordEl.dataset.s), end = Number(wordEl.dataset.e);
      const list = (wordEl.dataset.x || "").split(",").filter(Boolean).map((xi) => {
        const x = line.expressions[Number(xi)];
        return { line: i, text: x.text, offset: x.start, start: x.start, end: x.end, kind: x.kind, expr: true };
      });
      list.sort((a, b) => b.end - b.start - (a.end - a.start)); // longest match first
      list.push({
        line: i, text: line.text.slice(start, end), offset: start, start, end, kind: "word", expr: false,
        reading: line.tokens[Number(wordEl.dataset.ti)][1],
      });
      return list;
    }

    function lookup(q) {
      if (!cache.has(q)) {
        cache.set(q, opts.lookup(q).catch(() => ({ entries: [], dictionary: { state: "unavailable" } })));
      }
      return cache.get(q);
    }

    function highlight(cand) {
      clearHighlight();
      cap.querySelectorAll(".ytm-w").forEach((w) => {
        if (Number(w.dataset.i) === cand.line && Number(w.dataset.s) < cand.end && Number(w.dataset.e) > cand.start) {
          w.classList.add("ytm-hl");
        }
      });
    }
    function clearHighlight() { cap.querySelectorAll(".ytm-hl").forEach((w) => w.classList.remove("ytm-hl")); }

    function buildEntry(cand, token) {
      const box = el(doc, "div", "ytm-entry");
      const head = el(doc, "div", "ytm-head");
      head.append(el(doc, "span", "ytm-word", cand.text));
      if (cand.kind !== "word") head.append(el(doc, "span", "ytm-kind", cand.kind));
      const status = el(doc, "span", "ytm-status", opts.getStatus ? opts.getStatus(cand.text) : "");
      head.appendChild(status);
      const body = el(doc, "div", "ytm-note", "Looking up…");
      const act = el(doc, "div", "ytm-act");
      act.append(button("Mine", `Mine the line with “${cand.text}” as the target`, () => mineCandidate(cand)));
      const knownBtn = button(
        opts.getStatus && opts.getStatus(cand.text) === "known" ? "Not known" : "Known", "Mark this word as known",
        async () => {
          const was = opts.getStatus(cand.text) === "known";
          try { await opts.setKnown(cand.text, !was); } catch (e) { toast(e.message, true); return; }
          status.textContent = opts.getStatus(cand.text);
          knownBtn.textContent = !was ? "Not known" : "Known";
          s.version++;
        },
      );
      act.appendChild(knownBtn);
      box.append(head, body, act);
      lookup(cand.text).then((res) => {
        if (token !== s.seq) return;
        body.textContent = "";
        body.className = "";
        const entries = res.entries || [];
        if (!entries.length) {
          body.className = "ytm-note";
          const off = res.dictionary && res.dictionary.state !== "ready";
          body.textContent = (cand.reading ? `${cand.reading}. ` : "") +
            (off ? "The dictionary is not available, so there are no definitions." : "No dictionary entry.");
        }
        for (const entry of entries.slice(0, 4)) {
          const row = el(doc, "div", "ytm-def");
          row.append(el(doc, "span", "ytm-r", entry.reading));
          if (entry.alt_form) row.append(el(doc, "span", "ytm-r", `(${entry.alt_form})`));
          row.append(doc.createTextNode(entry.definitions.slice(0, MAX_DEFINITIONS).join("; ")));
          body.appendChild(row);
        }
        place();
      });
      return box;
    }

    function place() {
      if (!s.open || !s.hoverEl) return;
      const rr = root.getBoundingClientRect(), wr = s.hoverEl.getBoundingClientRect();
      pop.style.maxHeight = `${Math.max(120, wr.top - rr.top - 8)}px`;
      const left = Math.min(Math.max(wr.left - rr.left, 8), Math.max(8, rr.width - pop.offsetWidth - 8));
      pop.style.left = `${left}px`;
      // Sit directly above the word, overlapping it slightly so the pointer never crosses a gap.
      pop.style.bottom = `${rr.bottom - wr.top - 4}px`;
      pop.style.top = "auto";
    }

    function openPopup(wordEl) {
      s.hoverEl = wordEl;
      s.open = true;
      s.seq++;
      pauseForHover();
      doc.addEventListener("pointermove", onDocMove, true);
      if (opts.setShield) opts.setShield(true);
      const cands = candidatesFor(wordEl);
      highlight(cands[0]);
      pop.textContent = "";
      cands.forEach((c) => pop.appendChild(buildEntry(c, s.seq)));
      pop.hidden = false;
      place();
    }

    function closePopup() {
      clearTimeout(hoverTimer);
      cancelLeave();
      doc.removeEventListener("pointermove", onDocMove, true);
      if (opts.setShield) opts.setShield(false);
      const wasOpen = s.open;
      s.open = false;
      s.hoverEl = null;
      s.seq++;
      pop.hidden = true;
      clearHighlight();
      resumeAfterHover();
      if (wasOpen) render();
    }

    function cancelLeave() { clearTimeout(leaveTimer); leaveTimer = 0; }
    function scheduleLeave() {
      cancelLeave();
      leaveTimer = setTimeout(() => { leaveTimer = 0; closePopup(); }, LEAVE_DELAY);
    }
    // Belt and braces: while the popup is open, a pointer resting anywhere that is not the
    // caption or the popup closes it, even if no pointerout reached us.
    function onDocMove(e) {
      if (!s.open) return;
      if (cap.contains(e.target) || pop.contains(e.target)) return;
      if (!leaveTimer) scheduleLeave();
    }

    cap.addEventListener("pointerover", (e) => {
      const w = e.target.closest && e.target.closest(".ytm-w");
      if (!w || e.pointerType === "touch") return;
      cancelLeave(); // coming back to the word (or onto another) cancels a pending close
      if (w === s.hoverEl) return;
      clearTimeout(hoverTimer);
      // Resting on a word for a moment, not sweeping across the captions, opens the popup.
      hoverTimer = setTimeout(() => openPopup(w), s.open ? 0 : HOVER_DELAY);
    });
    cap.addEventListener("pointerout", (e) => {
      const w = e.target.closest && e.target.closest(".ytm-w");
      if (!w) return;
      if (e.relatedTarget && w.contains(e.relatedTarget)) return; // moved between its own children
      clearTimeout(hoverTimer);
      if (s.open) scheduleLeave();
    });
    pop.addEventListener("pointerenter", cancelLeave);
    pop.addEventListener("pointerleave", () => { if (s.open) scheduleLeave(); });

    // ---- mining
    function toast(message, isError) {
      toastEl.textContent = message;
      toastEl.className = `ytm-toast ytm-on${isError ? " ytm-err" : ""}`;
      clearTimeout(toastTimer);
      toastTimer = setTimeout(() => (toastEl.className = "ytm-toast"), isError ? 5000 : 2200);
    }

    async function mineWith(target, label) {
      const range = displayedRange();
      if (!range) return;
      closePopup();
      toast("Mining…");
      try {
        const result = await opts.mine({ from: range.from, to: range.to, target });
        toast(result && result.duplicate ? "Already mined." : `Mined: ${label}`);
      } catch (e) {
        toast(e.message, true);
      }
      s.version++;
      render();
    }
    const mineCandidate = (cand) => mineWith({ line: cand.line, text: cand.text, offset: cand.offset }, cand.text);
    const mineLine = () => {
      const range = displayedRange();
      if (range) mineWith(null, s.lines[range.from].text);
    };

    cap.addEventListener("click", (e) => {
      const t = e.target;
      if (t.closest(".ytm-input")) return;
      const editBtn = t.closest(".ytm-edit");
      if (editBtn) { startEdit(Number(editBtn.dataset.i)); return; }
      if (t.closest(".ytm-plus")) { mineLine(); return; }
      if (e.shiftKey) { if (opts.extend) opts.extend(); s.version++; render(); return; }
      const w = t.closest(".ytm-w");
      if (w) {
        // The longest highlighted match wins. The popup lists the shorter ones with their own Mine buttons.
        mineCandidate(candidatesFor(w)[0]);
        return;
      }
      if (t.closest(".ytm-line")) mineLine();
    });

    // ---- fixing a caption
    function startEdit(i) {
      closePopup();
      if (opts.isPlaying && opts.isPlaying() === true) opts.pause();
      s.editing = i;
      const wrap = main.querySelector(`.ytm-line[data-i="${i}"]`);
      if (!wrap) { s.editing = null; return; }
      const input = el(doc, "input", "ytm-input");
      input.type = "text";
      input.value = s.lines[i].text;
      input.setAttribute("aria-label", "Corrected caption");
      // Keep typing away from the page's and YouTube's own shortcuts.
      for (const type of ["keydown", "keyup", "keypress"]) input.addEventListener(type, (e) => e.stopPropagation());
      input.addEventListener("keydown", async (e) => {
        if (e.key === "Escape") { s.editing = null; render(true); }
        if (e.key !== "Enter") return;
        e.preventDefault();
        const text = input.value.trim();
        if (!text || text === s.lines[i].text) { s.editing = null; render(true); return; }
        try {
          const fixed = await opts.fixLine(i, text);
          s.lines[i] = fixed;
          if (opts.onLineFixed) opts.onLineFixed(i, fixed);
          toast("Caption corrected.");
        } catch (err) {
          toast(err.message, true);
        }
        s.editing = null;
        s.version++;
        render(true);
      });
      wrap.textContent = "";
      wrap.appendChild(input);
      input.focus();
      input.select();
    }

    return {
      setLines(lines) { s.lines = lines; s.version++; s.drawnKey = null; cache.clear(); render(true); },
      update() { render(); },
      refresh() { s.version++; render(); },
      setAutoSource(on) { s.autoSource = on; buildBar(); },
      getOptions() { return { ...options }; },
      toast,
      closePopup,
      // The user pressed play or pause themselves, so a hover pause must not undo it.
      noteUserToggle() { s.userTouched = true; s.pausedByHover = false; },
      destroy() {
        clearTimeout(hoverTimer); cancelLeave(); clearTimeout(toastTimer); clearTimeout(barTimer);
        doc.removeEventListener("pointermove", onDocMove, true);
        if (opts.setShield) opts.setShield(false);
        if (observer) observer.disconnect();
        root.removeEventListener("pointermove", showBar);
        bar.remove(); cap.remove(); pop.remove(); toastEl.remove();
      },
    };
  }

  global.YtmineOverlay = { create };
})(window);
