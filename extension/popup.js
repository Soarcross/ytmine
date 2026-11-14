"use strict";
/* The toolbar popup: this session's cards, with edit and delete, and the two export buttons.
 * Like the content script, it reaches the server only through the background worker. */

const $ = (id) => document.getElementById(id);
let cards = [];
let editingId = null;

function say(text, ok) {
  const el = $("message");
  el.textContent = text;
  el.className = ok === undefined ? "" : ok ? "ok" : "bad";
}
const scope = () => ($("all").checked ? "all" : "session");

function button(label, onClick, disabled = false) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.disabled = disabled;
  b.addEventListener("click", onClick);
  return b;
}

async function refresh() {
  try {
    cards = (await ytmJson(`/api/cards?scope=${scope()}`)).cards;
    say("");
  } catch (e) {
    cards = [];
    say(e.message, false);
  }
  render();
}

function render() {
  $("count").textContent = cards.length;
  const list = $("cards");
  list.textContent = "";
  for (const card of cards) {
    const li = document.createElement("li");
    li.appendChild(card.id === editingId ? editForm(card) : view(card));
    list.appendChild(li);
  }
}

function view(card) {
  const box = document.createElement("div");
  const zh = document.createElement("div");
  zh.className = "zh";
  if (card.parts) {
    const mark = document.createElement("mark");
    mark.textContent = card.parts.target;
    zh.append(card.parts.before, mark, card.parts.after);
  } else {
    zh.textContent = card.sentence;
  }
  const py = document.createElement("div");
  py.className = "py";
  py.textContent = card.pinyin || "";
  const en = document.createElement("div");
  en.className = "en";
  en.textContent = card.english || "";
  const btns = document.createElement("div");
  btns.className = "btns";
  btns.append(
    button("▶", () => play(card), !card.audio_url),
    button("Edit", () => { editingId = card.id; render(); }),
    button("Delete", () => remove(card)),
  );
  box.append(zh, py, en, btns);
  return box;
}

function editForm(card) {
  const form = document.createElement("form");
  const inputs = {};
  for (const [key, label] of [["sentence", "Sentence"], ["target", "Word (optional)"], ["pinyin", "Pinyin"], ["english", "English"]]) {
    const wrap = document.createElement("label");
    wrap.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.value = card[key] || "";
    wrap.appendChild(input);
    form.appendChild(wrap);
    inputs[key] = input;
  }
  const btns = document.createElement("div");
  btns.className = "btns";
  const save = button("Save", () => {});
  save.type = "submit";
  btns.append(save, button("Cancel", () => { editingId = null; render(); }));
  form.appendChild(btns);
  form.addEventListener("submit", async (event) => {
    event.preventDefault();
    const changes = {};
    for (const key of Object.keys(inputs)) if (inputs[key].value !== (card[key] || "")) changes[key] = inputs[key].value;
    try {
      if (Object.keys(changes).length) Object.assign(card, await ytmJson(`/api/cards/${card.id}`, { method: "PATCH", body: changes }));
      editingId = null;
      render();
    } catch (e) { say(e.message, false); }
  });
  return form;
}

async function remove(card) {
  try {
    await ytmJson(`/api/cards/${card.id}`, { method: "DELETE" });
    cards = cards.filter((c) => c.id !== card.id);
    render();
  } catch (e) { say(e.message, false); }
}

async function play(card) {
  const reply = await ytmRequest(card.audio_url, { binary: true });
  if (!reply.ok) { say(ytmProblem(reply) || "Could not play the clip.", false); return; }
  const audio = new Audio(URL.createObjectURL(ytmBlobFromReply(reply)));
  audio.play().catch(() => say("Could not play the clip.", false));
}

$("download").addEventListener("click", async () => {
  const reply = await ytmRequest(`/api/export.apkg?scope=${scope()}`, { binary: true });
  if (!reply.ok) {
    say(ytmProblem(reply) || (reply.data && reply.data.error) || "Export failed.", false);
    return;
  }
  const name = reply.filename || "ytmine.apkg";
  const link = Object.assign(document.createElement("a"), { href: URL.createObjectURL(ytmBlobFromReply(reply)), download: name });
  document.body.appendChild(link);
  link.click();
  link.remove();
  say(`Saved ${name}. Open it with Anki to import.`, true);
});

$("anki").addEventListener("click", async () => {
  try {
    const r = await ytmJson("/api/anki/send", { method: "POST", body: { scope: scope() } });
    say(`Anki imported ${r.notes} note${r.notes === 1 ? "" : "s"}. Notes it already had were updated.`, true);
  } catch (e) { say(e.message, false); }
});

$("all").addEventListener("change", refresh);
$("options").addEventListener("click", (event) => { event.preventDefault(); YTM_API.runtime.openOptionsPage(); });
// The full ytmine page, for when ytmine runs in the background (shortcut or start at login).
$("open").addEventListener("click", async (event) => {
  event.preventDefault();
  window.open(ytmLocalServer((await ytmGetSettings()).server), "_blank");
  window.close();
});

refresh();
