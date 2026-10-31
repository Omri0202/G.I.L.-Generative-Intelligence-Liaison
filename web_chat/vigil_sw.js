/* G.I.L. on your phone — the app's service worker.
   It does three things: makes the chat installable as an app, shows G.I.L.'s
   push notifications (with Do it / Don't right on them), and opens the chat
   when one is tapped. It never keeps messages: the chat lives on your PC.
   Only the app's own shell is cached, so the app still opens — and can say
   what's wrong — when your PC or Tailscale can't be reached. */
const SHELL = "gil-vigil-shell-v1";
const SHELL_URLS = ["/", "/vigil.webmanifest", "/gil_icon_192.png", "/gil_icon_512.png",
                    "/gil_badge_96.png"];
const KEY = "/__pairing-key";        // the phone's pairing key, handed over by the page

self.addEventListener("install", (e) => {
  self.skipWaiting();
  e.waitUntil(caches.open(SHELL).then((c) => c.addAll(SHELL_URLS).catch(() => {})));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys()
    .then((keys) => Promise.all(keys.filter((k) => k !== SHELL).map((k) => caches.delete(k))))
    .then(() => self.clients.claim()));
});

// The page passes the pairing key along, so a tap on "Do it" works without opening the app.
self.addEventListener("message", (e) => {
  if (e.data && typeof e.data.key === "string") {
    e.waitUntil(caches.open(SHELL).then((c) => c.put(KEY, new Response(e.data.key))));
  }
});

self.addEventListener("fetch", (e) => {
  const req = e.request;
  const url = new URL(req.url);
  // The chat itself — messages, the live stream, voice notes — always comes from the PC.
  if (req.method !== "GET" || url.origin !== self.location.origin ||
      /^\/(api|events|voice)/.test(url.pathname) || url.pathname === KEY) return;
  e.respondWith(fetch(req)
    .then((res) => {
      if (res.ok && SHELL_URLS.includes(url.pathname)) {
        const copy = res.clone();
        caches.open(SHELL).then((c) => c.put(url.pathname, copy)).catch(() => {});
      }
      return res;
    })
    .catch(() => caches.match(url.pathname, { ignoreSearch: true })
      .then((hit) => hit || caches.match("/"))));
});

self.addEventListener("push", (e) => {
  let d = {};
  try { d = e.data ? e.data.json() : {}; } catch { d = { body: e.data ? e.data.text() : "" }; }
  e.waitUntil(self.registration.showNotification(d.title || "G.I.L.", {
    body: d.body || "",
    icon: "/gil_icon_192.png",
    badge: "/gil_badge_96.png",
    tag: d.tag || undefined,
    renotify: !!d.tag && !d.silent,          // a "done" replaces "working on it" — and buzzes
    silent: !!d.silent,
    requireInteraction: !!d.urgent,
    vibrate: d.silent ? undefined : [90, 50, 90],
    timestamp: Date.now(),
    data: { id: d.id || 0 },
    actions: (d.actions || []).slice(0, 2),
  }));
});

async function pressButton(id, action) {
  const hit = await (await caches.open(SHELL)).match(KEY);
  const key = hit ? await hit.text() : "";
  const res = await fetch("/api", {
    method: "POST",
    headers: { "Content-Type": "application/json", "X-Vigil": "1",
               ...(key ? { "X-GIL-Token": key } : {}) },
    body: JSON.stringify({ method: "button", args: [id, action] }),
  });
  if (!res.ok) throw new Error(String(res.status));
  const out = await res.json();
  if (!out || !out.result) throw new Error("no answer");
  return out.result;
}

self.addEventListener("notificationclick", (e) => {
  e.notification.close();
  const id = (e.notification.data || {}).id || 0;
  e.waitUntil((async () => {
    if (e.action && id) {
      try {
        const msg = await pressButton(id, e.action);
        const done = (msg.meta && msg.meta.resolved) || "Done";
        // A quiet confirmation, so a tap on "Do it" is never left wondering.
        return self.registration.showNotification("G.I.L.", {
          body: done, icon: "/gil_icon_192.png", badge: "/gil_badge_96.png",
          tag: `msg-${id}`, silent: true, data: { id },
        });
      } catch { /* PC out of reach — open the app instead, which says why */ }
    }
    const wins = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const w of wins) {
      if ("focus" in w) {
        w.postMessage({ jump: id });
        return w.focus();
      }
    }
    return self.clients.openWindow(`/?app=vigil${id ? `#m${id}` : ""}`);
  })());
});
