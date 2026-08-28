# WebApp Sub-Projekt 6: Backup + Google-Sheets-Sync

Status: Freigegeben (Brainstorming + Spec-Review mit Nutzer abgeschlossen
2026-08-28)

## Kontext

Sechstes Sub-Projekt der WebApp-Migration. Letzter verbliebener,
nicht bewusst ausgeschlossener Punkt aus `webapp-poc/README.md`s
"Was absichtlich fehlt": **Google Drive/Sheets-Sync, Backups.**

Die Desktop-App (`integrations/google_drive_sync.py`,
`google_sheets_sync.py`) hatte dafür zwei getrennte Google-Integrationen:
Drive für Bild-Hosting + ZIP-Backups, Sheets für einen einseitigen
SQLite→Sheets-Reporting-Export. Beide nutzen einen **"Desktop app"-
OAuth-Client** mit `flow.run_local_server(port=0)` — das öffnet einen
lokalen Browser-Port auf derselben Maschine, auf der der Code läuft.
Für `webapp-poc` (headless im Docker-Container auf dem NAS, bedient von
einem ganz anderen Gerät im Tailscale-Netz) funktioniert das nicht.

## Brainstorming-Entscheidungen

- **Google Drive entfällt komplett.** Bild-Hosting läuft bereits
  vollständig über Supabase Storage (seit der eBay-Integration sogar
  public-read). Eine Backup-Funktion ersetzt Drive-Backups, s. u.
- **Backup:** Download-Button im Browser statt automatischem
  NAS-Volume-Schreiben — erzeugt eine ZIP-Datei serverseitig, der
  Browser lädt sie direkt herunter. Kein neues Docker-Volume, keine
  docker-compose-Änderung nötig; der Nutzer legt die Datei selbst dort
  ab, wo er sie haben möchte (z. B. NAS-Ordner, eigener Rechner).
- **Backup-Umfang:** Alle sechs Supabase-Tabellen (`scan_batches`,
  `cards`, `purchases`, `purchase_items`, `ebay_listings`,
  `ebay_sales`) als JSON, **plus** alle Kartenbilder aus dem
  `card-images`-Bucket — eine ZIP-Datei, damit ein Supabase-Ausfall
  oder eine versehentlich gelöschte Zeile nicht zum vollständigen
  Datenverlust führt (Supabase Free-Tier pausiert Projekte nach einer
  Woche Inaktivität, s. `supabase/README.md`).
- **Google-Sheets-OAuth:** Neuer, redirect-basierter Web-OAuth-Flow
  direkt in `webapp-poc` (kein neuer Service) — **portiert dieselbe
  bewährte Architektur wie `ebay-oauth-server`s eBay-OAuth-Flow**
  (`/oauth/start` → Google-Konsens-Bildschirm → `/oauth/callback` →
  Token speichern), nur mit einem **"Web application"-OAuth-Client**
  statt des bisherigen "Desktop app"-Clients (neue, einmalige manuelle
  Einrichtung in der Google Cloud Console nötig — bestehende
  `credentials.json` aus der Desktop-App ist nicht wiederverwendbar).
- **Token-Speicherung:** In Supabase (neue Tabelle
  `google_sheets_settings`, Singleton-Row), **nicht** in einer lokalen
  Datei — `webapp-poc`s Container läuft ohne persistentes Volume (s.
  README, Abschnitt "Starten auf dem NAS"), ein Datei-basierter Token
  wäre bei jedem Container-Neustart verloren. Reuse bereits vorhandener
  Infrastruktur statt eines neuen Docker-Volumes.
- **Sync-Richtung/-Trigger:** Genau wie in der Desktop-App — einseitig
  Supabase → Sheets, manuell per Button ausgelöst (kein Scheduler, kein
  automatischer Hintergrund-Sync). Supabase bleibt die Master-Quelle.
