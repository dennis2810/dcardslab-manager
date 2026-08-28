# WebApp Sub-Projekt 5: Manuelles Anlegen einzelner Karten

Status: Freigegeben (Brainstorming + Spec-Review mit Nutzer abgeschlossen
2026-08-27)

## Kontext

Fünftes Sub-Projekt der WebApp-Migration. Der bisherige Weg, Karten
anzulegen, ist ausschließlich der 9er-Scan-Bogen über `index.html`
(`POST /api/scan`). Das passt nicht für Einzelkäufe oder Nachträge, bei
denen nur eine oder wenige Karten fotografiert werden — dafür bräuchte es
einen vollen 9er-Bogen. Bereits in `webapp-poc/README.md` unter "Was
absichtlich fehlt" vorgemerkt: manuelles Anlegen einer einzelnen Karte
direkt auf `cards.html`, inkl. Bild-Upload vom Gerät (nicht nur
Scan-Bogen), Dreh-Funktion wie in `card.html`, und KI-Vorbelegung der
Kartendaten aus den hochgeladenen Bildern.

## Brainstorming-Entscheidungen

- **UI-Ort:** Eigene neue Seite `static/card-new.html` (nicht ein
  Inline-Formular/Modal auf `cards.html`) — passt zum bestehenden Muster
  eigener Seiten pro Vorgang (`index.html` scannen, `cards.html` Liste,
  `card.html` Detail, `purchase.html`, `ebay.html`).
- **KI-Erkennung:** Läuft **nicht automatisch** nach dem Hochladen,
  sondern nur per explizitem Button "KI erkennen" — vermeidet
  ungewollte Claude-Vision-Kosten bei jedem Bild-Upload, anders als der
  automatische 9er-Scan-Flow.
- **Bildpflicht:** Vorder- **und** Rückseite sind beim Anlegen
  Pflichtfelder — hält den Scope klein, kein neuer
  Bild-Nachtragen-Endpoint für bereits gespeicherte Karten nötig
  (`card.html` kann aktuell nur drehen, nicht hochladen; das bleibt so).
- **Datenmodell:** Kein Schema-Update, keine neue Spalte/Markierung
  "manuell angelegt". Eine manuell angelegte Karte bekommt wie jede
  gescannte Karte eine `scan_batches`-Zeile (`card_count=1`,
  `position_in_batch=1`) — unverändertes `db.insert_card()`/
  `storage.upload_image()`, keine Sonderbehandlung nötig oder gewünscht.
- **Nach dem Speichern:** Weiterleitung zu `card.html?id=<id>` statt
  eines eigenen vollständigen Bearbeitungsformulars auf der neuen Seite
  — `card.html` deckt Feld-Bearbeitung, Drehen im Nachhinein, Kauf- und
  eBay-Zuordnung bereits vollständig ab. `card-new.html` beschränkt sich
  bewusst auf Upload + Drehen (vor dem Speichern) + KI-Vorbelegung +
  Speichern, keine Duplikation der bestehenden Bearbeitungs-UI.

## Ziel

- Auf `cards.html` ein Link/Button "+ Neue Karte" zu `card-new.html`.
- Auf `card-new.html`: Vorder- und Rückseite vom Gerät hochladen
  (Dateiauswahl, kein Scan-Bogen), mit Vorschau + Dreh-Buttons **vor**
  dem Speichern (rein clientseitig per Canvas, identisches Muster wie
  bereits in `index.html` für den Scan-Bogen vorhanden).
- Button "KI erkennen": ruft `recognize_card()` auf die aktuell
  hochgeladenen/gedrehten Bilder auf und befüllt das Formular mit den
  erkannten Feldern (Titel, Kategorie, Team, Hersteller, Set, Saison,
  Kartennummer, …) — **ohne** etwas zu speichern, rein editierbare
  Vorbelegung.
- Alle Kartenfelder (dieselben wie in `card.html`s Bearbeitungsformular)
  sind unabhängig von der KI-Erkennung manuell editierbar.
