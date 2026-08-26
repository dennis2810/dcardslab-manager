# WebApp Sub-Projekt 1: Datenbank- & Backend-Fundament

Status: approved (Brainstorming abgeschlossen 2026-08-26)

## Kontext

Ziel des Gesamtprojekts: Die WebApp soll die bestehende Desktop-App
(`app/dcardlabs_manager.py`, Tkinter + SQLite) langfristig vollständig
ablösen. Das ist zu groß für eine Spec/einen Plan und wird in Sub-Projekte
zerlegt:

1. **Datenbank- & Backend-Fundament** (dieses Dokument) – Scan-Ergebnisse
   und Karten-Stammdaten werden tatsächlich persistiert, statt wie im
   validierten PoC nur einmalig als JSON zurückgegeben zu werden.
2. Inventar-Verwaltung im Web-Frontend (Liste, Bearbeiten, Suche/Filter)
3. Käufe/Purchases
4. eBay-Integration (Listing-Erstellung, Export, Sales-Sync) – Anbindung
   an den bestehenden `ebay-oauth-server`
5. Google Drive/Sheets-Sync, Backups

Jedes Sub-Projekt bekommt seine eigene Spec → Plan → Umsetzung. Dieses
Dokument deckt ausschließlich Sub-Projekt 1 ab.

Ausgangslage:

- `webapp-poc/main.py` (FastAPI) wurde bereits mit echten Kartenscans
  validiert: Upload → Zuschnitt (`scanner/scanner_v0_8_dynamic.py`,
  unverändert) → Erkennung (`integrations/ai_card_recognition.py`,
  Claude Vision, unverändert) → JSON-Response. Persistiert nichts.
- `ebay-oauth-server/app.py` (Flask, Port 8080) läuft bereits produktiv
  auf dem NAS für eBay-OAuth/Sell-API und bleibt unangetastet.
- Die Desktop-App nutzt aktuell SQLite (`app/dcardlabs_manager.py`,
  Schema-Konstante `SCHEMA`) mit Tabellen `cards`, `inventory`,
  `purchases`, `purchase_items`, `scan_batches`, `ebay_settings`,
  `ebay_listings`, `ebay_sales`. Für Bild-URLs im eBay-Listing lädt sie
  Kartenbilder heute über Google Drive hoch (`google_drive_sync.py`),
  nur um eine öffentliche URL für das eBay-Pflichtfeld `PicURL` zu
  bekommen.
- In der SQLite-DB liegen aktuell keine echten Bestandsdaten, die
  migriert werden müssten – die neue DB startet leer.

## Nutzung/Zugriff (Rahmenbedingung)

Primär ein Nutzer, Zugriff ausschließlich über Tailscale (wie der
bestehende `ebay-oauth-server`). Öffentlicher Zugriff/mehrere Nutzer ist
eine mögliche spätere Erweiterung, aber kein Ziel dieses Sub-Projekts –
das Design soll das nicht verbauen (Supabase Auth existiert bereits als
Option), muss es aber jetzt nicht umsetzen.

## Architektur

```
Browser/Handy
   │  multipart/form-data (front + back Scanbogen)
   ▼
webapp-poc (FastAPI, Port 8000, wird zum echten Backend)
   │
   ├─ scanner_v0_8_dynamic.process()      (unverändert) – 9-up-Zuschnitt
   ├─ ai_card_recognition.recognize_card() (unverändert) – Claude Vision
   ├─ Bild-Kompression (Pillow) + Upload   → Supabase Storage (Bucket "card-images")
   └─ Persistenz                          → Supabase Postgres (scan_batches, cards)
   │
   ▼
JSON-Response: gespeicherte Karten inkl. id + signierter Bild-URLs
```

`ebay-oauth-server` bleibt ein separater, unveränderter Service. Die
WebApp spricht ihn – wenn das relevant wird (Sub-Projekt 4) – per HTTP
an, genau wie es die Desktop-App heute tut. Kein Zusammenführen der
beiden Services.

