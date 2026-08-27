# WebApp Sub-Projekt 3: Käufe/Purchases

Status: Entwurf (Brainstorming mit Nutzer abgeschlossen 2026-08-27, Spec zur
Freigabe)

## Kontext

Drittes Sub-Projekt der WebApp-Migration. Sub-Projekt 1
(`docs/superpowers/specs/2026-08-26-webapp-db-backend-foundation-design.md`)
und Sub-Projekt 2
(`docs/superpowers/specs/2026-08-26-webapp-inventory-ui-design.md`) sind
live im Einsatz: Karten werden gescannt, in Supabase gespeichert und lassen
sich über `cards.html`/`card.html` durchsuchen, bearbeiten und löschen. Was
fehlt: **wo kommt eine Karte her, und was hat sie gekostet.**

Die Desktop-App (`app/dcardlabs_manager.py`) hat dafür bereits ein
bewährtes Datenmodell:

- `purchases` — ein Kauf-Vorgang (Datum, Plattform, Verkäufer, Versand,
  Gesamtpreis, Notizen).
- `purchase_items` — verknüpft einen Kauf mit einzelnen Karten
  (`allocated_cost`, `quantity`, `notes` pro Karte).

Dieses Sub-Projekt überträgt das Modell auf die WebApp (Supabase statt
SQLite, UUIDs statt Auto-Increment-IDs) — mit einer bewussten
Vereinfachung gegenüber der Desktop-App (siehe
[Abweichungen vom Desktop-Modell](#abweichungen-vom-desktop-modell)).

## Ziel

- Käufe erfassen — sowohl **Einzelkauf** (eine Karte, ein Kauf) als auch
  **Sammelkauf/Lot** (mehrere Karten in einem Kauf, z. B. "20 Karten für
  50 €").
- Pro Kauf erfassen: Kaufdatum, Plattform/Quelle, Verkäufer, Versandkosten,
  Gesamtpreis, Notizen (inkl. Zustand-bei-Kauf als Freitext, wie in der
  Desktop-App).
- Käufe als eigene Liste ansehen/durchsuchen/bearbeiten/löschen
  (`purchases.html`/`purchase.html`, analog zu `cards.html`/`card.html`).
- Auf `card.html` sehen, zu welchem Kauf eine Karte gehört (falls
  verknüpft) — und eine unverknüpfte Karte direkt dort einem neuen oder
  bestehenden Kauf zuordnen können.

## Datenmodell

Zwei neue Tabellen in `supabase/schema.sql`, UUID-PKs wie `cards`/
`scan_batches`:

```sql
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
```

**`unique (card_id)`:** Eine Karte kann höchstens einem Kauf zugeordnet
sein. Das deckt den realistischen Fall ab (eine physische Karte wird
einmal gekauft) und hält `card.html` einfach — ein `GET /api/cards/{id}`
muss höchstens einen verknüpften Kauf zurückgeben, keine Liste. Ein
zweiter Kauf derselben Karte (z. B. Rückabwicklung + Neukauf) erfordert in
diesem Fall: erst die alte Verknüpfung lösen (`DELETE
/api/purchases/{id}/items/{item_id}`), dann neu verknüpfen.

### Abweichungen vom Desktop-Modell

- **Kein `card_count` auf `purchases`.** Die Desktop-App pflegt dieses
  Feld manuell mit; hier wird die Kartenanzahl aus `purchase_items`
  gezählt (`count(*)` bzw. Länge der `items`-Liste in der API-Antwort) —
  kann nicht aus dem Sync laufen.
- **Kein `purchase_price`/`purchase_date`/`purchase_source` direkt auf
  `cards`.** Die Desktop-App pflegt diese Felder redundant zusätzlich zu
  `purchases`/`purchase_items` (Altlast aus einer Zeit vor der
  relationalen Verknüpfung). Hier ist `purchases`/`purchase_items` die
  einzige Quelle der Wahrheit für Kaufdaten — vermeidet
  Synchronisationsfehler zwischen den beiden Stellen.
- **Kein separates `condition`-Feld auf `purchase_items`.** Wie in der
  Desktop-App auch dort nur implizit über `notes` abgedeckt (dort gibt es
  ein `condition`-Feld nur auf der separaten `inventory`-Tabelle für den
  *aktuellen* Zustand, nicht den Zustand *bei Kauf*) — "Zustand bei Kauf"
  ist Freitext in `purchase_items.notes`.

## Architektur

Kein neuer Service, gleiches Muster wie Sub-Projekt 1/2: FastAPI-Endpoints
in `webapp-poc/main.py`, DB-Zugriff über neue Funktionen in
`webapp-poc/db.py`, statische Vanilla-JS-Seiten unter `webapp-poc/static/`.

```
static/purchases.html  (neu) - Käufe-Liste: Suche, "+ Neuer Kauf", Klick -> Detail
static/purchase.html   (neu) - Kauf-Detail: Vor-/Zurück-Navigation, Felder
                                bearbeiten, verknüpfte Karten
                                (hinzufügen/entfernen/Anteile), löschen
static/card.html        (erweitert) - neuer "Kauf"-Bereich: verknüpften Kauf
                                anzeigen/lösen, oder unverknüpfte Karte einem
                                Kauf zuordnen
```

`purchase.html` liest die Kauf-ID aus dem URL-Query-Parameter
(`purchase.html?id=<uuid>`), analog zu `card.html`.

## API-Endpoints

### `POST /api/purchases` (neu)

Legt einen Kauf an. Body: die Kauf-Felder (`purchase_date`, `platform`,
`seller`, `shipping`, `total_price`, `notes`) plus optional `items`: eine
Liste von `{card_id, allocated_cost, quantity, notes}`. Deckt beide Fälle
in einem Aufruf ab:

- **Einzelkauf:** `items` mit genau einem Eintrag.
- **Sammelkauf:** `items` mit mehreren Einträgen.
- **Kauf ohne sofortige Zuordnung:** `items` weglassen oder leere Liste —
  Karten können später über `POST /api/purchases/{id}/items` nachträglich
  verknüpft werden (deckt den Desktop-App-Ablauf ab, wo ein Kauf oft vor
  dem eigentlichen Scan/Erfassen der Karten manuell angelegt wird).

Ein `card_id` in `items`, das bereits einem anderen Kauf zugeordnet ist
(Verletzung von `unique(card_id)`), liefert 409 mit deutscher
Fehlermeldung und legt **keinen** Kauf an (alles-oder-nichts, damit kein
halb angelegter Kauf mit nur einem Teil der Items übrig bleibt). Gibt den
angelegten Kauf inkl. `items` (jeweils mit Karten-Kurzinfo: `id`, `title`,
`front_image_url` fürs Thumbnail) zurück.

### `GET /api/purchases` (neu)

Query-Parameter `q` (Freitext über `platform`/`seller`/`notes`, wie das
`q` bei `GET /api/cards`), optional. Liste sortiert nach
`purchase_date desc`. Jeder Eintrag inkl. Anzahl verknüpfter Karten
(`item_count`, gezählt aus `purchase_items`).

### `GET /api/purchases/{id}` (neu)

Ein Kauf inkl. `items` (Karten-Kurzinfo je Item wie bei `POST`). 404 mit
deutscher Fehlermeldung, falls die ID nicht existiert.

### `PATCH /api/purchases/{id}` (neu)

Aktualisiert die Kauf-Felder (nicht `items` — dafür die Item-Endpoints
unten). Gleiches Teil-Update-Muster wie `PATCH /api/cards/{id}`. 404 bei
unbekannter ID.

### `DELETE /api/purchases/{id}` (neu)

Löscht den Kauf und (per `on delete cascade`) alle `purchase_items`-Zeilen
dazu — **nicht** die verknüpften Karten selbst, die bleiben in `cards`
erhalten, nur ohne Kauf-Zuordnung. 404 bei unbekannter ID.

### `POST /api/purchases/{id}/items` (neu)

Verknüpft eine Karte mit einem bestehenden Kauf. Body:
`{card_id, allocated_cost, quantity, notes}`. 409 mit deutscher
Fehlermeldung, falls `card_id` bereits einem Kauf zugeordnet ist
(eigenem oder fremdem). 404, falls Kauf oder Karte nicht existiert.

### `PATCH /api/purchases/{id}/items/{item_id}` (neu)

Aktualisiert `allocated_cost`/`quantity`/`notes` eines Items. 404 bei
unbekannter ID.

### `DELETE /api/purchases/{id}/items/{item_id}` (neu)

Löst die Verknüpfung (löscht nur die `purchase_items`-Zeile, Karte und
Kauf bleiben bestehen). 404 bei unbekannter ID.

### `GET /api/cards/{id}` (erweitert)

Antwort bekommt ein zusätzliches Feld `purchase`: `null`, falls die Karte
keinem Kauf zugeordnet ist, sonst
`{purchase_id, purchase_date, platform, seller, allocated_cost, quantity, notes}`
(Kauf-Felder + das zugehörige Item in einem flachen Objekt, damit
`card.html` ohne zweiten Request alles zum Anzeigen hat). Gleiches Muster
für `GET /api/cards` (Liste) — dort reicht ein `has_purchase`-Bool statt
des vollen Objekts, um die Liste schlank zu halten.

## UI-Design

### `purchases.html` — Liste

- Freitextsuche (Plattform/Verkäufer/Notizen), wie bei `cards.html`.
- Tabelle/Kacheln: Kaufdatum, Plattform, Verkäufer, Anzahl Karten,
  Gesamtpreis, Versand. Klick navigiert zu `purchase.html?id=<id>`.
- **"+ Neuer Kauf"**-Button öffnet ein einfaches Formular (Kaufdatum,
  Plattform, Verkäufer, Versand, Gesamtpreis, Notizen) — ohne
  Karten-Zuordnung, die passiert danach auf `purchase.html` oder direkt
  auf `card.html` (siehe unten). Deckt den Desktop-App-Ablauf ab, wo ein
  Kauf oft manuell vor dem Scannen angelegt wird.
- Legt bei jedem Rendern die aktuell angezeigte, gefilterte ID-Reihenfolge
  in `sessionStorage` ab (Schlüssel `purchaseListIds`) — exakt das gleiche
  Muster, mit dem `cards.html` bereits `cardListIds` für `card.html`s
  Vor-/Zurück-Navigation ablegt (kein neuer Backend-Endpoint, nur
  clientseitiger State für die aktuelle Tab-Session).

### `purchase.html` — Detail/Bearbeiten

- **Vor-/Zurück-Navigation** oben auf der Seite: liest `purchaseListIds`
  aus `sessionStorage`, ermittelt die Position der aktuellen Kauf-ID darin
  und zeigt "← Vorherige"/"Nächste →"-Links innerhalb dieser Reihenfolge —
  1:1 übertragen aus `card.html`s `renderPrevNextNav()` (gleiches
  User-Erlebnis wie beim Blättern durch Karten: Direktaufruf ohne zuvor
  geladene Liste, oder eine ID außerhalb der Liste, zeigt einfach keine
  Links; `sessionStorage`-Zugriff ist try/catch-abgesichert, falls der
  Browser-Kontext das blockiert).
- Formular mit den Kauf-Feldern, vorausgefüllt, **"Speichern"** ruft
  `PATCH /api/purchases/{id}`.
- Liste der verknüpften Karten (Thumbnail, Titel, `allocated_cost`,
  `quantity`, `notes` je Karte, editierbar inline oder per kleinem
  Formular), Klick auf eine Karte navigiert zu `card.html?id=<card_id>`.
  Jede Zeile hat einen "Entfernen"-Button (`DELETE
  /api/purchases/{id}/items/{item_id}`).
- **"Karte hinzufügen"**: Freitext-Suchfeld (nutzt `GET
  /api/cards?q=...`, zeigt nur Karten ohne bestehende Kauf-Zuordnung —
  Filterung clientseitig auf Basis von `has_purchase` aus der
  Cards-Liste), Auswahl + `allocated_cost`/`quantity`/`notes` eingeben,
  **"Verknüpfen"** ruft `POST /api/purchases/{id}/items`.
- **"Kauf löschen"**-Button mit Bestätigungsdialog (`confirm()`), danach
  zurück zu `purchases.html`.
- Link **"Zurück zur Liste"**.

### `card.html` — neuer "Kauf"-Bereich

Zusätzlicher Abschnitt unterhalb der bestehenden Felder, gespeist aus dem
neuen `purchase`-Feld in `GET /api/cards/{id}`:

- **Falls verknüpft:** schreibgeschützte Anzeige (Kaufdatum, Plattform,
  Verkäufer, Anteil-Preis, Notizen) plus Link **"Zum Kauf"**
  (`purchase.html?id=<purchase_id>`) und ein **"Verknüpfung lösen"**-Button
  (`DELETE /api/purchases/{purchase_id}/items/{item_id}`, danach die
  Karte neu laden).
- **Falls nicht verknüpft:** ein einklappbarer Mini-Bereich **"Kauf
  erfassen"** mit zwei Optionen:
  - **Neuer Kauf für diese Karte:** Kurzformular (Kaufdatum, Plattform,
    Verkäufer, Preis, Versand, Notizen) → `POST /api/purchases` mit
    genau einem Item (dieser Karte). Deckt den Einzelkauf-Schnellpfad
    direkt von der Kartenseite aus ab.
  - **Zu bestehendem Kauf hinzufügen:** Freitext-Suche über bestehende
    Käufe (`GET /api/purchases?q=...`), Auswahl → `POST
    /api/purchases/{id}/items` mit dieser Karte. Deckt den Fall ab, eine
    Karte nachträglich einem bereits angelegten Sammelkauf zuzuordnen.

## Fehlerbehandlung

- `PATCH`/`DELETE` auf nicht existierende Kauf- oder Item-IDs: 404 mit
  deutscher Fehlermeldung (bestehendes Muster).
- Doppelte Kauf-Zuordnung einer Karte (`unique(card_id)`-Verletzung): 409
  mit deutscher Fehlermeldung, sowohl bei `POST /api/purchases` (mit
  `items`) als auch bei `POST /api/purchases/{id}/items`.
- `POST /api/purchases` mit `items`: alles-oder-nichts (kein Kauf wird
  angelegt, wenn auch nur ein Item scheitert) — vermeidet einen Kauf mit
  nur teilweise verknüpften Karten, der beim erneuten Versuch schwer zu
  reparieren wäre.
- Frontend zeigt Netzwerk-/Serverfehler als einfache Fehlermeldung im UI
  (gleiches `#status`-Muster wie `cards.html`/`card.html`).

## Tests

Neue Dateien unter `tests/` (Supabase-Client gemockt, wie im gesamten
Projekt):

- `tests/test_webapp_poc_purchases_endpoints.py` — alle neuen/erweiterten
  Endpoints: `POST`/`GET`/`PATCH`/`DELETE /api/purchases[/{id}]`, `POST`/
  `PATCH`/`DELETE /api/purchases/{id}/items[/{item_id}]`, 404-Fälle,
  409 bei doppelter Zuordnung, alles-oder-nichts bei `POST
  /api/purchases` mit `items`, erweitertes `GET /api/cards/{id}` mit
  `purchase`-Feld (verknüpft/unverknüpft).
- Ergänzungen in `tests/test_webapp_poc_db.py` für die neuen `db.py`-
  Funktionen (`create_purchase`, `list_purchases`, `get_purchase`,
  `update_purchase`, `delete_purchase`, `add_purchase_item`,
  `update_purchase_item`, `delete_purchase_item`).
- Frontend (`purchases.html`/`purchase.html`/erweitertes `card.html`):
  kein JS-Test-Framework im Projekt (wie Sub-Projekt 2) — Verifikation
  manuell im Browser gegen die echten Endpoints.

## Explizit außerhalb dieses Scopes

- Mehrere Käufe pro Karte (Kauf-Historie) — aktuell `unique(card_id)`,
  siehe Datenmodell-Abschnitt.
- Automatische Margen-/Gewinn-Berechnung (Kaufpreis vs. Verkaufspreis) —
  Grundlage (`allocated_cost`) ist gelegt, Auswertung folgt mit der
  eBay-Integration (Sub-Projekt 4), wenn Verkaufsdaten verfügbar sind.
- Paging in `purchases.html` (analog zur Begründung in Sub-Projekt 2).
- Bulk-Import von Käufen (z. B. CSV-Upload).
- eBay-Integration (Sub-Projekt 4).
- Google Drive/Sheets-Sync, Backups (Sub-Projekt 5).