- Button "Karte speichern": persistiert Bilder + Felder, leitet zu
  `card.html?id=<neue-id>` weiter.

## Architektur

```
webapp-poc/
    main.py                (erweitert) - zwei neue Endpoints
    static/cards.html       (erweitert) - Link zu card-new.html
    static/card-new.html    (neu) - Upload + Drehen + KI-Vorbelegung + Speichern
```

Kein neues Backend-Modul — beide Endpoints sind dünne Wrapper um bereits
vorhandene Bausteine (`recognize_card()`, `db.create_batch()`/
`db.insert_card()`, `storage.upload_image()`), analog zum bestehenden
`POST /api/scan`, nur ohne den Zuschnitt-Schritt (`scanner.process()`) —
das hochgeladene Bild ist bereits eine einzelne Karte, kein 9er-Bogen.

## API-Endpoints (`webapp-poc/main.py`)

### `POST /api/cards/recognize` (neu)

Multipart, zwei Pflicht-Dateien `front`/`back` (analog zu `POST
/api/scan`s Handling). Schreibt beide in ein Temp-Verzeichnis, ruft
`recognize_card(front_path=, back_path=)` auf, gibt das Ergebnis-Dict
unverändert als JSON zurück (`recognize_card()` fängt bereits alle
Fehler intern ab und liefert `EMPTY_FIELDS` mit erklärendem
`status`-Text, s. `integrations/ai_card_recognition.py` — kein weiteres
Error-Handling hier nötig). **Kein** Datenbank-/Storage-Zugriff, rein
lesend/berechnend — kann beliebig oft ohne Nebenwirkung aufgerufen
werden.

### `POST /api/cards` (neu)

Multipart: Pflicht-Dateien `front`/`back`, plus ein Textfeld `fields`
(JSON-String der Kartenfelder — ein einzelnes JSON-Blob statt 15
einzelner Form-Felder, einfacher auf Client- und Serverseite). Ungültiges
JSON in `fields` → 400. Ablauf:

1. `db.create_batch(card_count=1)`.
2. `storage.upload_image(batch_id, 1, "front", front_tmp_path)` /
   `..., "back", ...)` — identische Funktion wie im bestehenden
   `/api/scan`-Flow, keine Änderung an `storage.py` nötig.
3. Schlägt der Bild-Upload fehl: `db.update_batch_status(batch_id,
   "failed")`, HTTP 502 mit der Fehlermeldung (analog zum
   `image_error`-Muster in `/api/scan`, hier aber als harter Fehler statt
   Teilerfolg, da es nur eine einzige Karte in diesem Batch gibt — kein
   "teilweise erfolgreicher Batch"-Fall wie beim 9er-Scan möglich).
4. `db.insert_card(batch_id, 1, parsed_fields, front_image_path,
   back_image_path)`.
5. `db.update_batch_status(batch_id, "ok")`.
6. Antwort: die neu angelegte Karte, signierte Bild-URLs über das
   bestehende `_attach_signed_urls()` (gleiche Form wie `GET
   /api/cards/{id}`).

## UI-Design

### `cards.html` — neuer Einstiegspunkt

Ein Link/Button "+ Neue Karte" oberhalb oder neben der Suchleiste,
führt zu `card-new.html`.

### `card-new.html` (neu)

- Zwei Datei-Inputs (Vorderseite/Rückseite), Vorschau + Dreh-Buttons
  sobald eine Datei gewählt ist — identisches JS-Muster wie
  `index.html`s `rotateBlob()`/Canvas-Ansatz (kopiert, kein gemeinsames
  JS-Modul — Projekt hat keinen Build-Schritt, Duplikation zwischen
  statischen Seiten ist hier das etablierte Muster).
