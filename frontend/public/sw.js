const CACHE = "opengridiron-v1";
self.addEventListener("install", (event) => event.waitUntil(caches.open(CACHE).then((cache) => cache.addAll(["/", "/football.svg"]))));
self.addEventListener("activate", (event) => event.waitUntil((async () => {
  // Retire the cached shell from before the Open Gridiron rename.
  await caches.delete("fourth-down-v1");
  await self.clients.claim();
})()));
self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET" || event.request.url.includes("/api/")) return;
  event.respondWith(fetch(event.request).catch(() => caches.match(event.request).then((result) => result || caches.match("/"))));
});
self.addEventListener("push", (event) => {
  const data = event.data ? event.data.json() : { title: "Open Gridiron", body: "New football alert" };
  event.waitUntil(self.registration.showNotification(data.title, { body: data.body, icon: "/football.svg", data: { url: data.url || "/news" } }));
});
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(self.clients.openWindow(event.notification.data?.url || "/"));
});
