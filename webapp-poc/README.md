# DCardLabs Web PoC

Beantwortet genau eine Frage, bevor wir Zeit in DB/Auth/Frontend stecken:
**Funktioniert Upload → Zuschnitt → KI-Erkennung sauber als Web-Request?**

Mittlerweile wird jeder Scan in Supabase persistiert (Postgres + Storage,
siehe unten) - der validierte PoC-Workflow bildet damit die Basis für die
eigentliche WebApp-Architektur. Seit Sub-Projekt 4 lassen sich Karten auch
als eBay-Angebote veröffentlichen (siehe unten). Weiterhin kein Login.

## Was hier passiert

- `POST /api/scan` nimmt Vorder- und Rückseiten-Scanbogen (multipart/
  form-data) entgegen.
- Schneidet beide mit `scanner/scanner_v0_8_dynamic.py` (unverändert aus
  der Desktop-App) in je 9 Karten.
- Lässt jede der 9 Kartenpaare von `integrations/ai_card_recognition.py`
  (ebenfalls unverändert) per Claude Vision erkennen, bis zu 4 parallel.
- Gibt die 9 erkannten Karten als JSON zurück.
- `static/index.html` ist eine einzelne HTML-Seite mit Upload-Formular und
  Ergebnistabelle, um das ohne Frontend-Build direkt im Browser zu testen.
- Persistiert jede gescannte Karte in Supabase Postgres (`scan_batches`,
  `cards`) und lädt die (komprimierten) Kartenbilder in Supabase Storage
  hoch - siehe `supabase/README.md` für die einmalige Projekt-Einrichtung.
- `GET /api/cards` und `GET /api/cards/{id}` lesen gespeicherte Karten
  inkl. frisch signierter Bild-URLs zurück.
- `static/cards.html` zeigt gespeicherte Karten als durchsuchbare/
  filterbare Liste (Freitext über Titel/Team/Set/Kartennummer,
  Status-Filter); `static/card.html` zeigt eine einzelne Karte im
  Detail zum Bearbeiten/Vervollständigen fehlender Felder oder Löschen,
  inkl. Vor-/Zurück-Navigation innerhalb der zuletzt geladenen Liste
  (gemerkt in `sessionStorage`, kein eigener Backend-Endpoint nötig).
- `PATCH /api/cards/{id}` aktualisiert einzelne Felder einer Karte,
  `DELETE /api/cards/{id}` löscht eine Karte inkl. ihrer Bilder im
  Storage.
- `POST /api/cards/{id}/rotate` (Body: `{"side": "front"|"back", "degrees":
  90|180|270}`) dreht das gespeicherte Bild einer Seite im Nachhinein -
  nutzbar über die Dreh-Buttons in `static/card.html`. Zusätzlich lässt
  sich schon vor dem Scannen in `static/index.html` jeder Bogen per
  Vorschau + Dreh-Buttons korrigieren, bevor er hochgeladen wird (rein
  clientseitig per Canvas, das Backend bekommt nur das bereits gedrehte
  Bild zu sehen).
- `static/card-new.html` legt eine einzelne Karte manuell an, ohne
  9er-Scan-Bogen - Vorder-/Rückseite vom Gerät hochladen (Pflicht),
  Vorschau + Drehen vor dem Speichern (gleiches Canvas-Verfahren wie
  `index.html`), optional per Button "KI erkennen" die Felder aus den
  Bildern vorbelegen lassen (`POST /api/cards/recognize`, ruft
  `recognize_card()` auf, ohne etwas zu speichern), dann "Karte
  speichern" (`POST /api/cards`, multipart: `front`/`back` + `fields`
  als JSON-String) - legt wie jede gescannte Karte einen
  `scan_batches`-Eintrag mit `card_count=1` an, leitet danach zu
  `card.html?id=...` weiter.
- Käufe (Einzelkauf oder Sammelkauf/Lot) lassen sich erfassen, durchsuchen,
  bearbeiten und löschen (`static/purchases.html`/`purchase.html`,
  `POST`/`GET`/`PATCH`/`DELETE /api/purchases[/{id}]`). Einzelne Karten
  lassen sich einem Kauf zuordnen bzw. die Zuordnung wieder lösen - sowohl
  über `purchase.html` als auch direkt im "Kauf"-Bereich von `card.html`
  (`POST`/`DELETE /api/purchases/{id}/items[/{item_id}]`). Eine Karte
  gehört höchstens einem Kauf gleichzeitig.
