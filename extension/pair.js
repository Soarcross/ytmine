"use strict";
/* Pairs the extension with ytmine by itself, so nobody has to copy the token by hand.
 *
 * This runs on pages at 127.0.0.1 and localhost. It does nothing unless the page is ytmine's
 * own (it carries <meta name="ytmine-pairing">), so other local web servers never see a request
 * from it. On ytmine's page it reads the token from the server as that page (same origin),
 * hands it to the background worker, which checks it against the server before saving it
 * together with this page's address. If ytmine moved to another port, this follows it.
 */
(() => {
  if (!document.querySelector('meta[name="ytmine-pairing"]')) return;
  const api = globalThis.browser || globalThis.chrome;
  // Firefox runs content-script fetches as the extension; content.fetch runs them as the page.
  const pageFetch = typeof content !== "undefined" && content.fetch ? content.fetch.bind(content) : fetch;

  const mark = (state) => {
    document.documentElement.dataset.ytmineExtension = state;
    document.dispatchEvent(new CustomEvent("ytmine-extension"));
  };

  (async () => {
    let token;
    try {
      const response = await pageFetch("/api/pairing", { cache: "no-store" });
      if (!response.ok) return mark("failed");
      token = (await response.json()).token;
    } catch (e) {
      return mark("failed");
    }
    if (typeof token !== "string" || !token) return mark("failed");
    try {
      const reply = await api.runtime.sendMessage({ type: "pair", token });
      mark(reply && reply.ok ? "connected" : "failed");
    } catch (e) {
      mark("failed");
    }
  })();
})();
