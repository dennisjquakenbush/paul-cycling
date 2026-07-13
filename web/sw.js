/* Service worker: makes the dashboard installable and usable offline.
   Network-first for data.js (so it shows the latest daily analysis when online),
   cache-first for the static shell. */
const CACHE = "paul-mtb-v1";
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

  const isData = url.pathname.endsWith("data.js");
  if (isData) {
    // network-first: freshest analysis when online, cached copy when offline
    e.respondWith(
      fetch(e.request).then((res) => {
        const copy = res.clone();
        caches.open(CACHE).then((c) => c.put(e.request, copy));
        return res;
      }).catch(() => caches.match(e.request))
    );
  } else {
    // cache-first for the static shell
    e.respondWith(caches.match(e.request).then((r) => r || fetch(e.request)));
  }
});