- Karten lassen sich als eBay-Angebote anlegen und veröffentlichen
  (`static/ebay.html` für die Übersicht inkl. Mehrfachauswahl, der
  "eBay"-Bereich in `static/card.html` für einzelne Karten). Sport/
  Non-Sport-Kategorie und Pflicht-Item-Specifics werden aus den
  Kartendaten sowie den eBay-Vorlagen in `templates/ebay/` automatisch
  abgeleitet. Angebote lassen sich für einen späteren Zeitpunkt planen
  (`scheduled_at` auf `POST /api/ebay/listings/{id}/publish`) - **natives
  eBay-Scheduling ist noch nicht gegen die Sandbox verifiziert**
  (`ebay_client.NATIVE_SCHEDULING_SUPPORTED = False`), bis dahin läuft
  jede Planung über den zuverlässigen App-seitigen Fallback
  (`ebay_scheduler.py`, kein eBay-seitiges Vorab-Einsehen möglich). Siehe
  `docs/superpowers/specs/2026-08-27-webapp-ebay-integration-design.md`,
  Abschnitt "Sandbox-Spike", für den offenen Verifikationsschritt.
  `POST /api/ebay/sync-sales` holt Verkäufe von eBay und verknüpft sie
  automatisch mit dem passenden Angebot. Die dafür genutzte
  eBay-Sell-API-Anbindung (`ebay_client.py`) spricht `ebay-oauth-server`
  nur noch für einen Access-Token an (`GET
  /api/internal/access-token`), alle Listing-Operationen laufen direkt
  von hier aus gegen eBay.
- `static/settings.html` bündelt zwei unabhängige Funktionen:
  **Backup** (`GET /api/backup`, ein Klick lädt eine ZIP-Datei mit allen
  Supabase-Tabellen als JSON plus allen Kartenbildern herunter - eine
  Kopie außerhalb von Supabase) und **Google-Sheets-Sync**
  (einseitig Supabase → Sheets, manuell per Button, vier Tabs: Karten/
  Käufe/eBay/Sync_Info). Die Google-Verbindung läuft über einen
  redirect-basierten OAuth-Flow direkt in `webapp-poc`
  (`google_sheets_client.py`, portiert vom bewährten
  `ebay-oauth-server`-Muster) statt der Desktop-App's lokalem
  Browser-Flow, der auf einem headless NAS-Container nicht
  funktioniert - siehe Einrichtung unten.

### Google-Sheets-Sync einrichten

Einmalige manuelle Einrichtung, unabhängig vom Backup-Download (der
braucht keine Google-Konfiguration):

1. Google Cloud Console → Projekt wählen/anlegen, **Google Sheets
   API** aktivieren.
2. Unter „APIs & Services" → „Credentials" einen OAuth-Client vom Typ
   **„Web application"** anlegen (nicht „Desktop app" - ein
   bestehender Desktop-Client aus der alten Desktop-App-Einrichtung
   ist hier nicht wiederverwendbar, da er keine Redirect-URI
   akzeptiert).
3. Als autorisierte Redirect-URI eintragen:
   `http://<nas-tailscale-name>:8000/api/sheets/oauth/callback`.
4. Client-ID/-Secret als `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`
   sowie die Redirect-URI aus Schritt 3 als `GOOGLE_REDIRECT_URI` beim
   `webapp-poc`-Container-Deployment setzen (s. u.).
5. In Google Sheets eine neue Tabelle anlegen, die Tabellen-ID aus der
   URL kopieren (`https://docs.google.com/spreadsheets/d/<ID>/edit`).
6. Auf `settings.html` auf „Mit Google verbinden" klicken (einmaliger
   Consent-Screen), danach die Tabellen-ID eintragen und speichern.

### Bekannte Einschränkung: eBay-Sandbox kann `publishOffer` mit generischem Systemfehler blockieren

Live gegen die echte eBay-Sandbox getestet (27.08.2026): `POST .../offer/{id}/publish`
schlägt reproduzierbar mit `errorId 25002` fehl
(`"A user error has occurred. Systemfehler. Ihre Anfrage konnte nicht
bearbeitet werden. Bitte versuchen Sie es zu einem späteren Zeitpunkt
erneut."`) - ohne weitere Details, auch für ein komplett frisches Angebot
(neue SKU, `create_offer` statt `update_offer`).

Vor einem erneuten Debugging-Anlauf **zuerst prüfen, ob es diesmal an
dieser Codebase liegt** - folgende Ursachen wurden bereits ausgeschlossen
(alle mit Logging/direkten eBay-API-Abfragen live verifiziert):
- Fehlender `merchantLocationKey` (behoben, siehe `ensure_merchant_location()`).
- `includeCatalogProductDetails` (eBays Default `true`, explizit auf
  `false` gesetzt in `_offer_payload()`).
- Fehlendes/nicht erreichbares Bild (Bild ist öffentlich erreichbar und
  laut `GET /sell/inventory/v1/inventory_item/{sku}` korrekt bei eBay
  hinterlegt).
- Ein einzelnes "kaputtes" Offer (ein komplett frisches Angebot mit neuer
  SKU scheitert identisch).

