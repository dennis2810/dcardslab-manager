# WebApp Sub-Projekt 4: eBay-Integration

Status: Entwurf (Brainstorming mit Nutzer abgeschlossen 2026-08-27, Spec zur
Freigabe)

## Kontext

Viertes Sub-Projekt der WebApp-Migration. Sub-Projekte 1–3 sind live im
Einsatz: Karten werden gescannt, in Supabase gespeichert, lassen sich
durchsuchen/bearbeiten/löschen (`cards.html`/`card.html`) und einem Kauf
zuordnen (`purchases.html`/`purchase.html`). Was fehlt: **eine Karte bei
eBay als Angebot einstellen und den Verkauf zurückverfolgen.**

Die Desktop-App (`app/dcardlabs_manager.py`) hat dafür ein bewährtes
Datenmodell (`ebay_listings`, `ebay_sales`, `ebay_settings`) und nutzt einen
separaten Flask-Service, `ebay-oauth-server` (`ebay-oauth-server/app.py`),
der die eBay-OAuth-Autorisierung hält und Sell-Inventory-API-Aufrufe
(Inventory Item, Offer, Publish, Policies, Orders) kapselt. Dieser Service
**läuft bereits als eigener Container auf Port 8080** auf dem NAS,
unverändert von diesem Sub-Projekt weiterverwendet.

## Brainstorming-Entscheidungen

