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
