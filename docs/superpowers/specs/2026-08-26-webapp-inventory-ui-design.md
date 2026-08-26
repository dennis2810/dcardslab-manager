# WebApp Sub-Projekt 2: Inventar-Verwaltung im Web-Frontend

Status: approved (Brainstorming abgeschlossen 2026-08-26)

## Kontext

Zweites Sub-Projekt der WebApp-Migration (siehe Sub-Projekt 1:
`docs/superpowers/specs/2026-08-26-webapp-db-backend-foundation-design.md`,
inzwischen live im Einsatz und verifiziert). Sub-Projekt 1 hat die
Persistenz gebaut (`POST /api/scan` speichert nach Supabase, `GET
/api/cards`/`GET /api/cards/{id}` lesen zurück) — aber es gibt noch keine
Möglichkeit, gespeicherte Karten zu **sehen, zu korrigieren oder zu
löschen**. `static/index.html` ist weiterhin nur das Wegwerf-Testformular
für den Scan-Vorgang.

Dieses Sub-Projekt baut die erste echte Nutzungsoberfläche: eine
Karten-Liste mit Suche/Filter plus eine Detailseite zum Bearbeiten/
Ausfüllen fehlender Felder und zum Löschen.

## Ziel

- Gespeicherte Karten als Liste ansehen, mit Freitext- und
  Status-Filter durchsuchen.
- Eine einzelne Karte im Detail ansehen (beide Bilder), alle Felder
  korrigieren oder nachtragen und speichern.
- Eine Karte (z. B. Fehlscan, Duplikat) löschen — inklusive ihrer
  Bilder im Storage.

## Architektur

Kein neuer Service, kein Build-Prozess. Bleibt beim bestehenden Muster
aus Sub-Projekt 1: mehrere einfache HTML-Seiten mit Vanilla JS,
ausgeliefert vom bestehenden FastAPI-`StaticFiles`-Mount in
`webapp-poc/main.py`.

```
static/index.html   (bestehend) - Scan-Testformular, bekommt einen Link zur neuen Liste
static/cards.html   (neu)       - Karten-Liste: Suche/Filter, Miniaturbild, Klick -> Detail
static/card.html    (neu)       - Detail/Bearbeiten: beide Bilder, Formular, Speichern/Löschen
```

`card.html` liest die Karten-ID aus dem URL-Query-Parameter
(`card.html?id=<uuid>`).

**Warum kein Framework/Build-Schritt:** Konsistent mit der Entscheidung
aus Sub-Projekt 1 (schlankes Setup, keine npm/Node-Pipeline auf dem
NAS) — für eine Liste mit Suchfeld und ein Formular mit ~15 Feldern
reicht Vanilla JS völlig aus.

**Warum serverseitige statt clientseitige Suche/Filter:** Client-seitig
müsste der Browser bei jedem Laden der Liste alle Karten (und für jede
zwei signierte Bild-URLs) auf einmal abrufen, auch wenn am Ende nur
wenige zum Suchbegriff passen. Serverseitig filtert Supabase direkt in
der Datenbank-Abfrage — bleibt performant, auch wenn der Bestand auf
Hunderte/Tausende Karten wächst, und erzeugt nur für tatsächlich
angezeigte Treffer signierte URLs.

## API-Endpoints

### `GET /api/cards` (erweitert)

Neue optionale Query-Parameter, beide kombinierbar und beide weglassbar
(Standardverhalten wie bisher: alle Karten, neueste zuerst):

- `q` — Freitext-Suche, durchsucht `title`, `team`, `set_name`,
  `card_number` (case-insensitive Teilstring-Suche, Postgres `ILIKE`
  über alle vier Spalten verknüpft mit OR).
- `status` — exakter Filter auf `recognition_status` (z. B. nur
  `"prüfen"`, um Karten zu finden, die noch Korrektur brauchen).

### `PATCH /api/cards/{id}` (neu)

Nimmt ein JSON-Objekt mit einem oder mehreren der bearbeitbaren Felder
(alle Namen aus `db.CARD_FIELDS`, plus `recognition_status`). Nur die
im Request-Body übergebenen Felder werden aktualisiert — kein Feld ist
Pflicht, so lässt sich auch ein einzelnes leeres Feld nachtragen, ohne
alle anderen erneut mitzuschicken. Gibt die aktualisierte Karte
(inkl. frisch signierter Bild-URLs, wie `GET /api/cards/{id}`) zurück.
404 mit deutscher Fehlermeldung, falls die ID nicht existiert.