- **Architektur:** Business-Logik (Titel/Beschreibung generieren,
  Inventory-Item/Offer aus einer Karte bauen, Publish, Policy-Auflösung,
  Sales-Sync) lebt in `webapp-poc` (FastAPI) — dort liegen bereits alle
  Kartendaten. `ebay-oauth-server` bleibt ein reiner Token-Proxy plus
  seine bestehenden Setup-Hilfsfunktionen (OAuth-Flow,
  `/api/ebay/policies/bootstrap`); er bekommt nur einen neuen internen
  Endpoint, der `webapp-poc` einen gültigen Access-Token liefert (siehe
  [Neuer Endpoint in `ebay-oauth-server`](#neuer-endpoint-in-ebay-oauth-server)).
- **Umgebung:** Entwicklung/Test zuerst gegen die eBay-Sandbox
  (`EBAY_ENVIRONMENT=sandbox`, so wie der Server aktuell auf dem NAS
  konfiguriert ist). Umschalten auf Produktion ist ein reiner
  Config-Wechsel (env vars) nach Freigabe, kein Code-Unterschied.
- **Scope:** Listing-Erstellung + Publish (inkl. **Mehrfachauswahl und
  gebündeltes Veröffentlichen mehrerer Entwürfe auf einmal**) und
  Sales-Sync. CSV-Export (wie in `docs/EBAY_IMPORT.md` für die Desktop-App
  beschrieben) ist **optional und explizit außerhalb dieses Scopes** — die
  Live-API ist jetzt sinnvoll nutzbar, weil Supabase Storage (anders als
  lokale Dateipfade der Desktop-App) öffentliche Bild-URLs liefern kann.
- **Bild-Hosting:** `card-images`-Bucket wird public-read gestellt (siehe
  [Bild-URLs](#bild-urls-für-ebay)) statt bei jedem Publish eine langlebige
  signierte URL zu erzeugen — einfacher und eBay braucht die URL nicht nur
  beim Publish, sondern auch danach, wenn eBay das Angebot periodisch neu
  crawlt/anzeigt.

## Ziel

- Aus einer Karte heraus (`card.html`) ein eBay-Angebot als **Entwurf**
  anlegen: Titel/Beschreibung vorgeschlagen (editierbar), Preis, Menge,
  Zustand, Kategorie — analog zum bewährten Desktop-Flow.
- Entwürfe in einer eigenen Übersicht (`ebay.html`) sehen, filtern
  (Status), bearbeiten, löschen.
- Einzelne Entwürfe **oder mehrere per Checkbox-Auswahl auf einmal**
  veröffentlichen (Inventory Item + Offer anlegen, Business Policies
  auflösen, Publish auslösen) — mit klarer Fehleranzeige pro Karte, falls
  einzelne Angebote scheitern (fehlende Policy, eBay-Validierungsfehler),
  ohne dass erfolgreiche Veröffentlichungen in derselben Aktion verloren
  gehen.
- Verkäufe von eBay abrufen (**Sales-Sync**) und automatisch mit dem
  passenden Angebot/der passenden Karte verknüpfen, statt sie wie in der
  Desktop-App manuell einzutippen.
- OAuth-Verbindungsstatus zum `ebay-oauth-server` sichtbar machen
  (autorisiert ja/nein, Umgebung), damit klar ist, warum ein Publish
  scheitert, falls der Sandbox/Produktions-Token fehlt oder abgelaufen ist.

## Datenmodell

Zwei neue Tabellen in `supabase/schema.sql`, UUID-PKs wie die bestehenden
Tabellen:

```sql
create table if not exists ebay_listings (
    id               uuid primary key default gen_random_uuid(),
    card_id          uuid not null unique references cards(id) on delete cascade,
    sku              text not null unique,
    title            text default '',
    description      text default '',
    condition        text default 'NM',
    condition_id     text default '4000',
    category_id      text default '261328',
    price            numeric default 0,
    quantity         int default 1,
    status           text not null default 'Entwurf',
        -- 'Entwurf' | 'Veroeffentlicht' | 'Verkauft' | 'Fehler'
    ebay_offer_id    text default '',
    ebay_listing_id  text default '',
    last_error       text default '',
    published_at     timestamptz,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index if not exists ebay_listings_status_idx on ebay_listings(status);

create table if not exists ebay_sales (
    id                uuid primary key default gen_random_uuid(),
    listing_id        uuid references ebay_listings(id) on delete set null,
    card_id           uuid references cards(id) on delete set null,
    ebay_order_id     text not null,
    ebay_line_item_id text default '',
    sale_date         timestamptz,
    quantity          int default 1,
    gross_price       numeric default 0,
    shipping_charged  numeric default 0,
    ebay_fees         numeric default 0,
    net_amount        numeric default 0,
    notes             text default '',
    created_at        timestamptz not null default now(),
    unique (ebay_order_id, ebay_line_item_id)
);

create index if not exists ebay_sales_listing_id_idx on ebay_sales(listing_id);
create index if not exists ebay_sales_card_id_idx on ebay_sales(card_id);
```

**`ebay_listings.card_id unique`:** wie in der Desktop-App höchstens ein
Angebot pro Karte — deckt den realistischen Fall ab und hält
`card.html` einfach (ein `GET /api/cards/{id}` liefert höchstens ein
Angebot, keine Liste). Ein Angebot erneut einstellen, nachdem es beendet
wurde, erfordert in diesem Fall: bestehenden Entwurf bearbeiten und erneut
veröffentlichen (kein zweiter Datensatz).

**`sku`:** eBays Sell-Inventory-API braucht eine verkäuferseitig vergebene,
eindeutige SKU pro Inventory Item. Generiert als `webapp-{card_id}` beim
Anlegen des Entwurfs — der Präfix vermeidet Kollisionen, falls dieselbe
eBay-Produktionsumgebung später auch von der Desktop-App aus bespielt
wird (deren SKU-Schema ist unbekannt/undokumentiert im bestehenden Code).

**`ebay_sales.unique(ebay_order_id, ebay_line_item_id)`:** ein Sales-Sync
kann wiederholt über denselben Zeitraum laufen (z. B. täglich per Button),
ohne Duplikate anzulegen — ein erneuter Sync macht ein `upsert` auf diesen
Schlüssel.

### Abweichungen vom Desktop-Modell

- **Keine `ebay_settings`-Tabelle mit UI zum Bearbeiten.** Nur eine
  Kategorie wird praktisch genutzt (Trading Card Einzelkarten, `261328`).
  Default-Kategorie/Zustands-IDs sind Konstanten in `webapp-poc` (analog zu
  `_CONDITION_ID_TO_ENUM` in `ebay-oauth-server/app.py`), pro Angebot
  überschreibbar über `category_id`/`condition_id` auf `ebay_listings`
  selbst. Spart eine Tabelle + eigene Verwaltungsseite für einen Wert, der
  in der Praxis nie geändert wird.
- **Keine redundanten `sold_at`/`sale_price`/`ebay_fees`/`ebay_order_id`
  auf `ebay_listings`.** Die Desktop-App pflegt diese doppelt (einmal auf
  `ebay_listings`, einmal auf `ebay_sales`). Hier ist `ebay_sales` die
  einzige Quelle der Wahrheit für Verkaufsdaten — `ebay_listings.status`
  wird beim Sync auf `'Verkauft'` gesetzt, Detailwerte kommen ausschließlich
  aus `ebay_sales` (analog zur Vereinfachung bei `purchases`/`cards` in
  Sub-Projekt 3).
- **Kein `template_key`/CSV-Exportfelder** (`exported_at`, `scheduled_at`)
  — CSV-Export ist optional/außerhalb dieses Scopes (s. o.).
- **Angebot beenden (`withdrawOffer`) ist nicht Teil dieses Sub-Projekts** —
  s. [Explizit außerhalb dieses Scopes](#explizit-außerhalb-dieses-scopes).

## Bild-URLs für eBay

`card-images` ist aktuell ein privater Bucket, `storage.py.signed_url()`
liefert Ablauf-URLs (1h). eBay braucht dauerhaft erreichbare HTTP(S)-URLs —
nicht nur beim Publish, auch danach (eBay crawlt Angebotsbilder erneut).
Umstellung:

- Bucket `card-images` auf **public read** stellen (Supabase Dashboard:
  Storage → `card-images` → Bucket public machen, oder eine `select`-Policy
  für `anon`/`public` auf `storage.objects` dieses Buckets).
- Neue Helper-Funktion `storage.public_url(object_path)` (baut die
  öffentliche Supabase-Storage-URL ohne Signatur-Token) — ersetzt
  `signed_url()` **nur** für die eBay-Bildübergabe. `cards.html`/
  `card.html` behalten `signed_url()` unverändert, keine Notwendigkeit,
  bestehende Aufrufer anzufassen.
- Objektpfade sind bereits nicht erratbar genug für ein Sicherheitsproblem
  (`{batch_id}/{position}_{side}.jpg`, `batch_id` ist eine UUID) — dieselbe
  Einschätzung wie bei jedem anderen "Bucket public, Pfade als Capability"
  Setup. Sensible Daten (Käufe, Preise) bleiben in Postgres mit RLS/Service
  Key, nicht im Storage-Bucket.

## Architektur

```
webapp-poc/
    ebay_client.py   (neu) - HTTP-Client für eBay Sell-API (Inventory Item,
                              Offer, Publish, Business Policies, Orders) +
                              Access-Token vom oauth-server holen
    ebay_listing.py  (neu) - Titel/Beschreibung generieren, SKU/Preis-Logik,
                              Sales-Sync-Matching (kein HTTP, reine Funktionen
                              -> einfach testbar ohne Mock-HTTP)
    db.py            (erweitert) - CRUD für ebay_listings/ebay_sales
    main.py          (erweitert) - neue /api/ebay/*-Endpoints
    static/ebay.html          (neu) - Angebots-Übersicht, Mehrfachauswahl
    static/card.html          (erweitert) - neuer "eBay"-Bereich
```

`ebay_client.py` spricht **nur** `ebay-oauth-server` (für den Token) und
direkt die eBay Sell-API (`https://api.sandbox.ebay.com` bzw. `.ebay.com`,
Basis-URL aus `EBAY_ENVIRONMENT`-Analogon, hier als Env-Var
`EBAY_ENVIRONMENT` an `webapp-poc` übergeben, muss mit dem Wert des
oauth-servers übereinstimmen — sonst passt der Token nicht zur
API-Basis-URL). Portiert die bereits in `ebay-oauth-server/app.py`
bewährte Logik 1:1 (kein Fremdgebiet, nur ins neue Modul kopiert und an
`httpx` statt `urllib` angepasst, da `webapp-poc` schon `httpx` nutzt):

- `condition_id_to_enum()` (Mapping Zustands-ID → eBay-`ConditionEnum`)
- `get_listing_policies()` (liest bestehende Fulfillment-/Payment-/
  Return-Policy-IDs für eine Marketplace-ID — **erstellt keine**, das
  bleibt der einmalige manuelle/`policies/bootstrap`-Schritt gegen den
  oauth-server, unverändert dokumentiert in `ebay-oauth-server/README.md`)

### Neuer Endpoint in `ebay-oauth-server`

```
GET /api/internal/access-token
-> {"access_token": "...", "environment": "sandbox", "expires_in": 7200}
```

Nutzt intern `refresh_access_token()`/`load_token()` (bereits vorhanden),
liefert nur einen frischen Token statt einer HTML-Seite. Kein
Autorisierungs-Header/Secret auf diesem Endpoint — konsistent mit allen
bestehenden Endpoints des Servers, der ausschließlich im
Tailscale-internen NAS-Netz erreichbar ist (kein öffentlicher Ingress,
gleiche Vertrauensannahme wie z. B. `/api/ebay/policies/bootstrap` heute
schon). 401 mit `{"authorized": false}`, falls kein Token hinterlegt ist
(OAuth-Flow noch nicht durchlaufen) — `webapp-poc` gibt das als klare
deutsche Fehlermeldung weiter, statt eines rohen eBay-Fehlers.

`webapp-poc` bekommt eine neue Env-Var `EBAY_OAUTH_SERVER_URL` (Default
`http://ebay-oauth-server:8080` bzw. die NAS-interne Adresse — gleiches
Muster wie `DCARDSLAB_EBAY_SERVER_URL` in der Desktop-App).

## API-Endpoints (`webapp-poc/main.py`)

### `POST /api/cards/{card_id}/ebay-listing` (neu)

Legt einen Angebots-Entwurf für eine Karte an (404, falls Karte nicht
existiert; 409, falls bereits ein Angebot existiert — dann `PATCH`
verwenden). Body optional: `title`, `description`, `price`, `quantity`,
`condition`, `condition_id`, `category_id` — jedes fehlende Feld wird aus
den Kartendaten vorgeschlagen (`ebay_listing.generate_title(card)`/
`generate_description(card)`, portiert von
`ebay_generate_title`/`ebay_generate_description` in
`app/dcardlabs_manager.py`, an die webapp-Feldnamen angepasst). Gibt den
angelegten Entwurf zurück.

### `GET /api/ebay/listings` (neu)

Query-Parameter `status` (optional, exakter Match), `q` (Freitext über
`title`, wie bei `GET /api/cards`). Liste sortiert nach `updated_at desc`,
jeder Eintrag inkl. Karten-Kurzinfo (`title`, `front_image_url`) für die
Übersicht.

### `GET /api/ebay/listings/{id}` (neu)

Ein Angebot inkl. Karten-Kurzinfo. 404 bei unbekannter ID.

### `PATCH /api/ebay/listings/{id}` (neu)

Aktualisiert die Angebots-Felder. **Ist der Status bereits
`'Veroeffentlicht'`,** wird nach dem Speichern automatisch ein erneuter
Publish-Call ausgelöst (Inventory Item + Offer aktualisieren, `publish`
erneut aufrufen) — eBay übernimmt Preis-/Beschreibungsänderungen an einem
bereits aktiven Angebot nur so. Schlägt dieser Re-Publish fehl, bleiben die
neuen Feldwerte in der DB gespeichert, `status` wechselt auf `'Fehler'`
mit `last_error` (kein Rollback der Bearbeitung nötig — der Nutzer sieht
den Fehler und kann erneut veröffentlichen).

### `DELETE /api/ebay/listings/{id}` (neu)

Nur erlaubt, solange `status = 'Entwurf'` (409 mit deutscher
Fehlermeldung sonst — ein bereits bei eBay live stehendes Angebot lässt
sich hier bewusst nicht "verschwinden lassen", s.
[Explizit außerhalb dieses Scopes](#explizit-außerhalb-dieses-scopes)).
Löscht nur den Entwurf, nicht die Karte.

### `POST /api/ebay/listings/{id}/publish` (neu)

Veröffentlicht **ein** Angebot: Business Policies auflösen (Fehler mit
deutscher Meldung, falls eine fehlt — Text unverändert aus
`get_listing_policies()`), Inventory Item anlegen/aktualisieren (`PUT
/sell/inventory/v1/inventory_item/{sku}`, Bild-URL aus
`storage.public_url()`), Offer anlegen (falls `ebay_offer_id` leer) oder
aktualisieren, `publish` aufrufen. Bei Erfolg: `status='Veroeffentlicht'`,
`ebay_offer_id`/`ebay_listing_id`/`published_at` gesetzt, `last_error`
geleert. Bei Fehler: `status='Fehler'`, `last_error` mit der
eBay-Fehlermeldung, HTTP 502 an den Client mit derselben Meldung.

### `POST /api/ebay/listings/publish-bulk` (neu)

Body: `{"listing_ids": ["...", "..."]}`. Veröffentlicht jedes Angebot
**unabhängig** (kein Alles-oder-nichts wie bei
`POST /api/purchases` — hier sind es unabhängige eBay-API-Aufrufe, ein
einzelner eBay-Validierungsfehler bei Karte X soll die erfolgreiche
Veröffentlichung von Karte Y nicht verhindern). Antwort:

```json
{
  "results": [
    {"listing_id": "...", "status": "Veroeffentlicht"},
    {"listing_id": "...", "status": "Fehler", "error": "..."}
  ]
}
```

Immer HTTP 200 (Fehler stehen pro Zeile im Body) — das Frontend zeigt eine
Ergebnisliste statt eines einzelnen Fehler-Alerts.

### `GET /api/ebay/oauth/status` (neu, Proxy)

Reicht `GET {EBAY_OAUTH_SERVER_URL}/api/oauth/status` durch (bestehender
Endpoint, unverändert). Vermeidet, dass das Frontend eine zweite
Host/Port-Kombination kennen muss — `ebay.html` spricht ausschließlich
`webapp-poc`.

### `POST /api/ebay/sync-sales` (neu)

Holt Bestellungen von eBay (`GET /sell/fulfillment/v1/order`, Filter
`creationdate` seit dem letzten erfolgreichen Sync — Zeitpunkt wird nicht
separat gespeichert, sondern als `max(ebay_sales.created_at)` ermittelt;
beim allerersten Sync die letzten 90 Tage, eBays praktikables Maximum für
diesen Endpoint). Für jede Bestellzeile: `sku` gegen `ebay_listings.sku`
matchen; kein Treffer → Zeile wird übersprungen (Bestellung stammt nicht
aus einem über die WebApp veröffentlichten Angebot) und im
Antwort-Objekt unter `skipped` gezählt. Treffer → `ebay_sales`-Zeile
per `upsert` auf `(ebay_order_id, ebay_line_item_id)` anlegen/aktualisieren,
zugehöriges `ebay_listings.status` auf `'Verkauft'` setzen. `ebay_fees`
bleibt `0` (eBays Fulfillment-API liefert keine Verkaufsgebühren — dafür
wäre die separate Finances-API mit eigenem OAuth-Scope nötig, s.
[Explizit außerhalb dieses Scopes](#explizit-außerhalb-dieses-scopes));
manuell nachtragbar über eine spätere `PATCH`-Erweiterung, falls gewünscht
— hier zunächst nur lesbar in der UI. Antwort:
`{"synced": <n>, "skipped": <n>}`.

## UI-Design

### `card.html` — neuer "eBay"-Bereich

Analog zum bestehenden "Kauf"-Bereich, unterhalb davon:

- **Kein Angebot vorhanden:** einklappbarer Bereich "eBay-Angebot
  erstellen" — Titel/Beschreibung vorausgefüllt (aus
  `POST /api/cards/{id}/ebay-listing` ohne Body, das Backend generiert),
  Felder editierbar, Preis/Menge/Zustand-Auswahl, **"Als Entwurf
  speichern"**.
- **Entwurf vorhanden:** Felder editierbar (`PATCH`), Buttons
  **"Veröffentlichen"** (`POST .../publish`) und **"Entwurf löschen"**
  (`DELETE`, mit `confirm()`).
- **Veröffentlicht:** schreibgeschützte Kernfelder (Titel, Preis, Zustand)
  mit **"Bearbeiten"**-Umschalter (löst beim Speichern automatisch den
  Re-Publish aus, s. o.), Link **"Auf eBay ansehen"**
  (`https://www.ebay{.sandbox}.de/itm/{ebay_listing_id}` je nach
  `EBAY_ENVIRONMENT`).
- **Verkauft:** wie Veröffentlicht, zusätzlich schreibgeschützte
  Verkaufsdaten aus `ebay_sales` (Verkaufsdatum, Bruttopreis, Versand,
  Netto).
- **Fehler:** `last_error` sichtbar, **"Erneut veröffentlichen"**-Button.

### `ebay.html` — Angebots-Übersicht (neu)

- OAuth-Status-Banner oben (`GET /api/ebay/oauth/status`): "Verbunden
  (Sandbox)" / "Nicht verbunden — OAuth-Flow im eBay-Server starten"
  (Link auf `{EBAY_OAUTH_SERVER_URL}/ebay/oauth/start`, öffnet in neuem
  Tab).
- Status-Filter (Entwurf/Veröffentlicht/Verkauft/Fehler) + Freitextsuche
  über Titel.
- Tabelle/Kacheln mit Checkbox pro Zeile, Thumbnail, Titel, Preis, Status.
  Klick auf eine Zeile (außerhalb der Checkbox) navigiert zu
  `card.html?id=<card_id>`.
- **"Ausgewählte veröffentlichen"**-Button (aktiv, sobald ≥1 Checkbox mit
  Status `Entwurf` oder `Fehler` markiert ist) → `POST
  /api/ebay/listings/publish-bulk`, danach Ergebnisliste (pro Karte
  ✓/✗ mit Fehlertext) unterhalb der Tabelle, Tabelle neu laden.
- **"Verkäufe synchronisieren"**-Button → `POST /api/ebay/sync-sales`,
  zeigt `{synced} neue/aktualisierte Verkäufe, {skipped} ohne Treffer`
  als Statusmeldung, Tabelle neu laden (Status-Änderungen zu `Verkauft`
  sichtbar machen).

## Fehlerbehandlung

- Fehlende OAuth-Autorisierung (`ebay-oauth-server` liefert 401 auf
  `/api/internal/access-token`): einheitliche deutsche Fehlermeldung
  "eBay ist nicht verbunden — bitte zuerst den OAuth-Flow abschließen.",
  sowohl bei Publish als auch bei Sales-Sync.
- Fehlende Business Policy: eBays Fehlercode 25007 wird nicht direkt
  durchgereicht — `get_listing_policies()`s bestehende, bereits deutsche
  Fehlermeldung (Text mit Link auf Seller Hub) wird verwendet.
- eBay-Validierungsfehler beim Publish (Titel zu lang, ungültige
  Kategorie/Zustand-Kombination etc.): eBays Fehlertext wird 1:1 in
  `last_error` gespeichert und angezeigt — keine Übersetzung, da diese
  Fehler variabel und nicht vorab bekannt sind (gleiche Haltung wie die
  bestehende, unverifizierte `bootstrap_listing_policies()`-Fehlerbehandlung
  im oauth-server).
- `POST /api/cards/{id}/ebay-listing` bei bereits bestehendem Angebot:
  409 mit deutscher Fehlermeldung ("Für diese Karte existiert bereits ein
  eBay-Angebot.").
- `DELETE` auf ein nicht-Entwurf-Angebot: 409 mit deutscher
  Fehlermeldung.
- Frontend zeigt Netzwerk-/Serverfehler über das bestehende
  `#status`-Muster.

## Tests

Neue Dateien unter `tests/` (Supabase-Client + eBay-HTTP gemockt, wie im
gesamten Projekt):

- `tests/test_webapp_poc_ebay_listing.py` — reine Logik in
  `ebay_listing.py` (Titel-/Beschreibungsgenerierung aus Kartendaten,
  SKU-Format, Sales-Sync-Matching von Bestellzeilen gegen `ebay_listings`).
- `tests/test_webapp_poc_ebay_client.py` — `ebay_client.py` gegen einen
  gemockten `httpx`-Transport: Token holen (inkl. 401-Fall),
  `condition_id_to_enum`, `get_listing_policies` (Erfolg + fehlende
  Policy → Fehlermeldung), Inventory-Item/Offer/Publish-Aufrufe (Request-
  Payload-Form + Fehlerpfad).
- `tests/test_webapp_poc_ebay_endpoints.py` — alle neuen `/api/ebay/*`-
  und `/api/cards/{id}/ebay-listing`-Endpoints gegen `db.py`/
  `ebay_client.py`-Mocks: Entwurf anlegen (409 bei Duplikat), Liste/Filter,
  `PATCH` (inkl. Re-Publish-Trigger bei bereits veröffentlichtem Angebot),
  `DELETE` (409 bei Nicht-Entwurf), `publish` (Erfolg/Fehler-Pfad),
  `publish-bulk` (gemischtes Ergebnis, ein Fehler blockiert die anderen
  nicht), `sync-sales` (Treffer/Skip/Idempotenz bei erneutem Sync),
  `oauth/status`-Proxy.
- Ergänzungen in `tests/test_webapp_poc_db.py` für die neuen `db.py`-
  Funktionen (`create_ebay_listing`, `get_ebay_listing`,
  `get_ebay_listing_for_card`, `list_ebay_listings`, `update_ebay_listing`,
  `delete_ebay_listing`, `upsert_ebay_sale`).
- Frontend (`ebay.html`, erweitertes `card.html`): kein JS-Test-Framework
  im Projekt (wie in Sub-Projekt 2/3) — Verifikation manuell im Browser
  gegen die Sandbox.
- **Live-Sandbox-Verifikation** (nicht automatisiert, aber notwendig vor
  "fertig"): einmal ein echtes Sandbox-Angebot über die UI veröffentlichen
  und im Sandbox Seller Hub sichtbar prüfen — die bestehenden
  `test_offer_create`/`test_orders`-Endpoints im oauth-server waren bisher
  "nicht live gegen eBay getestet" laut eigener Doku, das gilt es hier
  erstmals zu verifizieren.

## Explizit außerhalb dieses Scopes

- CSV-Export/-Import wie in `docs/EBAY_IMPORT.md` — optional, nur falls
  die Live-API sich als unzuverlässig erweist.
- Angebot manuell beenden (`withdrawOffer`) — Angebote laufen aus oder
  werden verkauft; ein aktives Angebot vorzeitig zurückziehen ist ein
  eigener, kleiner Folgeschritt.
- Mehrere Angebote pro Karte (Re-Listing-Historie) — aktuell
  `unique(card_id)`, siehe Datenmodell-Abschnitt.
- eBay-Verkaufsgebühren automatisch nachtragen (Finances API, eigener
  OAuth-Scope) — `ebay_fees` bleibt vorerst `0`.
- Automatisches Bootstrap der Business Policies als Teil des
  Publish-Flows — bleibt der bestehende, einmalige manuelle Schritt über
  `ebay-oauth-server/README.md`.
- Eigene eBay-Kategorie-/Zustands-Verwaltung in der WebApp (s.
  [Abweichungen vom Desktop-Modell](#abweichungen-vom-desktop-modell)).
- Umschalten auf Produktion (bleibt ein reiner Env-Var-Wechsel nach
  Freigabe, kein Teil dieses Sub-Projekts).
- Google Drive/Sheets-Sync, Backups (Sub-Projekt 5).
