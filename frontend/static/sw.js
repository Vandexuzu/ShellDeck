// ShellDeck service worker — offline shell caching for PWA installability.
const CACHE = "shelldeck-v1";
const SHELL = ["/", "/login", "/static/app.js", "/static/style.css", "/static/login.js", "/manifest.webmanifest"];

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
  );
  self.clients.claim();
});

self.addEventListener("fetch", (event) => {
  const req = event.request;
  if (req.method !== "GET") return;
  const url = new URL(req.url);
  // Never cache API calls or the websocket terminal.
  if (url.pathname.startsWith("/api") || url.pathname.startsWith("/ws") || url.pathname.includes("terminal")) return;
  // Cache-first for static assets, network fallback for navigation.
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).then((res) => {
      if (res.ok && url.origin === location.origin) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    }).catch(() => caches.match("/")))
  );
});