### `DELETE /api/cards/{id}` (neu)

Löscht die Karten-Zeile in Supabase Postgres **und** die zugehörigen
Bilder (`front_image_path`/`back_image_path`, falls gesetzt) im
Storage-Bucket `card-images` — sonst blieben verwaiste, nicht mehr
referenzierte Bilddateien im Storage-Kontingent liegen. Gibt 204 (kein
Body) zurück. 404 mit deutscher Fehlermeldung, falls die ID nicht
existiert.

## UI-Design

### `cards.html` — Liste

- Oben: ein Freitext-Suchfeld und ein Status-Dropdown ("alle" /
  `ok` / `prüfen` / `nicht erkannt`). Änderung an einem der beiden
  löst eine neue `GET /api/cards?q=...&status=...`-Abfrage aus.
- Darunter: Kacheln oder Tabelle mit Vorderseiten-Miniaturbild, Titel,
  Team, Saison, Status (farblich hervorgehoben, z. B. Gelb für
  `"prüfen"`, damit unvollständige Karten auffallen). Klick auf eine
  Karte navigiert zu `card.html?id=<id>`.
- Kein eigenes Paging in dieser Version — bei der aktuellen
  Bestandsgröße nicht nötig, kann als eigene spätere Erweiterung
  nachgerüstet werden, falls der Bestand deutlich wächst.

### `card.html` — Detail/Bearbeiten

- Vorder- und Rückseiten-Bild groß nebeneinander (signierte URLs aus
  `GET /api/cards/{id}`).
- Alle Felder als Textfelder in einem Formular, vorausgefüllt mit den
  gespeicherten Werten (leere Felder erscheinen als leere Textfelder
  zum Ausfüllen).
- **"Speichern"**-Button: schickt nur die geänderten Werte per
  `PATCH /api/cards/{id}`, zeigt danach eine Bestätigung.
- **"Löschen"**-Button mit Bestätigungsdialog (`confirm()`): ruft
  `DELETE /api/cards/{id}` auf, navigiert danach zurück zu
  `cards.html`.
- Link **"Zurück zur Liste"**.

## Fehlerbehandlung

- `PATCH`/`DELETE` auf eine nicht existierende ID: 404 mit deutscher
  Fehlermeldung (Muster wie bereits bei `GET /api/cards/{id}`).
- Schlägt das Löschen eines Bildes im Storage fehl (z. B. Datei bereits
  weg), darf das den Rest des Lösch-Vorgangs nicht blockieren — die
  Karten-Zeile in der DB wird trotzdem gelöscht (kein Alles-oder-Nichts,
  analog zur Fehlerbehandlung aus Sub-Projekt 1).
- Frontend zeigt Netzwerk-/Serverfehler als einfache Fehlermeldung im
  UI an (gleiches Muster wie das bestehende `#status`-Element in
  `index.html`).

## Tests

- Backend: neue Tests für `PATCH /api/cards/{id}` (Teil-Update ändert
  nur übergebene Felder, 404 bei unbekannter ID), `DELETE
  /api/cards/{id}` (löscht Karte + beide Bilder aus dem Storage, 404
  bei unbekannter ID, Bild-Lösch-Fehler blockiert DB-Löschung nicht),
  erweiterter `GET /api/cards` mit `q`/`status`-Parametern. Supabase-
  Client wird wie im gesamten Projekt gemockt.
- Frontend (`cards.html`/`card.html`): kein JS-Test-Framework im
  Projekt vorhanden und für dieses schlanke Vanilla-JS auch nicht
  nötig — Verifikation manuell im Browser gegen die echten Endpoints,
  wie bereits bei `index.html` gehandhabt.

## Explizit außerhalb dieses Scopes

- Paging/Unendlich-Scroll in der Liste (spätere Erweiterung, falls
  Bestand deutlich wächst).
- Käufe/Purchases (Sub-Projekt 3).
- eBay-Integration (Sub-Projekt 4).
- Google Drive/Sheets-Sync, Backups (Sub-Projekt 5).
- Mehrere Karten auf einmal bearbeiten/löschen (Bulk-Aktionen).
- Eigenes React/Next-Frontend (bewusste Entscheidung für schlankes
  Vanilla-JS, siehe Architektur-Abschnitt).
