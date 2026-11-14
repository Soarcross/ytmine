"use strict";

const $ = (id) => document.getElementById(id);

function say(text, ok) {
  const el = $("result");
  el.textContent = text;
  el.className = ok === undefined ? "" : ok ? "ok" : "bad";
}

async function load() {
  const s = await ytmGetSettings();
  $("server").value = s.server;
  $("token").value = s.token;
  $("hide").checked = !!s.hideYtCaptions;

  const box = $("keys");
  box.textContent = "";
  for (const [name, label] of Object.entries(YTM_KEY_LABELS)) {
    const text = document.createElement("label");
    text.textContent = label;
    text.style.margin = "0";
    text.style.fontWeight = "400";
    const input = document.createElement("input");
    input.type = "text";
    input.readOnly = true; // it records a key press instead of text
    input.value = s.keys[name];
    input.setAttribute("aria-label", label);
    input.addEventListener("keydown", async (event) => {
      event.preventDefault();
      if (event.key === "Tab") return;
      if (event.key === "Backspace") input.value = YTM_DEFAULTS.keys[name];
      else {
        const combo = ytmComboFromEvent(event);
        if (!combo) return;
        input.value = combo;
      }
      const keys = { ...(await ytmGetSettings()).keys, [name]: input.value };
      await YTM_API.storage.local.set({ keys });
      say("Hotkey saved.", true);
    });
    box.append(text, input);
  }
}

$("show").addEventListener("click", () => {
  const hidden = $("token").type === "password";
  $("token").type = hidden ? "text" : "password";
  $("show").textContent = hidden ? "Hide" : "Show";
});

$("hide").addEventListener("change", () => YTM_API.storage.local.set({ hideYtCaptions: $("hide").checked }));

$("test").addEventListener("click", async () => {
  await YTM_API.storage.local.set({ server: $("server").value.trim(), token: $("token").value.trim() });
  say("Testing…");
  const reply = await ytmRequest("/api/info");
  if (reply.ok) {
    const dict = reply.data.dictionary && reply.data.dictionary.state;
    say(`Connected to ytmine. Dictionary: ${dict === "ready" ? "ready" : "not available yet"}.`, true);
  } else {
    say(ytmProblem(reply) || `ytmine answered with an error (${reply.status}).`, false);
  }
});

load();
