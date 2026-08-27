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
  dcardslab-webapp-poc
```

`EBAY_OAUTH_SERVER_URL`/`EBAY_ENVIRONMENT` sind optional (Default:
`http://ebay-oauth-server:8080` bzw. `sandbox`) - nur nötig, falls der
oauth-server unter einer anderen Adresse läuft oder bereits auf
Produktion umgestellt wurde. `EBAY_ENVIRONMENT` muss mit dem Wert
übereinstimmen, den `ebay-oauth-server` selbst gesetzt hat.

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

- **Manuelles Anlegen einzelner Karten auf `cards.html`** (nächstes
  Sub-Projekt): Der 9er-Scan über `index.html` bleibt der primäre Weg,
  aber es soll auch möglich sein, eine einzelne Karte direkt auf
  `cards.html` anzulegen - inkl. Vorder-/Rückseiten-Upload vom Rechner/
  Device (nicht nur per Scan-Bogen), Dreh-Funktion wie in `card.html`
  bereits vorhanden, und nach Möglichkeit KI-Vorbelegung der Kartendaten
  aus den hochgeladenen Bildern (analog zu `recognize_card()` im
  bestehenden Scan-Flow).
- Google Drive/Sheets-Sync, Backups.
- Kein Build-Frontend (React/Next) - weiterhin nur die statische Testseite.
- CSV-Export/-Import für eBay (wie in `docs/EBAY_IMPORT.md` für die
  Desktop-App beschrieben) - bewusst nicht gebaut, da die Live-API jetzt
  sinnvoll nutzbar ist.
- Ein bereits live veröffentlichtes eBay-Angebot manuell beenden
  (`withdrawOffer`).
- Automatisches Nachtragen von eBay-Verkaufsgebühren (Finances API,
  eigener OAuth-Scope) - `ebay_sales.ebay_fees` bleibt `0`.