- Button "KI erkennen" — aktiv, sobald beide Bilder gewählt sind, ruft
  `POST /api/cards/recognize` mit den aktuellen (ggf. bereits gedrehten)
  Bild-Blobs auf, befüllt das Formular unten mit dem Ergebnis. Während
  des Aufrufs Button deaktiviert + Statustext ("Erkenne …"), da ein
  Claude-Vision-Call einige Sekunden dauert (gleiche Erwartung wie beim
  bestehenden Scan-Flow).
- Formularfelder: identische Liste wie `card.html`s `FIELDS`-Array
  (`title`, `category`, `theme`, `team`, `manufacturer`, `set_name`,
  `season_year`, `card_type`, `variant`, `position`, `squad_number`,
  `club_debut_season`, `card_number`, `serial_number`, `print_run`) —
  leer vorbelegt, durch "KI erkennen" befüllbar, jederzeit manuell
  editierbar.
- Button "Karte speichern" — aktiv, sobald beide Bilder gewählt sind
  (unabhängig davon, ob KI-Erkennung gelaufen ist). Sendet `POST
  /api/cards` (multipart: aktuelle Bild-Blobs + `fields` als
  JSON-String aus den Formularwerten). Bei Erfolg: Weiterleitung zu
  `card.html?id=<neue-id>`. Bei Fehler: Meldung über das bestehende
  `#status`-Muster, Formular bleibt erhalten (kein Datenverlust).

## Fehlerbehandlung

- `POST /api/cards/recognize` ohne beide Dateien: FastAPI liefert
  automatisch 422 (`File(...)` ist Pflicht) — keine eigene Prüfung
  nötig.
- `POST /api/cards` ohne beide Dateien: gleiches automatisches 422.
- `POST /api/cards` mit ungültigem `fields`-JSON: 400 mit deutscher
  Meldung.
- `POST /api/cards` mit fehlschlagendem Bild-Upload: 502 mit
  Fehlermeldung, `scan_batches`-Zeile bleibt mit `status="failed"`
  bestehen (kein verwaister `cards`-Eintrag, da `insert_card()` erst
  danach läuft).
- Frontend zeigt Netzwerk-/Serverfehler über das bestehende
  `#status`-Muster (wie `index.html`/`cards.html`/`purchase.html`).

## Tests

- `tests/test_webapp_poc_cards_endpoints.py` (bestehende Datei,
  erweitert): `POST /api/cards/recognize` ruft `recognize_card()` mit
  den hochgeladenen Bildern auf, gibt dessen Rückgabewert unverändert
  zurück, kein DB-/Storage-Aufruf (Assertion:
  `db.create_batch.assert_not_called()`). `POST /api/cards`: legt Batch
  + Karte an (Assertion auf `db.create_batch(card_count=1)`,
  `storage.upload_image` zweimal mit `position=1`, `db.insert_card`),
  400 bei ungültigem `fields`-JSON, 502 bei fehlschlagendem
  `storage.upload_image` (inkl. `db.update_batch_status(batch_id,
  "failed")`-Assertion), Antwortform wie `GET /api/cards/{id}`.
- Frontend (`card-new.html`, erweitertes `cards.html`): kein
  JS-Test-Framework im Projekt (wie in allen bisherigen Sub-Projekten) —
  Verifikation manuell im Browser (Playwright-gestützt, wie bei
  Sub-Projekt 3/4 bereits praktiziert).

## Explizit außerhalb dieses Scopes

- Bild nachträglich zu einer bereits gespeicherten Karte hochladen/
  ersetzen (nur Drehen ist in `card.html` vorhanden, bleibt so) — beide
  Bilder sind beim Anlegen Pflicht, s. Brainstorming-Entscheidungen.
- Mehrere einzelne Karten in einem Rutsch anlegen (Batch-Upload
  mehrerer Einzelkarten) — eine Karte pro Aufruf von `card-new.html`.
- Automatische KI-Erkennung ohne Button-Klick.
- Markierung/Filter "manuell angelegt" vs. "gescannt" in `cards.html`.
