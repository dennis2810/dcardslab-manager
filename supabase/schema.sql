-- Einmalig im Supabase SQL Editor ausführen (Projekt: dcardslab-manager).
create extension if not exists pgcrypto;

create table if not exists scan_batches (
    id          uuid primary key default gen_random_uuid(),
    created_at  timestamptz not null default now(),
    status      text not null default 'pending',   -- 'pending' | 'ok' | 'partial' | 'failed'
    card_count  int not null default 0
);

create table if not exists cards (
    id                  uuid primary key default gen_random_uuid(),
    batch_id            uuid references scan_batches(id) on delete cascade,
    position_in_batch   int not null,

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
    recognition_status  text default '',

    front_image_path    text,
    back_image_path     text,

    created_at          timestamptz not null default now()
);

create index if not exists cards_batch_id_idx on cards(batch_id);

-- Migration (2026-08-28): a short, sequential, human-readable card number -
-- cards.id is a UUID, which makes a poor eBay SKU/inventory reference to
-- read off an order export or a Seller Hub listing by eye. Adds the column
-- without a default, backfills existing rows in creation order, then wires
-- up a sequence for all future inserts. Safe to re-run - the column add and
-- backfill are both guarded.
alter table cards add column if not exists card_no bigint;

with ordered as (
    select id, row_number() over (order by created_at, id) as rn
    from cards
    where card_no is null
)
update cards set card_no = ordered.rn
from ordered
where cards.id = ordered.id;

create sequence if not exists cards_card_no_seq;
select setval('cards_card_no_seq', coalesce((select max(card_no) from cards), 0));
alter sequence cards_card_no_seq owned by cards.card_no;
alter table cards alter column card_no set default nextval('cards_card_no_seq');
alter table cards alter column card_no set not null;
create unique index if not exists cards_card_no_idx on cards(card_no);

create table if not exists purchases (
    id             uuid primary key default gen_random_uuid(),
    purchase_date  date not null,
    platform       text default '',   -- z.B. "eBay", "Kleinanzeigen", "Messe"
    seller         text default '',
    shipping       numeric default 0,
    total_price    numeric default 0,
    notes          text default '',
    created_at     timestamptz not null default now()
);

create table if not exists purchase_items (
    id              uuid primary key default gen_random_uuid(),
    purchase_id     uuid not null references purchases(id) on delete cascade,
    card_id         uuid not null references cards(id) on delete cascade,
    allocated_cost  numeric default 0,
    quantity        int default 1,
    notes           text default '',   -- Zustand bei Kauf o.ä., Freitext
    created_at      timestamptz not null default now(),
    unique (card_id)
);

create index if not exists purchase_items_purchase_id_idx
    on purchase_items(purchase_id);

