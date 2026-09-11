// Minimaler Service Worker, nur fuer die PWA-Installierbarkeit (manche
// Browser verlangen einen registrierten Service Worker mit fetch-Handler,
// bevor sie "Zum Startbildschirm hinzufuegen" anbieten). Cached bewusst
// nichts: die App zeigt staendig aktuelle Bestands-/Verkaufsdaten, ein
// Cache wuerde veraltete Ansichten ausliefern.
self.addEventListener("install", () => {
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener("fetch", () => {
  // Kein respondWith() - jeder Request geht unveraendert ans Netzwerk.
});

// Web-Push (siehe push_notify.py) - Payload ist JSON {title, body, url},
// von send_push_to_all() gesetzt. Auf iOS/Android als Home-Bildschirm-App
// funktioniert das auch bei geschlossener App (siehe README.md).
self.addEventListener("push", (event) => {
  let data = { title: "DCardsLab", body: "" };
  try {
    data = event.data.json();
  } catch (err) {
    // Kein/kein JSON-Payload - Fallback auf die Default-Werte oben statt
    // die Benachrichtigung ganz zu verwerfen.
  }
  event.waitUntil(
    self.registration.showNotification(data.title || "DCardsLab", {
      body: data.body || "",
      icon: "assets/icon-192.png",
      badge: "assets/icon-192.png",
      data: { url: data.url || "/dashboard.html" },
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const url = (event.notification.data && event.notification.data.url) || "/dashboard.html";
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((clientList) => {
      for (const client of clientList) {
        if (client.url.endsWith(url) && "focus" in client) return client.focus();
      }
      if (self.clients.openWindow) return self.clients.openWindow(url);
    })
  );
});