- **Sheets-Tabs:** An das tatsächliche WebApp-Schema angepasst statt
  1:1 die (inzwischen veralteten) Desktop-Tab-Namen zu kopieren — die
  WebApp hat kein separates `inventory`, dafür `purchase_items` und
  `ebay_listings`/`ebay_sales`, die die Desktop-App nicht kannte. Vier
  Tabs: **Karten**, **Käufe**, **eBay**, **Sync_Info** (s.
  [Sheets-Tab-Mapping](#sheets-tab-mapping)).
- **UI-Ort:** Neue Seite `static/settings.html` (Verbindungsstatus +
  Tabellen-ID-Eingabe + "Jetzt synchronisieren"-Button für Sheets,
  "Backup herunterladen"-Button) — passt zum bestehenden Muster eigener
  Seiten pro Vorgang, kein bestehender Ort (`cards.html`/`ebay.html`)
  passt thematisch.

## Ziel

- Auf `settings.html`: Google-Verbindungsstatus sehen, OAuth-Flow
  starten, eine Ziel-Tabellen-ID hinterlegen, Sync manuell auslösen.
- Karten/Käufe/eBay-Angebote landen nach einem Sync als lesbare/
  editierbare Ansicht in Google Sheets (reine Reporting-Ansicht,
  Supabase bleibt Master — Sheets-Änderungen werden beim nächsten Sync
  überschrieben, wie bisher in der Desktop-App).
- Ein Klick auf "Backup herunterladen" liefert eine ZIP-Datei mit allen
  Tabellen (JSON) und allen Kartenbildern — unabhängige Kopie außerhalb
  von Supabase.

## Datenmodell

Eine neue Tabelle in `supabase/schema.sql`, Singleton-Row-Pattern (nur
eine Google-Verbindung für die gesamte App):

```sql
create table if not exists google_sheets_settings (
    id              boolean primary key default true check (id),
    refresh_token   text default '',
    spreadsheet_id  text default '',
    connected_at    timestamptz,
    last_synced_at  timestamptz
);
```

`check (id)` erzwingt, dass niemals mehr als eine Zeile existieren
kann (Standard-Postgres-Trick für App-weite Singleton-Einstellungen) —
`upsert` mit `id=true` legt die Zeile beim ersten OAuth-Abschluss an,
jeder weitere Aufruf aktualisiert dieselbe Zeile.

## Architektur

```
webapp-poc/
    google_sheets_client.py  (neu) - OAuth-Flow (Authorization Code,
                               Web-Application-Client) + Sheets-API-
                               Schreiblogik, portiert aus
                               integrations/google_sheets_sync.py
    backup.py                 (neu) - ZIP-Erzeugung: alle Tabellen als
                               JSON + alle Bilder aus card-images
    db.py                     (erweitert) - CRUD fuer
                               google_sheets_settings, Lesefunktionen
                               fuer alle sechs Tabellen (Backup)
    main.py                   (erweitert) - neue /api/sheets/*- und
                               /api/backup-Endpoints
    static/settings.html      (neu) - Verbindungsstatus, Sync-Button,
                               Backup-Download-Button
```

Kein neuer Service, **keine neuen Python-Pakete** — `google_sheets_client.py`
spricht Googles OAuth2-Token-Endpoint und die Sheets-API v4 direkt per
`httpx`-REST-Aufrufen an (`_request()`-Helper analog zu `ebay_client.py`),
statt der schwereren `google-api-python-client`/`google-auth-oauthlib`-
SDK-Pakete, die die Desktop-App nutzt (dort historisch bedingt, wegen des
lokalen `InstalledAppFlow`-Browser-Flows). `httpx` ist bereits Dependency,
keine Änderung an `requirements.txt` nötig.

### Google-OAuth-Flow (portiert vom `ebay-oauth-server`-Muster)

Neue Env-Vars: `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`,
`GOOGLE_REDIRECT_URI` (z. B.
`http://<nas-tailscale-name>:8000/api/sheets/oauth/callback`).

**Einmalige manuelle Einrichtung** (dokumentiert in
`webapp-poc/README.md`, Abschnitt Sheets-Sync, analog zu
`ebay-oauth-server/README.md`s eBay-Setup):
1. Google Cloud Console → Projekt wählen/anlegen, Google Sheets API
   aktivieren.
2. OAuth-Client vom Typ **"Web application"** anlegen (nicht "Desktop
   app" — der bestehende Desktop-Client aus der alten Einrichtung ist
   nicht wiederverwendbar, da er keine Redirect-URI akzeptiert).
3. `GOOGLE_REDIRECT_URI` als autorisierte Redirect-URI im Client
   eintragen.
4. Client-ID/-Secret als `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET` beim
   `webapp-poc`-Container-Deployment setzen.
5. In `settings.html` auf "Mit Google verbinden" klicken → einmaliger
   Consent-Screen.

Ablauf (1:1 analog zu `ebay-oauth-server/app.py`s `/ebay/oauth/start`/
`/ebay/oauth/callback`, nur mit Googles Token-Endpoint statt eBays):

- `GET /api/sheets/oauth/start` → Redirect zu Googles
  Autorisierungs-URL (`scope=https://www.googleapis.com/auth/spreadsheets`,
  `access_type=offline`, `prompt=consent` — letzteres erzwingt, dass
  Google bei jedem erneuten Verbinden wieder einen `refresh_token`
  ausstellt, sonst nur beim allerersten Consent).
- `GET /api/sheets/oauth/callback?code=...` → Code gegen
  Access-/Refresh-Token tauschen, `refresh_token` + `connected_at` in
  `google_sheets_settings` speichern (`upsert`), Redirect zu
  `settings.html` mit Erfolgsmeldung.
- `get_access_token()` (intern, analog zu `ebay_client.get_access_token()`,
  aber lokal statt über einen Server) tauscht den gespeicherten
  `refresh_token` bei Bedarf gegen einen frischen Access-Token —
  kein separater Proxy-Service nötig, da `webapp-poc` den Refresh-Token
  selbst hält.

### Sheets-Tab-Mapping

Portiert `write_tab()`s Schreiblogik aus
`integrations/google_sheets_sync.py` (Tab leeren, Werte schreiben,
Kopfzeile einfrieren) unverändert, aber die Datenherkunft wechselt von
SQLite-Queries auf Supabase-`select("*")`-Aufrufe über `db.py`:

- **Karten** — alle `cards`-Felder (gleiche Reihenfolge/Label wie
  `card.html`s `FIELDS`-Array plus `id`, `recognition_status`,
  `created_at`).
- **Käufe** — alle `purchases`-Felder plus eine berechnete Spalte
  "Anzahl Karten" (Anzahl verknüpfter `purchase_items`-Zeilen pro
  Kauf).
- **eBay** — `ebay_listings`-Felder (Titel, Preis, Status,
  `scheduled_at`) plus, falls vorhanden, die verknüpften
  `ebay_sales`-Felder (`sale_date`, `gross_price`) aus einem Join über
  `listing_id`.
- **Sync_Info** — `Quelle` ("DCardsLab Supabase"), `Synchronisiert`
  (aktueller Zeitstempel), `Richtung` ("Supabase → Google Sheets"),
  `Hinweis` (identischer Warnhinweis wie in der Desktop-App: Supabase
  ist die Master-Datenbank, Sheets ist die externe Auswertungsansicht).

### Backup-ZIP

`backup.py`s `build_backup_zip()` (reine Funktion, kein HTTP):
1. Für jede der sechs Tabellen: `db`-Lesefunktion aufrufen, als
   `<tabelle>.json` in die ZIP schreiben (`json.dumps(rows, ensure_ascii=False,
   default=str)` — `default=str` deckt `datetime`/`Decimal`-Werte ab,
   die der Supabase-Client zurückgibt).
2. Für jede Karte mit `front_image_path`/`back_image_path`: Bild-Bytes
   über `get_client().storage.from_(BUCKET).download(object_path)`
   herunterladen, unter demselben Pfad (`images/<object_path>`) in die
   ZIP schreiben.
3. ZIP-Datei-Objekt (in-memory `io.BytesIO`, kein Temp-Datei-Handling
   nötig — Kartenbestand ist klein genug für diese persönliche
   Sammlung, s. [Explizit außerhalb dieses Scopes](#explizit-außerhalb-dieses-scopes))
   zurückgeben.

Synchron innerhalb eines HTTP-Requests (wie `POST /api/ebay/sync-sales`)
— bei vielen hundert Bildern kann das einen Moment dauern, das
Frontend zeigt währenddessen einen Statustext, kein Fortschrittsbalken
nötig für diese Sammlungsgröße.

## API-Endpoints (`webapp-poc/main.py`)

### `GET /api/sheets/status` (neu)

`{"connected": bool, "spreadsheet_id": str, "connected_at": str|null,
"last_synced_at": str|null}` — liest `google_sheets_settings`, `connected`
ist `true`, sobald ein `refresh_token` gespeichert ist.

### `GET /api/sheets/oauth/start` (neu)

Redirect zu Googles OAuth-Consent-URL (s. o.).

### `GET /api/sheets/oauth/callback` (neu)

Tauscht `code`, speichert Token, Redirect zu `/settings.html`.
Google-Fehler (`error`-Query-Param, z. B. bei Nutzer-Ablehnung) →
Redirect zu `/settings.html?sheets_error=...`, `settings.html` zeigt
die Meldung aus dem Query-Param.

### `POST /api/sheets/settings` (neu)

Body: `{"spreadsheet_id": "..."}`. Speichert die Ziel-Tabellen-ID
(`upsert` auf `google_sheets_settings`). 400, falls leer.

### `POST /api/sheets/sync` (neu)

Löst den Sync aus (s. [Sheets-Tab-Mapping](#sheets-tab-mapping)).
401 mit deutscher Meldung, falls noch nicht verbunden
(`connected=false`). 400, falls keine `spreadsheet_id` hinterlegt ist.
Erfolg: `{"synced_at": "<iso8601>"}`, aktualisiert
`last_synced_at`. Google-API-Fehler → 502 mit Googles Fehlertext (wie
bei `ebay_client.EbayApiError`).

### `GET /api/backup` (neu)

Erzeugt die Backup-ZIP synchron, gibt sie als
`application/zip`-Download zurück (`Content-Disposition: attachment;
filename="dcardslab-backup-<YYYY-MM-DD>.zip"`).

## UI-Design

### `static/settings.html` (neu)

- **Google Sheets**-Bereich: Verbindungsstatus (`GET
  /api/sheets/status`) — "Nicht verbunden" mit Button "Mit Google
  verbinden" (Link auf `/api/sheets/oauth/start`) oder "Verbunden seit
  \<Datum\>" mit Eingabefeld für die Tabellen-ID (vorbelegt, falls
  bereits gesetzt) + Button "Speichern" (`POST /api/sheets/settings`)
  und Button "Jetzt synchronisieren" (`POST /api/sheets/sync`, zeigt
  `Zuletzt synchronisiert: <Zeit>` nach Erfolg).
- **Backup**-Bereich: Button "Backup herunterladen" — löst
  `window.location.href = "/api/backup"` aus (Browser lädt die ZIP
  direkt herunter, kein `fetch()`/Blob-Handling nötig für einen reinen
  Download).
- Link von `cards.html` auf `settings.html` (analog zu den bestehenden
  Links auf `purchases.html`/`ebay.html`).

## Fehlerbehandlung

- `POST /api/sheets/sync` ohne Verbindung: 401, "Google Sheets ist
  nicht verbunden — bitte zuerst auf der Einstellungen-Seite
  verbinden."
- `POST /api/sheets/sync` ohne hinterlegte Tabellen-ID: 400, "Bitte
  zuerst eine Google-Sheets-Tabellen-ID hinterlegen."
- Google-API-Fehler (ungültige Tabellen-ID, fehlende Berechtigung):
  502 mit Googles Rohtext, wie bei bestehenden eBay-Fehlern.
- `GET /api/sheets/oauth/callback` bei vom Nutzer abgelehntem Consent:
  Redirect mit Fehlermeldung statt hartem 500.
- `GET /api/backup`: ein einzelnes fehlschlagendes Bild (Storage-
  Hiccup) darf den gesamten Download nicht abbrechen — analog zum
  bestehenden `_attach_signed_urls()`-Muster wird ein fehlendes Bild
  übersprungen (nicht in der ZIP), keine Exception nach außen.

## Tests

- `tests/test_webapp_poc_google_sheets_client.py` (neu) — OAuth-URL-
  Bau, Code-Tausch/Refresh (gemockte `httpx.request`-Aufrufe, gleiche
  Mock-Tiefe wie `tests/test_webapp_poc_ebay_client.py`), Tab-
  Schreiblogik (Clear-/Update-/BatchUpdate-Request-Bodies: Header-Zeile,
  Datenzeilen, Frozen-Row-Request).
- `tests/test_webapp_poc_backup.py` (neu) — `build_backup_zip()`:
  enthält alle sechs `<tabelle>.json`-Dateien mit den gemockten
  `db`-Rückgabewerten, enthält Bilddateien für Karten mit
  `front_image_path`/`back_image_path`, überspringt fehlschlagende
  Bild-Downloads statt zu crashen.
- `tests/test_webapp_poc_sheets_endpoints.py` (neu) — alle neuen
  `/api/sheets/*`- und `/api/backup`-Endpoints gegen `db.py`/
  `google_sheets_client.py`-Mocks: Status, OAuth-Start-Redirect,
  Callback (Erfolg + Google-Fehler-Query-Param), Settings-Update,
  Sync (401 ohne Verbindung, 400 ohne Tabellen-ID, Erfolg, 502 bei
  Google-Fehler), Backup-Download (Content-Type/-Disposition-Header).
- Ergänzungen in `tests/test_webapp_poc_db.py` für
  `get_google_sheets_settings`/`save_google_sheets_settings` und die
  sechs Tabellen-Lesefunktionen fürs Backup.
- Frontend (`settings.html`): kein JS-Test-Framework im Projekt — Live-
  Verifikation im Browser (Playwright-gestützt für Statustext/Buttons,
  wie bei Sub-Projekt 5), der eigentliche Google-OAuth-Consent-Screen
  selbst kann nur manuell mit einem echten Google-Konto durchgeklickt
  werden.

## Explizit außerhalb dieses Scopes

- Google Drive komplett (Bild-Hosting via Supabase Storage bereits
  gelöst, Backups jetzt über den Download-Button statt Drive-Upload).
- Automatischer/zeitgesteuerter Sheets-Sync — bleibt manuell per
  Button, wie in der Desktop-App.
- Zwei-Wege-Sync (Sheets → Supabase zurückschreiben) — Supabase bleibt
  einzige Master-Quelle, wie bisher.
- Automatisches/zeitgesteuertes Backup — bleibt manueller
  Download-Button.
- Backup-Wiederherstellung (Restore/Import der ZIP zurück in Supabase)
  — reine Sicherungskopie, kein Restore-Flow in diesem Sub-Projekt.
- Streaming/Chunked-ZIP-Erzeugung für sehr große Sammlungen (tausende
  Bilder) — die synchrone In-Memory-ZIP-Erzeugung reicht für die
  aktuelle, persönliche Sammlungsgröße.
