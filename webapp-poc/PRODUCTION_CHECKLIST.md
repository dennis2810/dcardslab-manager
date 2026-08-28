# Umstellung auf eBay-Produktion

Checkliste für den Wechsel von `EBAY_ENVIRONMENT=sandbox` auf
`production`. Rein manuelle/Konfigurationsschritte — der Code selbst
unterscheidet Sandbox/Produktion bereits vollständig über diese eine
Env-Variable (`ebay_client.EBAY_API_BASE`, `ebay-oauth-server`s
`EBAY_AUTH_BASE`/`EBAY_API_BASE`), keine Code-Änderung nötig, sobald
die folgenden Punkte erledigt sind.

## 1. eBay Developer Program

- [ ] Im [eBay Developer Program](https://developer.ebay.com/) einen
  **Produktions-Keyset** anlegen (separat vom bisherigen
  Sandbox-Keyset) — App ID (Client-ID), Client-Secret, RuName
  (Redirect-Name).
- [ ] Diese drei Werte als `EBAY_CLIENT_ID`/`EBAY_CLIENT_SECRET`/
  `EBAY_RUNAME` beim **`ebay-oauth-server`-Container** setzen (nicht
  die Sandbox-Werte wiederverwenden — Produktion braucht ein eigenes
  Keyset).

## 2. Business Policies im echten Verkäuferkonto

- [ ] Im echten (nicht Sandbox-)eBay-Verkäuferkonto unter Seller Hub →
  Account → Business Policies **Fulfillment-, Payment- und
  Return-Policy** anlegen — ohne diese schlägt jeder Publish-Versuch
  mit `get_listing_policies()`s Fehlermeldung fehl (identisch zum
  Sandbox-Verhalten).
- [ ] **Auszahlungsmethode/Bankverbindung** im echten Verkäuferkonto
  hinterlegt (Seller Hub → Payments) — ohne diese blockiert eBay das
  tatsächliche Live-Schalten eines Angebots, das haben wir in der
  Sandbox als wahrscheinlichste Ursache für den unklaren
  `errorId 25002`-Fehler identifiziert (siehe `README.md`, Abschnitt
  "Bekannte Einschränkung").
- [ ] Verpackungsgesetz/LUCID-Angabe im echten Konto geprüft, falls
  eBay das beim Live-Schalten für den deutschen Marktplatz verlangt.

## 3. Umgebungsvariablen umstellen

- [ ] `EBAY_ENVIRONMENT=production` bei **beiden** Containern setzen -
  `ebay-oauth-server` UND `dcardslab-webapp-poc` (`docker-compose.webapp-poc.yml`s
  `EBAY_ENVIRONMENT`). Beide müssen exakt übereinstimmen, sonst passt
  der vom oauth-server gelieferte Token nicht zur API-Basis-URL, die
  `webapp-poc` anspricht (führt zu 401-Fehlern).
- [ ] Beide Container neu starten, damit die neue Umgebungsvariable
  greift.

## 4. Erneuter OAuth-Flow

- [ ] Sandbox- und Produktions-Tokens sind getrennt — nach dem
  Umschalten einmalig erneut über `/ebay/oauth/start` (am
  `ebay-oauth-server`) den Autorisierungs-Flow gegen das echte
  eBay-Konto durchlaufen.

## 5. Erster Live-Test

- [ ] Mit **einer** Karte, niedriger Preis, volle Kontrolle: Entwurf
  anlegen, veröffentlichen, im echten eBay Seller Hub prüfen, dass das
  Angebot korrekt aussieht (Titel, Bild, Beschreibung inkl. des
  HTML-Footers, Preis, Kategorie/Item-Specifics).
- [ ] "Auf eBay ansehen"-Link in `card.html` prüfen — zeigt jetzt auf
  `ebay.de` statt `sandbox.ebay.de` (automatisch anhand
  `EBAY_ENVIRONMENT`, kein manueller Schritt mehr nötig, siehe unten).
- [ ] `POST /api/ebay/sync-sales` einmal gegen echte Bestellungen
  testen, sobald ein erster echter Verkauf stattgefunden hat.

## 6. Bekannter, hier bereits behobener Fehler

`card.html`s "Auf eBay ansehen"-Link war bis zu diesem Fix hartcodiert
auf `sandbox.ebay.de` (unabhängig von `EBAY_ENVIRONMENT`) — hätte nach
der Umstellung auf Produktion falsche/tote Links erzeugt. Jetzt liest
er die Umgebung dynamisch aus `GET /api/ebay/oauth/status` und baut
den Link entsprechend (`ebay.de` bei `production`, `sandbox.ebay.de`
sonst).

## Google Sheets (unabhängig von eBay)

Kein Sandbox/Produktion-Unterschied bei Google selbst - der
OAuth-Client spricht immer die echten Google-Endpunkte an. Einzig
relevant:

- [ ] Falls der OAuth-Client in der Google Cloud Console im
  **"Testing"-Modus** ist: das eigene Google-Konto (mit dem
  verbunden werden soll) unter "Test users" eintragen, sonst lehnt
  Google den Consent-Screen ab. Für reinen Eigenbedarf (ein Nutzer)
  ist "Testing" + Test-User-Eintrag ausreichend - eine
  Google-Verifizierung der App ist nicht nötig.