**Warum FastAPI-Ausbau statt Zusammenführen mit `ebay-oauth-server`:**
Der eBay-Server ist bereits produktiv gehärtet (Token-Refresh, Business
Policies, eBay-Fehlerbehandlung) – das Risiko, ihn beim Verschmelzen zu
beschädigen, überwiegt den Vorteil eines einzigen Service. FastAPI ist
zudem async-fähig (parallele DB-/Storage-Calls, parallele
Claude-Vision-Calls wie im PoC schon per `ThreadPoolExecutor`) und
liefert automatisch eine OpenAPI-Spec, die dem künftigen Frontend nützt.

## Datenmodell (Supabase Postgres)

Bewusst schlank – nur was Sub-Projekt 1 braucht. Inventar-/Kauf-/eBay-
Felder kommen mit den jeweiligen späteren Sub-Projekten als eigene
Tabellen dazu (kein Vorgriff, kein Vorrats-Schema).

```sql
create table scan_batches (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now(),
    status      text not null,       -- 'ok' | 'partial' | 'failed'
    card_count  int not null default 0
);

create table cards (
    id                  uuid primary key default gen_random_uuid(),
    batch_id            uuid references scan_batches(id),
    position_in_batch   int not null,   -- 1..9, Rasterposition im Scanbogen

    title               text default '',
    category            text default '',
    theme               text default '',
    manufacturer        text default '',
    set_name            text default '',
    season_year         text default '',
    card_type           text default '',
    variant             text default '',
    team                text default '',
    position            text default '',
    squad_number        text default '',
    club_debut_season   text default '',
    card_number         text default '',
    serial_number       text default '',
    print_run           text default '',
    is_numbered         boolean not null default false,
    confidence          numeric,
    recognition_status  text,           -- 'ok' | 'prüfen' | 'nicht erkannt' | Fehlertext

    front_image_path    text,           -- Objekt-Key im Storage-Bucket
    back_image_path     text,

    created_at          timestamptz not null default now()
);
```

Feldnamen/-typen entsprechen 1:1 dem, was `recognize_card()` in
`integrations/ai_card_recognition.py` heute zurückgibt (`EMPTY_FIELDS` /
`CardRecognition`-Pydantic-Modell) – keine Umbenennung, kein OCR-Ballast
aus dem alten SQLite-Schema (`ocr_*`-Spalten, doppelte
Inventar-Felder auf `cards`).

## Bild-Storage

- Ein Supabase-Storage-Bucket `card-images`, Objekt-Struktur:
  `{batch_id}/{position}_front.jpg`, `{batch_id}/{position}_back.jpg`.
- **Kompression vor Upload:** mit Pillow (bereits Dependency von
  `ai_card_recognition.py`) auf max. Kantenlänge ~1600px verkleinern,
  JPEG-Qualität ~85 % – spart deutlich Storage gegenüber den
  97%-Scanner-Originalen, für Web-Anzeige und eBay-Fotos mehr als
  ausreichend.
- **Bucket ist privat** (nicht öffentlich lesbar). Das Backend erzeugt
  bei Bedarf zeitlich begrenzte signierte URLs (Supabase
  `create_signed_url`). Das funktioniert auch für eBay-Listings
  (Sub-Projekt 4): eBay ruft die übergebene Bild-URL beim Anlegen des
  Listings einmalig ab und hostet das Bild danach selbst weiter (eBay
  Picture Services) – die URL muss also nur kurz gültig sein, nicht
  dauerhaft.

## API-Endpoints

- `POST /api/scan` (bestehender Endpoint, Verhalten geändert): nimmt
  Front-/Back-Scanbogen entgegen, schneidet + erkennt wie bisher, legt
  jetzt zusätzlich einen `scan_batches`-Eintrag an, komprimiert und lädt
  alle 18 Bilder in den Storage-Bucket, schreibt 9 `cards`-Zeilen. Gibt
  die gespeicherten Karten inkl. `id` und signierter Bild-URLs zurück
  statt wie bisher nur die rohen Erkennungsergebnisse.
- `GET /api/cards` (neu): Liste aller gespeicherten Karten. Dient in
  diesem Sub-Projekt primär zur Verifikation, dass Persistenz
  funktioniert (kein UI); wird die Datengrundlage für die Inventarliste
  in Sub-Projekt 2.
