// Makes the dashboard installable. It deliberately caches nothing: the inventory is live data from the NAS.
self.addEventListener("install", () => self.skipWaiting());
self.addEventListener("activate", event => event.waitUntil(self.clients.claim()));
self.addEventListener("fetch", () => {});