create table if not exists ebay_listings (
    id               uuid primary key default gen_random_uuid(),
    card_id          uuid not null unique references cards(id) on delete cascade,
    sku              text not null unique,
    title            text default '',
    description      text default '',
    condition        text default 'NM',
    condition_id     text default '4000',
    grader           text default '',  -- z.B. "PSA", "BGS" - nur bei condition_id '2750' (Graded)
    grade            text default '',  -- z.B. "9.5" - nur bei condition_id '2750' (Graded)
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

-- Migration (2026-08-31): Grader/Grade for graded (PSA/BGS/...) cards -
-- only relevant when condition_id is '2750' (Graded); safe to re-run.
alter table ebay_listings add column if not exists grader text default '';
alter table ebay_listings add column if not exists grade text default '';

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

create table if not exists google_sheets_settings (
    id              boolean primary key default true check (id),
    refresh_token   text default '',
    spreadsheet_id  text default '',
    connected_at    timestamptz,
    last_synced_at  timestamptz
);

-- Physical-stock ledger, ported from the desktop app's "inventory" table
-- (SQLite, card_id/quantity/condition/location/notes) - a card can have
-- more than one inventory row (e.g. tracked separately per location),
-- same as the desktop app allowed.
create table if not exists inventory (
    id          uuid primary key default gen_random_uuid(),
    card_id     uuid not null references cards(id) on delete cascade,
    quantity    int default 1,
    condition   text default 'NM',
    location    text default '',
    notes       text default '',
    created_at  timestamptz not null default now()
);

create index if not exists inventory_card_id_idx on inventory(card_id);

-- Migration (2026-09-06): manuell setzbares "Versendet"-Flag pro Karte.
alter table cards add column if not exists shipped boolean not null default false;

-- Migration (2026-09-06): tatsaechliche Portokosten pro Verkauf (manuell
-- eingetragen) - Gegenstueck zu shipping_charged (vom Kaeufer bezahlt, ab
-- jetzt beim sync-sales automatisch aus der eBay-Bestellung befuellt).
-- Zusammen ergeben sie den Versand als durchlaufenden Posten im Gewinn.
alter table ebay_sales add column if not exists shipping_cost numeric default 0;

-- Migration (2026-09-06): Preisrecherche-Verlauf pro Karte - manuell
-- eingetragene Marktpreis-Beobachtungen ueber die Zeit (z.B. "eBay verkaufte
-- Artikel" am X. Datum bei Y Euro), unabhaengig vom eigenen eBay-Angebot.
create table if not exists price_research (
    id          uuid primary key default gen_random_uuid(),
    card_id     uuid not null references cards(id) on delete cascade,
    price       numeric not null,
    note        text default '',
    checked_at  date not null default current_date,
    created_at  timestamptz not null default now()
);

create index if not exists price_research_card_id_idx on price_research(card_id);

-- Migration (2026-09-08): freie, kommagetrennte Tags/Kategorien pro Karte
-- (z.B. "Rookie, PSA-wuerdig, Investment") zum Filtern/Wiederfinden.
alter table cards add column if not exists tags text not null default '';

-- Migration (2026-09-08): selbst eintragbares Jahresziel (Umsatz oder
-- Gewinn) fuer den Fortschrittsbalken im Dashboard - ein Ziel pro Jahr,
-- damit ein neues Jahr nicht versehentlich das alte Ziel weiterlaufen laesst.
create table if not exists dashboard_goals (
    year        int primary key,
    metric      text not null default 'revenue',
    amount      numeric not null default 0,
    updated_at  timestamptz not null default now()
);

-- Migration (2026-09-08): Beleg (Foto/PDF der Rechnung) pro Kauf, fuer die
-- Steuer - Pfad in den privaten purchase-receipts-Bucket (siehe README).
alter table purchases add column if not exists receipt_path text default '';

-- Migration (2026-09-08): Retoure/Rueckerstattung fuer einen eBay-Verkauf -
-- Verkaufspreis/erhaltener Versand zaehlen dann nicht mehr als Umsatz im
-- Gewinn (siehe main.py's _compute_statistics()), Einstandspreis/Porto/
-- eBay-Gebuehren bleiben als Verlust bestehen.
alter table ebay_sales add column if not exists refunded boolean not null default false;

-- Migration (2026-09-09): Singleton-Status fuers Dashboard - aktuell nur der
-- Zeitpunkt des letzten Backup-Downloads (Google-Sheets-Sync hat das schon
-- ueber google_sheets_settings.last_synced_at).
create table if not exists app_status (
    id              boolean primary key default true check (id),
    last_backup_at  timestamptz
);

-- Migration (2026-09-10): Verkaeufe ausserhalb von eBay (Kleinanzeigen,
-- Vinted, privat, ...) - eigenstaendig von ebay_sales, da sie nicht an
-- einen ebay_listings-Eintrag gebunden sind. Unique auf card_id, da eine
-- Karte hoechstens einmal manuell verkauft wird (anders als ebay_sales,
-- das theoretisch mehrere eBay-Bestellungen pro Karte abbilden koennte).
create table if not exists manual_sales (
    id                uuid primary key default gen_random_uuid(),
    card_id           uuid not null unique references cards(id) on delete cascade,
    channel           text default '',
    sale_date         timestamptz,
    gross_price       numeric default 0,
    shipping_charged  numeric default 0,
    shipping_cost     numeric default 0,
    fees              numeric default 0,
    refunded          boolean not null default false,
    notes             text default '',
    created_at        timestamptz not null default now()
);

create index if not exists manual_sales_card_id_idx on manual_sales(card_id);

-- Migration (2026-09-10): Sendungsverfolgung - Trackingnummer + Versand-
-- dienstleister pro Verkauf, damit sich der Status nachvollziehen laesst.
-- Bei eBay-Verkaeufen wird die Trackingnummer zusaetzlich an eBay
-- uebermittelt (siehe ebay_client.submit_shipping_fulfillment()), was dort
-- automatisch die Bestellung als versendet markiert und den Kaeufer
-- benachrichtigt - bei manuellen Verkaeufen (Kleinanzeigen, Vinted, ...)
-- gibt es dafuer keine API, das Feld dient dort nur der eigenen Notiz.
alter table ebay_sales add column if not exists tracking_number text default '';
alter table ebay_sales add column if not exists shipping_carrier text default '';
alter table manual_sales add column if not exists tracking_number text default '';
alter table manual_sales add column if not exists shipping_carrier text default '';

-- Migration (2026-09-10): Absenderadresse fuer das druckbare Adress-
-- Etikett/den Lieferschein - freier Mehrzeilen-Text statt einzelner
-- Adressfelder, da hier nur der Ausdruck davon abhaengt, keine
-- Auswertung/Validierung noetig ist.
alter table app_status add column if not exists sender_address text default '';

-- Migration (2026-09-10): weitere Fotos pro Karte, zusaetzlich zu Vorder-/
-- Rueckseite (z.B. Detailaufnahmen bei Beschaedigungen) - kommagetrennte
-- Storage-Objektpfade im card-images-Bucket, gleiches Freitext-Muster wie
-- cards.tags statt einer eigenen Tabelle, da die Reihenfolge/Anzahl klein
-- und ohne eigene Metadaten bleibt.
alter table cards add column if not exists extra_image_paths text not null default '';

-- Migration (2026-09-10): automatische Preisrecherche im Hintergrund-
-- Scheduler (ebay_scheduler.py) - merkt sich, wann ein veroeffentlichtes
-- Angebot zuletzt automatisch geprueft wurde, damit jedes Angebot nur
-- ca. einmal pro Woche erneut gesucht wird (eBays Application-Token hat
-- ein taegliches Anfragelimit fuer die Buy/Browse-Suche).
alter table ebay_listings add column if not exists last_price_research_at timestamptz;

-- Migration (2026-09-10): Wunschliste/Beobachtungsliste fuer Karten, die
-- (noch) nicht im Besitz sind - bewusst eine eigene, von cards komplett
-- getrennte Tabelle statt eines "noch nicht gekauft"-Flags an cards, damit
-- bestehende Auswertungen/Filter fuer tatsaechlich vorhandene Karten nicht
-- durch Wunschlisten-Eintraege verfaelscht werden.
create table if not exists wishlist_items (
    id            uuid primary key default gen_random_uuid(),
    title         text not null default '',
    team          text default '',
    set_name      text default '',
    target_price  numeric,
    notes         text default '',
    created_at    timestamptz not null default now()
);

create index if not exists wishlist_items_created_at_idx on wishlist_items(created_at);

-- Migration (2026-09-10): Mindestbestand-Schwelle fuer Bestandswarnungen -
-- global statt pro Karte/Lagerort, damit die Einstellung einfach bleibt;
-- gleiches Singleton-Row-Muster wie app_status.last_backup_at.
alter table app_status add column if not exists low_stock_threshold int not null default 0;

-- Migration (2026-09-10): Zeitstempel fuer das automatische, woechentliche
-- Backup (backup.py's run_forever()) - eigenes Feld statt last_backup_at
-- (manueller Download), da beides unterschiedliche Ereignisse sind.
alter table app_status add column if not exists last_auto_backup_at timestamptz;

-- Migration (2026-09-10): eBay-Käufer-Benutzername je Verkauf - bewusst nur
-- der pseudonyme eBay-Handle (aus order.buyer.username), keine
-- Klarname/Adresse (die bleibt weiterhin nicht gespeichert, siehe
-- GET /api/ebay/sales/{id}/shipping-address). Ermöglicht eine einfache
-- Käufer-Historie (Wiederholungskäufer erkennen) ohne personenbezogene
-- Adressdaten dauerhaft vorzuhalten.
alter table ebay_sales add column if not exists buyer_username text default '';

-- Migration (2026-09-10): Automatische Preispruefung fuer Wunschlisten-
-- Eintraege (Sourcing-Liste) - gleiches Prinzip wie last_price_research_at
-- auf ebay_listings fuer bereits veroeffentlichte eigene Angebote, hier
-- aber zusaetzlich mit dem guenstigsten gefundenen Treffer selbst
-- (last_match_*), damit wishlist.html ihn ohne manuelles "Neu suchen"
-- direkt anzeigen kann.
alter table wishlist_items add column if not exists last_price_check_at timestamptz;
alter table wishlist_items add column if not exists last_match_price numeric;
alter table wishlist_items add column if not exists last_match_title text default '';
alter table wishlist_items add column if not exists last_match_url text default '';

-- Migration (2026-09-10): Wiederverwendbare Textbausteine fuer eBay-
-- Beschreibungen (z.B. ein Hinweis fuer eine ganze Set-Serie) - werden beim
-- Anlegen eines Angebots optional in die automatisch generierte
-- Beschreibung eingefuegt (siehe ebay_listing.generate_description()),
-- statt denselben Text bei jeder aehnlichen Karte neu zu tippen. Eigene
-- Tabelle statt an eine Karte/ein Angebot gebunden, da ein Baustein ueber
-- viele Angebote hinweg wiederverwendet wird.
create table if not exists description_templates (
    id          uuid primary key default gen_random_uuid(),
    name        text not null default '',
    body        text not null default '',
    created_at  timestamptz not null default now()
);

-- Migration (2026-09-10): Perceptual-Hash (dHash) des Vorderseitenfotos je
-- Karte, fuer die Foto-basierte Duplikat-Erkennung beim Scannen - ergaenzt
-- den bestehenden exakten Titel/Set/Kartennummer-Abgleich (find_duplicate_card)
-- um Faelle, in denen die Texterkennung ein Feld falsch liest, das Foto aber
-- (nahezu) identisch zu einer bereits vorhandenen Karte ist.
alter table cards add column if not exists front_image_hash text;

-- Migration (2026-09-10): "Abgeholt"-Flag fuer Selbstabholer - eigenes,
-- von "shipped" unabhaengiges Kaestchen, da eine persoenliche Abholung kein
-- Versand ist und eBay dafuer auch keinen FULFILLED-Status/Tracking liefert.
alter table cards add column if not exists picked_up boolean not null default false;

-- Migration (2026-09-10): manuell setzbares "Zugestellt"-Kaestchen pro
-- Verkauf - eBay liefert ueber die API keine echte Zustellbestaetigung
-- (das braeuchte pro Versanddienstleister eine eigene Tracking-API-
-- Anbindung mit eigenen Zugangsdaten), daher bewusst manuell wie "Versendet"/
-- "Retoure" statt automatisiert.
alter table ebay_sales add column if not exists delivered boolean not null default false;
alter table manual_sales add column if not exists delivered boolean not null default false;

-- Migration (2026-09-10): Zeitstempel fuers Ausblenden aelterer Eintraege in
-- "Letzte Aktivitaet" (Dashboard) - die Liste wird live aus Kaeufen/
-- Verkaeufen/Karten berechnet statt aus einem gespeicherten Protokoll,
-- daher merkt sich nur dieser Zeitpunkt, ab wann wieder Ereignisse gezeigt
-- werden sollen.
alter table app_status add column if not exists activity_cleared_at timestamptz;

-- Migration (2026-09-10): Verlauf der automatischen Wunschlisten-Preis-
-- pruefung (siehe ebay_scheduler.run_wishlist_price_check_once()) - fuer
-- eine Sparkline auf wishlist.html, analog zum Preisrecherche-Verlauf bei
-- Karten (price_research). Nur Treffer werden gespeichert, keine
-- erfolglosen Pruefungen.
create table if not exists wishlist_price_checks (
    id          uuid primary key default gen_random_uuid(),
    item_id     uuid not null references wishlist_items(id) on delete cascade,
    price       numeric not null,
    checked_at  timestamptz not null default now()
);

create index if not exists wishlist_price_checks_item_id_idx on wishlist_price_checks(item_id);

-- Migration (2026-09-10): Privatentnahme - eine Karte wird aus dem
-- Verkaufsbestand in die private Sammlung ueberfuehrt. Setzt/loescht dieses
-- Flag den Inventar-Bestand entsprechend auf 0 bzw. stellt ihn wieder her
-- (siehe db.set_card_private_collection()); Statistiken/Inventar-Warnungen
-- schliessen so markierte Karten aus, ein eventuell noch aktives eBay-
-- Angebot bleibt aber bestehen und wird nur mit einem Hinweis versehen
-- (gleiche Mechanik wie ein anderweitiger Verkauf).
alter table cards add column if not exists private_collection boolean not null default false;

-- Migration (2026-09-11): Zeitpunkt des letzten periodischen Hintergrund-
-- Verkaufs-Syncs (ebay_scheduler.run_sales_sync_once()) - macht den bisher
-- nur manuell per Button ausloesbaren Sync ("Verkaeufe synchronisieren")
-- zusaetzlich automatisch (alle 15 Minuten), gleiches Singleton-Row-Muster
-- wie app_status.last_backup_at.
alter table app_status add column if not exists last_sales_sync_at timestamptz;

-- Migration (2026-09-11): SMTP-Einstellungen fuer die E-Mail-Benachrichtigung
-- bei neuen eBay-Verkaeufen (siehe main.py's _notify_new_sales()) - bewusst
-- im Tool unter "Einstellungen" eintragbar statt per Umgebungsvariable,
-- damit sie ohne Neu-Deployment aenderbar sind. smtp_password liegt damit
-- in der Datenbank statt nur beim Deployment - bewusste Abwaegung fuer
-- einfachere Bedienbarkeit.
alter table app_status add column if not exists smtp_host text default '';
alter table app_status add column if not exists smtp_port int;
alter table app_status add column if not exists smtp_username text default '';
alter table app_status add column if not exists smtp_password text default '';
alter table app_status add column if not exists smtp_from text default '';
alter table app_status add column if not exists smtp_to text default '';
alter table app_status add column if not exists smtp_use_tls boolean not null default true;
alter table app_status add column if not exists notify_on_sale boolean not null default false;

-- Migration (2026-09-11): Zeitpunkt des letzten periodischen Retouren-Syncs
-- (ebay_scheduler.run_returns_sync_once()) - gleiches Muster wie
-- last_sales_sync_at oben, eigenes Feld da unabhaengig voneinander
-- fehlschlagen/laufen koennend.
alter table app_status add column if not exists last_returns_sync_at timestamptz;

-- Migration (2026-09-11): globaler Ein/Aus-Schalter fuer die automatische
-- Neuveroeffentlichung unverkaufter eBay-Angebote (siehe
-- ebay_scheduler.run_auto_relist_once()) - zusaetzliche Sicherheitsbremse
-- neben der Pro-Angebot-Einstellung ebay_listings.auto_relist_after_days:
-- ein echter eBay-Aufruf (Beenden + Neueinstellen), der sich nicht
-- rueckgaengig machen laesst, soll sich global abschalten lassen, ohne jedes
-- Angebot einzeln bearbeiten zu muessen.
alter table app_status add column if not exists auto_relist_enabled boolean not null default false;

-- Migration (2026-09-11): pro Angebot einstellbare automatische
-- Neuveroeffentlichung nach X Tagen ohne Verkauf - nullable, da die
-- Automatik standardmaessig aus ist (kein Wert = nie automatisch neu
-- einstellen). last_auto_relisted_at markiert automatisch neu eingestellte
-- Angebote sichtbar in der Oberflaeche (siehe card.html/ebay.html), damit
-- transparent bleibt, dass das Tool und nicht der Nutzer selbst gehandelt hat.
alter table ebay_listings add column if not exists auto_relist_after_days int;
alter table ebay_listings add column if not exists last_auto_relisted_at timestamptz;

-- Migration (2026-09-11): woechentliche Schnappschuesse des geschaetzten
-- Bestandswerts (Preisrecherche-Durchschnitt x Menge je Karte, siehe
-- backup.run_forever()-aehnlicher Hintergrund-Job in main.py) fuer den
-- Portfolio-Wertverlauf auf der Statistik-Uebersichtsseite - eigene Tabelle
-- statt eines Felds auf app_status, da hier (anders als die Singleton-Row-
-- Einstellungen oben) ein Verlauf ueber die Zeit gespeichert wird.
create table if not exists portfolio_value_snapshots (
    id            uuid primary key default gen_random_uuid(),
    snapshot_date date not null,
    total_value   numeric not null default 0,
    card_count    int not null default 0,
    created_at    timestamptz not null default now()
);

create index if not exists portfolio_value_snapshots_date_idx on portfolio_value_snapshots(snapshot_date);

-- Web-Push-Abos (VAPID) je Geraet/Browser, siehe push_notify.py. Das
-- VAPID-Schluesselpaar selbst liegt bewusst NICHT hier (Umgebungsvariablen,
-- siehe README.md) - diese Tabelle haelt nur, wohin (endpoint) und womit
-- (p256dh/auth) ein einzelnes Geraet erreichbar ist.
create table if not exists push_subscriptions (
    id          uuid primary key default gen_random_uuid(),
    endpoint    text not null unique,
    p256dh      text not null,
    auth        text not null,
    created_at  timestamptz not null default now()
);

-- Migration (2026-09-11): seit wann das AKTUELL laufende eBay-Listing
-- (ebay_listing_id) live ist - anders als published_at, das bei JEDER
-- erneuten Veroeffentlichung ueberschrieben wird (auch bei einer reinen
-- Bearbeitung/Preisaenderung eines bereits laufenden Angebots), wird
-- listing_since nur beim allerersten Live-Gehen gesetzt und dann bei einem
-- Neu-Einstellen zurueckgesetzt (eBay vergibt dabei ein frisches Listing,
-- siehe main.py's _relist_listing()) - zeigt also "seit wann laeuft genau
-- dieses eBay-Listing" statt "wann zuletzt irgendwas geaendert wurde".
-- last_auto_relisted_at markiert seit dieser Migration nicht mehr nur ein
-- automatisches, sondern jedes Neu-Einstellen (auch ueber den manuellen
-- Button) - der Spaltenname blieb bewusst, um keine bestehende Spalte
-- umbenennen zu muessen.
alter table ebay_listings add column if not exists listing_since timestamptz;

-- Bugfix-Nachtrag zur obigen Migration: listing_since fehlte in db.py's
-- EBAY_LISTING_WRITABLE_STATUS_FIELDS-Allowlist und wurde von
-- update_ebay_listing() dadurch fuer JEDEN Aufruf (Erstveroeffentlichung
-- wie Neu-Einstellen) still verworfen, ohne Fehler - dieser Backfill holt
-- fuer bereits bestehende Zeilen mit published_at das nach, sonst bliebe
-- "Eingestellt am" auf der Kartenseite fuer alle vor diesem Fix
-- veroeffentlichten Angebote leer. Kein exaktes Ersteinstellungsdatum (da
-- published_at auch bei reinen Bearbeitungen ueberschrieben wurde), aber
-- die bestmoegliche Annaeherung ohne Blick in eBays eigene Historie.
update ebay_listings set listing_since = published_at
  where listing_since is null and published_at is not null;

-- Migration (2026-09-12): letzte bekannte Aufrufzahl (siehe
-- ebay_client.get_listing_views(), main.py's ebay_listing_views()) bleibt
-- jetzt in der Karten-/eBay-Uebersicht sichtbar, bis das naechste Mal auf
-- "Aufrufe laden" geklickt wird, statt nach jedem Seiten-Neuladen wieder
-- bei "-" zu starten (Klaerung mit dem Nutzer).
alter table ebay_listings add column if not exists last_known_views int;

-- Migration (2026-09-12): konfigurierbare Preis-Alarm-Schwelle (Einstellungen)
-- statt der zuvor fest verdrahteten 20% in card.html/dashboard.html/ebay.html.
alter table app_status add column if not exists price_alert_threshold_pct numeric not null default 20;

-- Migration (2026-09-12): globale Pagination-Seitengroesse und Kompakt-/
-- Komfort-Ansicht (Einstellungen) statt der zuvor pro Seite fest
-- verdrahteten 40 Zeilen (jede der betroffenen Seiten - Karten, Kaeufe,
-- Inventar, eBay, Inventur, Wunschliste, Statistik-Listen - hat ihr eigenes
-- PAGE_SIZE, siehe main.py's GET /api/app-status).
alter table app_status add column if not exists page_size int not null default 40;
alter table app_status add column if not exists view_density text not null default 'comfort';

-- Migration (2026-09-12): rein informatives Protokoll fehlgeschlagener
-- Login-Versuche (main.py's db.record_failed_login(), Anzeige in
-- settings.html) - die eigentliche Bruteforce-Drossel in main.py's
-- /api/login laeuft komplett in-memory und ist davon unabhaengig.
alter table app_status add column if not exists failed_login_log jsonb not null default '[]'::jsonb;

-- Migration (2026-09-12): Wiedervorlage/Erinnerungen (Klaerung mit dem
-- Nutzer) - freie, selbst angelegte Erinnerungen an eine Karte (Datum +
-- Notiz), ergaenzend zu den beiden automatischen Regeln (lange unverkauft
-- ohne aktuelle Preisrecherche; Wunschlisten-Preis lange nicht erreicht),
-- die main.py rein aus bestehenden Tabellen ableitet, ohne eigene Zeilen.
create table if not exists reminders (
    id          uuid primary key default gen_random_uuid(),
    card_id     uuid not null references cards(id) on delete cascade,
    note        text not null default '',
    due_date    date not null,
    resolved_at timestamptz,
    created_at  timestamptz not null default now()
);
create index if not exists reminders_card_id_idx on reminders(card_id);
-- Fuer die Dashboard-Abfrage "faellige, noch offene Erinnerungen" - filtert
-- zuerst auf unresolved (Teilindex), due_date-Vergleich bleibt im Query.
create index if not exists reminders_due_date_idx on reminders(due_date) where resolved_at is null;

-- E-Mail-Digest fuer faellige Wiedervorlagen/Erinnerungen - eigener Schalter
-- statt notify_on_sale mitzunutzen, da inhaltlich unabhaengig (taeglich
-- geprueft statt bei jedem Verkaufs-Sync, siehe main.py's
-- _send_reminder_digest_if_due()).
alter table app_status add column if not exists notify_on_reminders boolean not null default false;
alter table app_status add column if not exists last_reminder_email_sent_at timestamptz;
-- Schwellwerte fuer die beiden automatischen Wiedervorlage-Regeln (siehe
-- main.py's _stale_listing_reminders()/_stale_wishlist_reminders()) -
-- konfigurierbar statt fest verdrahtet, damit z.B. eine andere Kartenart/
-- Preisklasse ein anderes Tempo braucht als die Standardwerte.
alter table app_status add column if not exists stale_listing_min_days int not null default 90;
alter table app_status add column if not exists stale_listing_check_days int not null default 30;
alter table app_status add column if not exists stale_wishlist_min_days int not null default 60;
-- CSV-Trennzeichen fuer alle downloadCsv()-Exporte (Karten, Kaeufe, Inventar,
-- eBay, Inventur, Wunschliste, Private Sammlung, Versand, Statistiken) -
-- Standard Semikolon, da deutsches Excel das ohne Umweg erwartet.
alter table app_status add column if not exists csv_delimiter text not null default ';';
-- Schwellwerte fuer die Kleinunternehmer-Umsatzgrenzen-Warnung (siehe
-- kleinunternehmer.html/dashboard.html) - Standard sind die aktuellen
-- gesetzlichen Grenzen aus Paragraph 19 UStG, konfigurierbar, falls sich
-- diese per Gesetzesaenderung mal verschieben.
alter table app_status add column if not exists kleinunternehmer_prev_year_threshold numeric not null default 22000;
alter table app_status add column if not exists kleinunternehmer_current_year_threshold numeric not null default 50000;
-- Ob der Kleinunternehmer-Hinweis (Paragraph 19 UStG) auf gedruckten
-- Verkaufsbelegen erscheint (card.html, "Verkauf ausserhalb eBay") -
-- Standard an, laesst sich abschalten, sobald man die Kleinunternehmer-
-- grenze ueberschritten hat und regulaer Umsatzsteuer ausweisen muss.
alter table app_status add column if not exists kleinunternehmer_hint_enabled boolean not null default true;
-- Beleg fuer manuelle Verkaeufe (Kleinanzeigen, Vinted, privat, ...) -
-- gleiches Muster wie purchases.receipt_path, aber fuer die Verkaufsseite:
-- Aufbewahrungspflicht betrifft auch Verkaufsbelege, nicht nur Kaufbelege.
alter table manual_sales add column if not exists receipt_path text default '';

-- Rechnungstool (zusaetzlich zum bestehenden Kleinbetragsrechnung-Beleg,
-- nicht als Ersatz): fortlaufende, eindeutige Rechnungsnummer ueber alle
-- Jahre hinweg (Paragraph 14 UStG verlangt Fortlaufendheit/Eindeutigkeit,
-- nicht zwingend einen Jahresreset) - einmal vergeben, nie wieder geaendert
-- (siehe db.py's issue_invoice(), idempotent). unique-Constraint schuetzt
-- vor versehentlicher Doppelvergabe bei parallelen Anfragen.
alter table manual_sales add column if not exists invoice_number integer unique;
alter table manual_sales add column if not exists invoice_issued_at timestamptz;

-- Sonstige Betriebsausgaben (Verpackungsmaterial, Buerobedarf, Software-
-- Abos, Fahrtkosten, Porto ohne Verkaufsbezug, ...) - eigenstaendige,
-- schlanke Erfassung statt eine bestehende Tabelle (purchases/manual_sales)
-- zweckzuentfremden, da diese Ausgaben weder an einen Kauf noch an einen
-- Verkauf einer Karte gebunden sind. Fliesst in die EUER-Jahresaufstellung
-- auf kleinunternehmer.html ein (siehe main.py's _compute_euer()).
create table if not exists business_expenses (
    id            uuid primary key default gen_random_uuid(),
    expense_date  date not null,
    category      text not null default '',
    amount        numeric not null default 0,
    note          text not null default '',
    created_at    timestamptz not null default now()
);