- `GET /api/cards/{id}` (neu): Einzelne Karte inkl. frisch signierter
  Bild-URLs (falls die ursprünglichen abgelaufen sind).

Kein PATCH/Edit-Endpoint in diesem Sub-Projekt – Korrektur von
Fehlerkennungen ist Aufgabe der Inventar-UI (Sub-Projekt 2). Scope hier:
zuverlässig speichern, was gescannt wurde.

## Auth / Secrets

- Backend spricht Supabase über den **Service-Role-Key** an
  (Server-zu-Server, nie im Browser). Neue Env-Variablen `SUPABASE_URL`
  und `SUPABASE_SERVICE_KEY`, neben dem bereits vorhandenen
  `ANTHROPIC_API_KEY`.
- Kein App-seitiges Nutzer-Login in diesem Sub-Projekt. Schutz weiterhin
  ausschließlich über die Tailscale-Netzwerkgrenze, wie beim
  bestehenden `ebay-oauth-server`. Supabase Auth ist als Option für
  "später öffentlich" vorhanden, wird hier nicht aktiviert.

## Fehlerbehandlung

- `recognize_card()` liefert bei Fehlern (kein Internet, API-Fehler)
  weiterhin leere Felder + Statustext statt zu crashen (unverändertes
  Verhalten). Dieser Zustand wird genauso persistiert – die Karte landet
  mit `recognition_status = 'nicht erkannt'` (bzw. dem Fehlertext) in
  der DB, nichts geht verloren.
- Schlägt der **Bild-Upload** zu Supabase für eine einzelne Karte fehl,
  wird das nur für diese eine Karte als Fehler im Response markiert; der
  Rest des Batches läuft trotzdem durch (kein Alles-oder-Nichts pro
  Batch).

## Tests

- Unit-/Integrationstests laufen wie bisher gegen echten Code; der
  Supabase-Client wird gemockt (kein echter Netzwerk-Call in CI) – analog
  zum bestehenden Muster, das Claude Vision und Tkinter in den 28
  vorhandenen CI-Tests stubbt.
- Neue Testfälle: Batch anlegen + 9 Karten korrekt schreiben,
  Bild-Pfad-Zuordnung (Position ↔ Datei), Fehlerpfad "Upload schlägt für
  eine Karte fehl" (Rest des Batches läuft weiter, Fehlerkarte markiert).

## Deployment

- Bleibt wie beim validierten PoC: **ein** Docker-Container auf dem NAS,
  Port 8000, Build-Kontext Repo-Root (damit `scanner/` und
  `integrations/` unverändert mitkopiert werden).
- `docker-compose.webapp-poc.yml` bekommt die beiden neuen
  Supabase-Env-Variablen zusätzlich zu `ANTHROPIC_API_KEY`.
- Das Supabase-Projekt wird einmalig manuell angelegt (kostenloser
  Tier: 500 MB DB, 1 GB Storage – für Kartendaten + komprimierte Bilder
  ausreichend für den Start). Schema (`scan_batches`, `cards`,
  Storage-Bucket `card-images`) wird als SQL-Migration mitgeliefert und
  dort einmalig eingespielt.
- Bekannte Free-Tier-Einschränkung: Supabase-Projekte pausieren nach 1
  Woche Inaktivität (manuell reaktivierbar) – für "primär ich, gelegentlich
  genutzt" ein akzeptabler Trade-off, kein Blocker.

## Explizit außerhalb dieses Scopes

- Inventar-UI, Bearbeiten/Korrigieren von Karten, Suche/Filter
  (Sub-Projekt 2).
- Käufe/Purchases (Sub-Projekt 3).
- eBay-Listing-Erstellung/-Export/-Sales-Sync (Sub-Projekt 4) – die
  Storage-Grundlage (signierte URLs) wird hier zwar gelegt, aber nicht
  angebunden.
- Google Drive/Sheets-Sync, Backups (Sub-Projekt 5).
- Migration bestehender SQLite-Daten (nicht nötig, DB startet leer).
- Nutzer-Login/Auth, öffentlicher Zugriff (spätere Erweiterung, hier nur
  nicht verbaut).