Stattdessen zeigten sich an diesem Tag mehrere voneinander unabhängige
Ausfälle der eBay-Sandbox-Weboberfläche selbst (My-eBay-Aktivitäten-Seite,
der von eBay verlinkte DE-Payments-Link zeigt auf eine nicht auflösbare
interne eBay-Testdomain, `sandbox.ebay.de` selbst nicht erreichbar) -
zusammen mit öffentlich dokumentierten, wiederkehrenden Sandbox-Ausfällen
in der eBay-Developer-Community. Das deutet stark auf ein eBay-seitiges
Sandbox-Infrastrukturproblem hin, nicht auf einen Bug in dieser Codebase.

Empfehlung: `https://developer.ebay.com/support/api-status/sandbox` auf
gemeldete Vorfälle prüfen, ein paar Stunden/Tage warten, dann erneut
versuchen. Falls der Fehler dann verschwindet, ohne dass sich an dieser
Codebase etwas geändert hat, ist die obige Diagnose bestätigt.

## Starten auf dem NAS (Docker)

Läuft als **eigener, zweiter Container** neben `ebay-oauth-server` - andere
Abhängigkeiten (OpenCV, Anthropic, FastAPI statt Flask+eBay), kein
gemeinsamer Code außer diesem Repo-Checkout. Nicht in den
OAuth-Container packen.

Build-Kontext ist bewusst der **Repo-Root** (nicht `webapp-poc/`), weil
`scanner/` und `integrations/` unverändert mit reinkopiert werden:

```bash
# Im Hauptordner des Repos (dcardslab-manager), nicht in webapp-poc/:
docker build -f webapp-poc/Dockerfile -t dcardslab-webapp-poc .

docker run -d --name dcardslab-webapp-poc -p 8000:8000 \
  -e ANTHROPIC_API_KEY=dein-api-key \
  -e SUPABASE_URL=dein-supabase-project-url \
  -e SUPABASE_SERVICE_KEY=dein-supabase-service-role-key \
  -e EBAY_OAUTH_SERVER_URL=http://<nas-adresse>:8080 \
  -e EBAY_ENVIRONMENT=sandbox \
  -e GOOGLE_CLIENT_ID=deine-google-client-id \
  -e GOOGLE_CLIENT_SECRET=dein-google-client-secret \
  -e GOOGLE_REDIRECT_URI=http://<nas-tailscale-name>:8000/api/sheets/oauth/callback \
  dcardslab-webapp-poc
```

`EBAY_OAUTH_SERVER_URL`/`EBAY_ENVIRONMENT` sind optional (Default:
`http://ebay-oauth-server:8080` bzw. `sandbox`) - nur nötig, falls der
oauth-server unter einer anderen Adresse läuft oder bereits auf
Produktion umgestellt wurde. `EBAY_ENVIRONMENT` muss mit dem Wert
übereinstimmen, den `ebay-oauth-server` selbst gesetzt hat.
`GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/`GOOGLE_REDIRECT_URI` sind
nur nötig, falls Google-Sheets-Sync genutzt werden soll (s. o.) - ohne
sie funktioniert alles andere inkl. Backup-Download unverändert.

Dann von irgendeinem Gerät im Tailscale-Netz: `http://<nas-tailscale-name>:8000`
öffnen (Port 8000, nicht 8080 - das ist der OAuth-Server).

`docker logs -f dcardslab-webapp-poc` zeigt Fehler beim Verarbeiten; zum
Stoppen `docker rm -f dcardslab-webapp-poc`, das läuft ohne Volume, es
bleibt nichts Persistentes übrig.

`scanner_v0_8_dynamic.py` importiert (ungenutzt für `process()`, aber
vorhanden) `tkinter` auf Modulebene - `main.py` stubbt das Modul vorab weg
(gleiches Muster wie in `tests/`), damit kein System-Paket wie `python3-tk`
im Image installiert werden muss.

## Lokal starten (ohne Docker)

```bash
pip install -r webapp-poc/requirements.txt
export ANTHROPIC_API_KEY=dein-api-key   # gleicher Key wie in der Desktop-App
uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir webapp-poc
```

## Was absichtlich fehlt (kommt in späteren Sub-Projekten)

- Kein Build-Frontend (React/Next) - weiterhin nur die statische Testseite.
- CSV-Export/-Import für eBay (wie in `docs/EBAY_IMPORT.md` für die
  Desktop-App beschrieben) - bewusst nicht gebaut, da die Live-API jetzt
  sinnvoll nutzbar ist.
- Ein bereits live veröffentlichtes eBay-Angebot manuell beenden
  (`withdrawOffer`).
- Automatisches Nachtragen von eBay-Verkaufsgebühren (Finances API,
  eigener OAuth-Scope) - `ebay_sales.ebay_fees` bleibt `0`.
