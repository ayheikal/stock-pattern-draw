// Service worker: pages and their files load from the network when possible, so prices are always
// fresh; the last copy is kept for when the connection drops. API answers are never cached.
const CACHE = "pattern-charts-v1";
const SHELL = ["/profile", "/market", "/screener", "/track", "/", "/static/style.css", "/static/pwa.js",
  "/static/profile.js", "/static/market.js", "/static/screener.js", "/static/track.js", "/static/app.js",
  "/static/icons/icon-192.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).catch(() => {}));
  self.skipWaiting();
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))));
  self.clients.claim();
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET" || url.origin !== location.origin || url.pathname.startsWith("/api/")) return;
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone();
          caches.open(CACHE).then((c) => c.put(e.request, copy));
        }
        return res;
      })
      .catch(() => caches.match(e.request).then((hit) => hit || caches.match("/profile")))
  );
});
