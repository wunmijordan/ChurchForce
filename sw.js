const STATIC_CACHE = "static-cache-v2";

// Install
self.addEventListener("install", (event) => {
  console.log("Service Worker installed");
  self.skipWaiting();
});

// Activate — purge old caches
self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches.keys().then((keys) =>
      Promise.all(keys.map((key) => key !== STATIC_CACHE && caches.delete(key)))
    )
  );
  self.clients.claim();
});

// Fetch
self.addEventListener("fetch", (event) => {
  const url = new URL(event.request.url);

  // Never intercept WebSocket upgrades
  if (event.request.headers.get("upgrade") === "websocket") return;

  // Never intercept Chrome extension requests
  if (url.protocol.startsWith("chrome-extension")) return;

  // Pass through external CDN requests without caching
  if (url.origin !== self.location.origin) {
    event.respondWith(fetch(event.request));
    return;
  }

  // Cache-first for static assets
  if (
    event.request.method === "GET" &&
    ["style", "script", "font", "image"].includes(event.request.destination)
  ) {
    event.respondWith(
      caches.open(STATIC_CACHE).then(async (cache) => {
        const cached = await cache.match(event.request);
        const fetchPromise = fetch(event.request)
          .then((response) => {
            if (response.status === 200) cache.put(event.request, response.clone());
            return response;
          })
          .catch(() => cached);
        return cached || fetchPromise;
      })
    );
  }
});

// Push notifications
self.addEventListener("push", (event) => {
  let data = {};
  if (event.data) {
    try { data = event.data.json(); } catch (_) {}
  }

  const options = {
    body:    data.body    || "You have a new notification",
    icon:    "/static/images/icons/icon-192x192.png",
    badge:   "/static/images/icons/icon-192x192.png",
    vibrate: data.vibration ? [200, 100, 200] : undefined,
    data:    data.url || "/",
  };

  if (data.sound) options.sound = `/static/sounds/${data.sound}.mp3`;

  event.waitUntil(
    self.registration.showNotification(data.title || "ChurchForce", options)
  );
});

// Notification click
self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  event.waitUntil(
    clients.matchAll({ type: "window" }).then((windows) => {
      for (const w of windows) {
        if (w.url === event.notification.data && "focus" in w) return w.focus();
      }
      return clients.openWindow(event.notification.data || "/");
    })
  );
});