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
- **Scheduling:** Angebote sollen sich für einen späteren Zeitpunkt planen
  lassen, damit noch Zeit bleibt, sie **in eBay selbst** zu prüfen/
  anzupassen, bevor sie live gehen. Das setzt voraus, dass eBay den
  geplanten Eintrag bereits im Seller Hub anzeigt (natives Scheduling der
  Sell-Inventory-API) — ob dieses Feld existiert, konnte in diesem Chat
  nicht verifiziert werden (Netzwerkzugriff auf `developer.ebay.com` war
  blockiert). Vorgehen: zuerst in der Sandbox verifizieren
  ([Sandbox-Spike](#sandbox-spike-natives-scheduling-verifizieren)), bei
  Nichtverfügbarkeit App-seitiger Fallback (siehe
  [Scheduling-Architektur](#scheduling)) — dann ohne eBay-seitige Vorschau,
  das wird dem Nutzer in der UI klar kommuniziert.
- **Kartentyp (Sport/Non-Sport):** wird beim Anlegen eines Entwurfs aus dem
  KI-erkannten `category`-Feld der Karte automatisch abgeleitet (bekannte
  Sportarten → Kategorie `261328` + `Sportart`-Aspekt, sonst Kategorie
  `183050` ohne `Sportart`), im Formular per Dropdown überschreibbar.
  Pflicht-Aspekte pro Kategorie werden aus den bereits im Repo
  vorhandenen eBay-CSV-Vorlagen (`templates/ebay/*.csv`) gelesen — exakt
  wie in der Desktop-App, bleibt so automatisch synchron mit eBays echten
  Vorgaben.
- **Preisvorschläge:** kein automatischer Abruf/Scraping von eBay-
  Verkaufsdaten oder 130point.com — eBays "verkaufte Preise" sind über die
  normale Sell-API nicht zugänglich (dafür bräuchte es die stark
  eingeschränkte Marketplace Insights API), und 130point.com bietet keine
  bekannte öffentliche API; automatisiertes Abrufen wäre Scraping mit
  ToS-/rechtlichem Risiko. Stattdessen nur Links zur manuellen Recherche
  (siehe UI-Design, Abschnitt `card.html`).

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
- Ein Angebot **für einen späteren Zeitpunkt planen** statt sofort zu
  veröffentlichen, mit Möglichkeit, die Planung wieder zu stornieren.
- Sport- und Non-Sport-Karten mit den jeweils **richtigen eBay-
  Pflichtfeldern** (Item Specifics/Aspects) einstellen, automatisch aus
  Kartendaten vorausgefüllt.
- Direkte Links zur **manuellen Preisrecherche** (eBay verkaufte Artikel,
  130point.com) im Entwurf, vorausgefüllt mit dem generierten Titel.

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
    listing_type     text not null default 'sport',  -- 'sport' | 'non_sport'
    category_id      text default '261328',
    aspects          jsonb default '{}'::jsonb,
    price            numeric default 0,
    quantity         int default 1,
    status           text not null default 'Entwurf',
        -- 'Entwurf' | 'Geplant' | 'Veroeffentlicht' | 'Verkauft' | 'Fehler'
    scheduled_at     timestamptz,
    scheduling_mode  text default '',  -- '' | 'native' | 'app'
    ebay_offer_id    text default '',
    ebay_listing_id  text default '',
    last_error       text default '',
    published_at     timestamptz,
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now()
);

create index if not exists ebay_listings_status_idx on ebay_listings(status);
create index if not exists ebay_listings_scheduled_at_idx
    on ebay_listings(scheduled_at) where scheduled_at is not null;

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
- **Kein `template_key`/CSV-Exportfelder** (`exported_at`) — CSV-Export ist
  optional/außerhalb dieses Scopes (s. o.). `scheduled_at` existiert hier
  wohl, aber mit anderer Bedeutung als ein reines CSV-Metadatum: es steuert
  den [Scheduling](#scheduling)-Flow.
- **Ein bereits live veröffentlichtes Angebot lässt sich nicht beenden**
  (`withdrawOffer`) — das bleibt außerhalb dieses Sub-Projekts. Eine
  **noch nicht live gegangene Planung stornieren** ist dagegen Teil davon,
  s. [Scheduling](#scheduling) — technisch ein deutlich kleinerer Eingriff
  (ein noch nicht verkäuflich sichtbares Offer zurückziehen, kein aktives
  Angebot beenden).

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
    ebay_client.py    (neu) - HTTP-Client für eBay Sell-API (Inventory Item,
                               Offer, Publish, Business Policies, Orders) +
                               Access-Token vom oauth-server holen
    ebay_listing.py   (neu) - Titel/Beschreibung generieren, SKU/Preis-Logik,
                               Sport/Non-Sport-Ableitung + Aspects aus den
                               eBay-CSV-Vorlagen, Preisrecherche-Links,
                               Sales-Sync-Matching (kein HTTP, reine
                               Funktionen -> einfach testbar ohne Mock-HTTP)
    ebay_scheduler.py (neu) - Hintergrund-Loop für App-seitiges Scheduling
                               (Fallback, s. Scheduling)
    db.py             (erweitert) - CRUD für ebay_listings/ebay_sales
    main.py           (erweitert) - neue /api/ebay/*-Endpoints, startet den
                               Scheduler-Loop beim App-Start
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

### Kartentyp-Ableitung (Sport/Non-Sport) und Pflicht-Aspekte

`ebay_listing.derive_listing_type(card)` prüft `card["category"]` gegen
eine feste, erweiterbare Liste bekannter Sportarten
(`_KNOWN_SPORTS = {"Fußball", "Basketball", "Baseball", "Eishockey",
"American Football", "Tennis", "Boxen", "Golf", "Motorsport", ...}` — nur
eine Default-Vermutung, im Formular immer überschreibbar). Treffer →
`listing_type='sport'`, `category_id='261328'`, Aspekt `Sportart` =
`card["category"]`. Kein Treffer → `listing_type='non_sport'`,
`category_id='183050'`, kein `Sportart`-Aspekt.

`ebay_listing.build_aspects(card, listing_type)` füllt weitere Aspekte aus
vorhandenen Kartenfeldern (Team, Hersteller, Set, Saison, Kartennummer —
gleiche Zuordnung wie in `ebay_sandbox_create_offer()` in
`app/dcardlabs_manager.py`, Zeilen ~2751–2760) und speichert sie im neuen
`ebay_listings.aspects`-Feld (JSON) — im Formular als einfache
Key-Value-Liste editierbar, für den Fall, dass ein Aspekt manuell
korrigiert werden muss.

`ebay_listing.required_aspects(listing_type)` liest die Pflichtfelder
(mit `*` markierte Spalten in Zeile 2) direkt aus
`templates/ebay/eBay-category-listing-template_261328.csv` (sport) bzw.
`..._non_sport.csv` (non_sport) — portiert von `_ebay_required_aspects()`
in `app/dcardlabs_manager.py`. `POST .../publish` validiert dagegen
**vor** dem eBay-Aufruf: fehlt ein Pflicht-Aspekt, HTTP 422 mit deutscher
Meldung, welche Felder fehlen — vermeidet einen unklaren eBay-Fehler nach
einem bereits angelegten Inventory Item.

### Scheduling

Zwei Modi, `ebay_listings.scheduling_mode` hält fest, welcher für ein
konkretes Angebot verwendet wurde:

- **`native`** (bevorzugt, falls verfügbar): Inventory Item + Offer werden
  **sofort** bei der Planung angelegt (nicht erst zum Zieltermin), inkl.
  eines eBay-seitigen Scheduling-Felds am Offer/Publish-Aufruf. Das Offer
  existiert damit direkt im (Sandbox-)Seller-Hub und kann dort geprüft/
  angepasst werden, bevor es zum geplanten Zeitpunkt automatisch live
  geht — genau das gewünschte Verhalten. `status='Geplant'`,
  `ebay_offer_id` ist bereits gesetzt.
- **`app`** (Fallback): Inventory Item/Offer werden **nicht** vorab
  angelegt, nur `scheduled_at`/`status='Geplant'` in der DB gespeichert.
  `ebay_scheduler.py` läuft als `asyncio`-Hintergrund-Task (gestartet in
  `main.py`s FastAPI-Startup-Hook), prüft alle 5 Minuten auf
  `status='Geplant' AND scheduling_mode='app' AND scheduled_at <= now()`
  und veröffentlicht dann ganz normal (identischer Codepfad wie ein
  manueller Publish-Aufruf). **Einschränkung, die dem Nutzer in der UI
  angezeigt wird:** vor dem tatsächlichen Publish-Zeitpunkt existiert
  nichts bei eBay, das dort geprüft/angepasst werden könnte.

Welcher Modus verwendet wird, entscheidet `ebay_client.py` anhand des
[Sandbox-Spikes](#sandbox-spike-natives-scheduling-verifizieren) — als
Konstante (`NATIVE_SCHEDULING_SUPPORTED = True/False`), nicht dynamisch
pro Aufruf ermittelt, da eine eBay-API-Fähigkeit sich nicht innerhalb
einer Session ändert.

Für `native`-Angebote läuft derselbe Hintergrund-Task zusätzlich alle
5 Minuten `GET /sell/inventory/v1/offer/{offerId}` gegen alle
`status='Geplant' AND scheduling_mode='native'`-Zeilen und setzt
`status='Veroeffentlicht'`, sobald eBay das Offer als live meldet — ohne
das müsste der Nutzer selbst auf "Aktualisieren" klicken, um den Übergang
Geplant → Veröffentlicht zu sehen.

**`POST /api/ebay/listings/{id}/unschedule`** (neu) storniert eine
Planung: bei `scheduling_mode='app'` reicht `scheduled_at=null,
status='Entwurf'`; bei `scheduling_mode='native'` wird zusätzlich das
bereits angelegte, noch nicht live gegangene Offer über
`withdrawOffer` zurückgezogen (kein aktives Angebot, s.
[Abweichungen vom Desktop-Modell](#abweichungen-vom-desktop-modell)).

#### Sandbox-Spike: natives Scheduling verifizieren

**Erste Implementierungsaufgabe** dieses Sub-Projekts, vor dem Rest der
Scheduling-Logik: ein einzelner manueller Test-Aufruf gegen die
eBay-Sandbox (z. B. testweise über den bestehenden
`ebay-oauth-server/test_offer_create`-Endpoint oder ein kurzes Skript),
ob die Offer-Ressource ein Scheduling-Feld akzeptiert und das Offer
tatsächlich als "geplant" statt sofort live im Sandbox Seller Hub
erscheint. Ergebnis wird als eine Zeile Kommentar + die Konstante
`NATIVE_SCHEDULING_SUPPORTED` in `ebay_client.py` festgehalten. Fällt der
Test negativ aus, wird direkt mit `scheduling_mode='app'` als einzigem
Modus weitergebaut (kein Blocker für den Rest des Sub-Projekts) und der
Nutzer hier im Chat informiert.

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
`condition`, `condition_id`, `listing_type`, `category_id`, `aspects` —
jedes fehlende Feld wird aus den Kartendaten vorgeschlagen:
`title`/`description` über `ebay_listing.generate_title(card)`/
`generate_description(card)` (portiert von
`ebay_generate_title`/`ebay_generate_description` in
`app/dcardlabs_manager.py`, an die webapp-Feldnamen angepasst),
`listing_type`/`category_id`/`aspects` über
`derive_listing_type(card)`/`build_aspects(card, listing_type)` (s.
[Kartentyp-Ableitung](#kartentyp-ableitung-sportnon-sport-und-pflicht-aspekte)).
Gibt den angelegten Entwurf zurück, inkl. `required_aspects(listing_type)`
als Hinweis fürs Frontend, welche Felder vor dem Publish noch fehlen
könnten.

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

Body optional: `{"scheduled_at": "<iso8601>"}`. **Ohne** `scheduled_at`
(oder mit einem Zeitpunkt in der Vergangenheit): sofortiges Publish wie
folgt. Validiert zuerst `required_aspects(listing_type)` (422 bei
fehlenden Pflichtfeldern, s.
[Kartentyp-Ableitung](#kartentyp-ableitung-sportnon-sport-und-pflicht-aspekte)),
löst Business Policies auf (Fehler mit deutscher Meldung, falls eine
fehlt — Text unverändert aus `get_listing_policies()`), legt das
Inventory Item an/aktualisiert es (`PUT
/sell/inventory/v1/inventory_item/{sku}`, Bild-URL aus
`storage.public_url()`), legt das Offer an (falls `ebay_offer_id` leer)
oder aktualisiert es, ruft `publish` auf. Bei Erfolg:
`status='Veroeffentlicht'`, `ebay_offer_id`/`ebay_listing_id`/
`published_at` gesetzt, `last_error` geleert. Bei Fehler:
`status='Fehler'`, `last_error` mit der eBay-Fehlermeldung, HTTP 502 an
den Client mit derselben Meldung.

**Mit** `scheduled_at` in der Zukunft: `status='Geplant'`,
`scheduled_at` gespeichert, `scheduling_mode` auf `'native'` oder
`'app'` gesetzt (s. [Scheduling](#scheduling)) — bei `'native'` laufen
Validierung/Policy-Auflösung/Inventory-Item/Offer-Anlage bereits jetzt,
nur `publish` bekommt den Scheduling-Parameter statt sofort live zu
gehen; bei `'app'` passiert bis zum Zieltermin nichts weiter, das
übernimmt `ebay_scheduler.py`.

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

### `POST /api/ebay/listings/{id}/unschedule` (neu)

Storniert eine laufende Planung (s. [Scheduling](#scheduling)). Nur
erlaubt bei `status='Geplant'` (409 sonst). Gibt das aktualisierte
Angebot zurück (`status='Entwurf'`).

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
  Felder editierbar, Preis/Menge/Zustand-Auswahl, **Kartentyp**-Dropdown
  (Sport/Non-Sport, vorbelegt aus `derive_listing_type()`, s.
  [Kartentyp-Ableitung](#kartentyp-ableitung-sportnon-sport-und-pflicht-aspekte)),
  Aspects als einfache Key-Value-Liste (vorausgefüllt, editierbar), Link
  **"Preis recherchieren"** öffnet zwei neue Tabs — eBay-Sold-Suche
  (`https://www.ebay.de/sch/i.html?_nkw=<title>&LH_Sold=1&LH_Complete=1`)
  und `https://130point.com/sales/?search=<title>` — beide mit dem
  generierten Titel vorausgefüllt, rein informativ, Preis bleibt manuell
  im Formular einzutragen. **"Als Entwurf speichern"**.
- **Entwurf vorhanden:** Felder editierbar (`PATCH`), zusätzlich ein
  optionales **"Veröffentlichen am"**-Datum/Uhrzeit-Feld. Ohne gesetztes
  Datum: Button **"Veröffentlichen"** (`POST .../publish` ohne Body). Mit
  gesetztem Datum: Button **"Planen"** (`POST .../publish` mit
  `scheduled_at`). Daneben **"Entwurf löschen"** (`DELETE`, mit
  `confirm()`).
- **Geplant:** schreibgeschützte Anzeige "Geplant für \<Datum/Uhrzeit\>",
  bei `scheduling_mode='native'` zusätzlich Link **"Auf eBay ansehen"**
  (Angebot existiert dort bereits im Entwurfs-/Geplant-Status), bei
  `'app'` ein Hinweistext, dass vor dem Termin nichts bei eBay sichtbar
  ist. Buttons **"Jetzt sofort veröffentlichen"** und **"Planung
  stornieren"** (`POST .../unschedule`).
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
- Status-Filter (Entwurf/Geplant/Veröffentlicht/Verkauft/Fehler) +
  Freitextsuche über Titel. Geplante Angebote zeigen zusätzlich das
  `scheduled_at`-Datum in der Zeile.
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
- Fehlende Pflicht-Aspekte beim Publish: 422 mit deutscher Meldung, welche
  Felder (aus `required_aspects(listing_type)`) noch fehlen.
- `POST .../unschedule` auf ein nicht-`Geplant`-Angebot: 409 mit
  deutscher Fehlermeldung.
- Frontend zeigt Netzwerk-/Serverfehler über das bestehende
  `#status`-Muster.

## Tests

Neue Dateien unter `tests/` (Supabase-Client + eBay-HTTP gemockt, wie im
gesamten Projekt):

- `tests/test_webapp_poc_ebay_listing.py` — reine Logik in
  `ebay_listing.py` (Titel-/Beschreibungsgenerierung aus Kartendaten,
  SKU-Format, `derive_listing_type`/`build_aspects`/`required_aspects`
  für Sport- und Non-Sport-Beispielkarten inkl. Karten mit unbekannter/
  fehlender `category`, Preisrecherche-Link-Generierung,
  Sales-Sync-Matching von Bestellzeilen gegen `ebay_listings`).
- `tests/test_webapp_poc_ebay_client.py` — `ebay_client.py` gegen einen
  gemockten `httpx`-Transport: Token holen (inkl. 401-Fall),
  `condition_id_to_enum`, `get_listing_policies` (Erfolg + fehlende
  Policy → Fehlermeldung), Inventory-Item/Offer/Publish-Aufrufe (Request-
  Payload-Form + Fehlerpfad), Scheduling-Parameter im Publish-Request nur
  bei `NATIVE_SCHEDULING_SUPPORTED=True` gesetzt.
- `tests/test_webapp_poc_ebay_scheduler.py` — `ebay_scheduler.py`: findet
  fällige `app`-geplante Angebote und veröffentlicht sie, lässt noch nicht
  fällige unangetastet, aktualisiert `native`-geplante Angebote auf
  `Veroeffentlicht`, sobald `get_offer` das meldet, Fehlerfall setzt
  `status='Fehler'` statt den Loop abzubrechen.
- `tests/test_webapp_poc_ebay_endpoints.py` — alle neuen `/api/ebay/*`-
  und `/api/cards/{id}/ebay-listing`-Endpoints gegen `db.py`/
  `ebay_client.py`-Mocks: Entwurf anlegen mit automatischer Kartentyp-
  Ableitung (409 bei Duplikat), Liste/Filter (inkl. `Geplant`), `PATCH`
  (inkl. Re-Publish-Trigger bei bereits veröffentlichtem Angebot),
  `DELETE` (409 bei Nicht-Entwurf), `publish` ohne/mit `scheduled_at`
  (native + app, Erfolg/Fehler-Pfad, 422 bei fehlenden Pflicht-Aspekten),
  `unschedule` (409 bei Nicht-Geplant), `publish-bulk` (gemischtes
  Ergebnis, ein Fehler blockiert die anderen nicht), `sync-sales`
  (Treffer/Skip/Idempotenz bei erneutem Sync), `oauth/status`-Proxy.
- Ergänzungen in `tests/test_webapp_poc_db.py` für die neuen `db.py`-
  Funktionen (`create_ebay_listing`, `get_ebay_listing`,
  `get_ebay_listing_for_card`, `list_ebay_listings`, `update_ebay_listing`,
  `delete_ebay_listing`, `upsert_ebay_sale`, `list_due_scheduled_listings`).
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
- Automatischer Abruf/Scraping von eBay-Verkaufsdaten oder 130point.com —
  nur Links zur manuellen Recherche (s. o.).
- Automatisches Bootstrap der Business Policies als Teil des
  Publish-Flows — bleibt der bestehende, einmalige manuelle Schritt über
  `ebay-oauth-server/README.md`.
- Eigene eBay-Kategorie-/Zustands-Verwaltung in der WebApp (s.
  [Abweichungen vom Desktop-Modell](#abweichungen-vom-desktop-modell)).
- Umschalten auf Produktion (bleibt ein reiner Env-Var-Wechsel nach
  Freigabe, kein Teil dieses Sub-Projekts).
- Google Drive/Sheets-Sync, Backups (Sub-Projekt 5).
