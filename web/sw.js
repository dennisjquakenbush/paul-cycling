/* Service worker: makes the dashboard installable and usable offline.
   Network-first for everything (so shell + data updates show immediately when
   online), falling back to the cache only when offline. */
const CACHE = "paul-mtb-v2";
const SHELL = ["./index.html", "./style.css", "./app.js", "./manifest.webmanifest",
  "./icon-192.png", "./icon-512.png"];

self.addEventListener("install", (e) => {
  e.waitUntil(caches.open(CACHE).then((c) => c.addAll(SHELL)).then(() => self.skipWaiting()));
});

self.addEventListener("activate", (e) => {
  e.waitUntil(caches.keys().then((keys) =>
    Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k)))
  ).then(() => self.clients.claim()));
});

self.addEventListener("fetch", (e) => {
  const url = new URL(e.request.url);
  if (e.request.method !== "GET") return;
  if (url.pathname.endsWith("/api/config")) return; // never cache the config API

  // network-first for everything: keep a fresh copy when online, fall back to
  // cache only when the network is unavailable (offline install still works).
  e.respondWith(
    fetch(e.request).then((res) => {
      const copy = res.clone();
      caches.open(CACHE).then((c) => c.put(e.request, copy));
      return res;
    }).catch(() => caches.match(e.request))
  );
});
