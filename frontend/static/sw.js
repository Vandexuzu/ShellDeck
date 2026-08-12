// ShellDeck service worker — offline shell caching for PWA installability.
// Cache version is bumped on every meaningful frontend change so the browser
// drops stale cached assets (index.html / app.js) after a deploy.
const CACHE = "shelldeck-v3";
const SHELL = ["/login", "/static/login.js", "/static/style.css", "/manifest.webmanifest"];

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
  // Never cache the HTML navigation (index.html). It carries a ?v= cache-buster
  // on app.js/style.css, so always fetch it fresh from the network.
  if (url.pathname === "/" ) return;
  // Cache-first only for versioned static assets under /static.
  if (!url.pathname.startsWith("/static/")) return;
  event.respondWith(
    caches.match(req).then((cached) => cached || fetch(req).then((res) => {
      if (res.ok && url.origin === location.origin) {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(req, copy));
      }
      return res;
    }))
  );
});
