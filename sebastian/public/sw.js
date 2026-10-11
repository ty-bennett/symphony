// Symphony service worker. It only does one thing: when the box can't be
// reached (phone off the shop's Wi-Fi, box unplugged), show a friendly page
// instead of the browser's error. Nothing else is cached, so the app is never
// stale.
const CACHE = "symphony-offline-v1"
const OFFLINE = "/offline.html"

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll([OFFLINE, "/icons/icon-192.png"])))
  self.skipWaiting()
})

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) => Promise.all(keys.filter((key) => key !== CACHE).map((key) => caches.delete(key)))),
  )
  self.clients.claim()
})

self.addEventListener("fetch", (event) => {
  if (event.request.mode !== "navigate") return
  event.respondWith(fetch(event.request).catch(() => caches.match(OFFLINE)))
})
