/* G.I.L. service worker — its only job is to make GIL an INSTALLABLE app.
   Browsers require a registered SW with a fetch handler before they offer
   "Install". GIL is a live app talking to a local server, so we do NOT cache
   dynamic data — the fetch handler is network-first and only falls back to a
   cached shell when the network is unavailable. */
const SHELL = "gil-shell-v1";
const SHELL_URLS = ["/", "/manifest.json", "/gil_icon_192.png", "/gil_icon_512.png"];

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS).catch(() => {})));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  // never intercept the API or the SSE stream — those must always hit the server
  if (req.method !== "GET" || req.url.includes("/api") || req.url.includes("/events")) {
    return;
  }
  // network-first: live server when reachable, cached shell only as a fallback
  e.respondWith(
    fetch(req)
      .then((res) => {
        if (res && res.ok && new URL(req.url).origin === self.location.origin) {
          const copy = res.clone();
          caches.open(SHELL).then((c) => c.put(req, copy).catch(() => {}));
        }
        return res;
      })
      .catch(() => caches.match(req).then((hit) => hit || caches.match("/"))));
});
