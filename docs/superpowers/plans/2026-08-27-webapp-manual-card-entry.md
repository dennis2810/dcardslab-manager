# WebApp Manuelles Karten-Anlegen Implementation Plan

**Goal:** Auf `cards.html` eine einzelne Karte manuell anlegen können
(Vorder-/Rückseiten-Upload vom Gerät, Drehen vor dem Speichern,
optionale KI-Vorbelegung per Button), statt ausschließlich über den
9er-Scan-Bogen.

**Architecture:** Zwei neue, dünne Endpoints in `webapp-poc/main.py`
(`POST /api/cards/recognize`, `POST /api/cards`), die ausschließlich
bereits vorhandene Bausteine wiederverwenden (`recognize_card()`,
`db.create_batch()`/`db.insert_card()`, `storage.upload_image()`) — kein
neues Backend-Modul. Eine neue statische Seite `card-new.html` (gleiches
Muster wie `index.html`s clientseitiges Dreh-JS), plus ein Link auf
`cards.html`. Kein neuer Service, kein Build-Schritt.

**Tech Stack:** FastAPI (bestehend), Supabase Postgres/Storage
(bestehend), Vanilla JS/HTML.

**Spec:** `docs/superpowers/specs/2026-08-27-webapp-manual-card-entry-design.md`
(Status: Freigegeben)

## Global Constraints

- Beide neuen Endpoints verwenden ausschließlich bereits vorhandene
  Funktionen aus `db.py`/`storage.py`/`ai_card_recognition.py` — keine
  neuen Datenbank-/Storage-Funktionen nötig.
- `scan_batches`/`cards` bekommen keine neue Spalte — eine manuell
  angelegte Karte ist ein ganz normaler Batch mit `card_count=1` (s.
  Spec, Abschnitt "Brainstorming-Entscheidungen").
- Deutsche Statustexte/Fehlermeldungen im bestehenden Stil.
- Kein neues JS-Framework, kein Build-Schritt; `card-new.html`
  dupliziert bewusst das Dreh-JS aus `index.html` statt es zu teilen
  (Projekt-Konvention, s. Spec).

---

### Task 1: `webapp-poc/main.py` – `POST /api/cards/recognize` und `POST /api/cards`

**Files:**
- Modify: `webapp-poc/main.py`
- Modify: `tests/test_webapp_poc_cards_endpoints.py`

**Interfaces:**
- Consumes: `recognize_card()` (bereits importiert in `main.py`),
  `db.create_batch`, `db.insert_card`, `db.update_batch_status`,
  `storage.upload_image` (alle bestehend, unverändert).
- Produces:
  - `POST /api/cards/recognize` (multipart: `front`, `back`) → JSON,
    exakt das Rückgabe-Dict von `recognize_card()`.
  - `POST /api/cards` (multipart: `front`, `back`, Form-Feld `fields`
    als JSON-String) → JSON, gleiche Form wie `GET /api/cards/{id}`
    (Karte inkl. signierter Bild-URLs, ohne `purchase`/`ebay_listing`,
    da beide für eine gerade erst angelegte Karte immer leer/`None`
    wären).

Task 2 (`card-new.html`) ruft beide mit exakt dieser Form auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

An `tests/test_webapp_poc_cards_endpoints.py` anhängen (gleicher Stil
wie `tests/test_webapp_poc_scan_endpoint.py`s `_post_scan()`-Helper,
`main.recognize_card` direkt gepatcht statt über `ai_card_recognition`):

```python
import json


class RecognizeCardImagesEndpointTests(unittest.TestCase):
    def _post_recognize(self):
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        return client.post("/api/cards/recognize", files=files)

    def test_returns_recognized_fields(self):
        fake_result = {"title": "Max Mustermann", "category": "Fußball", "status": "ok"}
        with patch("main.recognize_card", return_value=fake_result) as mock_recognize:
            response = self._post_recognize()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fake_result)
        mock_recognize.assert_called_once()

    def test_does_not_touch_the_database(self):
        with patch("main.recognize_card", return_value={"status": "ok"}), \
             patch("main.db.create_batch") as mock_create_batch:
            self._post_recognize()
        mock_create_batch.assert_not_called()

    def test_missing_file_is_422(self):
        response = client.post("/api/cards/recognize", files={"front": ("front.jpg", b"x", "image/jpeg")})
        self.assertEqual(response.status_code, 422)


class CreateCardManualEndpointTests(unittest.TestCase):
    def _post_create(self, fields=None):
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        data = {"fields": json.dumps(fields if fields is not None else {"title": "Max Mustermann"})}
        return client.post("/api/cards", files=files, data=data)

    def _patch_all(self, **overrides):
        patches = {
            "main.db.create_batch": MagicMock(return_value="batch-1"),
            "main.db.update_batch_status": MagicMock(),
            "main.db.insert_card": MagicMock(return_value={
                "id": "card-1", "batch_id": "batch-1", "title": "Max Mustermann",
                "front_image_path": "batch-1/1_front.jpg", "back_image_path": "batch-1/1_back.jpg",
            }),
            "main.storage.upload_image": MagicMock(side_effect=lambda b, p, side, path: f"{b}/{p}_{side}.jpg"),
            "main.storage.signed_url": MagicMock(side_effect=lambda object_path, **_: f"https://signed/{object_path}"),
        }
        patches.update(overrides)
        patchers = [patch(target, new) for target, new in patches.items()]
        for p in patchers:
            self.addCleanup(p.stop)
        return {target: p.start() for target, p in zip(patches, patchers)}

    def test_creates_batch_with_count_one(self):
        mocks = self._patch_all()
        self._post_create()
        mocks["main.db.create_batch"].assert_called_once_with(card_count=1)

    def test_uploads_both_images_at_position_one(self):
        mocks = self._patch_all()
        self._post_create()
        upload_calls = mocks["main.storage.upload_image"].call_args_list
        self.assertEqual(len(upload_calls), 2)
        for call in upload_calls:
            self.assertEqual(call.args[0], "batch-1")
            self.assertEqual(call.args[1], 1)
        sides = {call.args[2] for call in upload_calls}
        self.assertEqual(sides, {"front", "back"})

    def test_inserts_card_with_parsed_fields(self):
        mocks = self._patch_all()
        self._post_create(fields={"title": "Erika Musterfrau", "team": "FC Test"})
        insert_args = mocks["main.db.insert_card"].call_args.args
        self.assertEqual(insert_args[0], "batch-1")
        self.assertEqual(insert_args[1], 1)
        self.assertEqual(insert_args[2]["title"], "Erika Musterfrau")
        self.assertEqual(insert_args[2]["team"], "FC Test")

    def test_marks_batch_ok_on_success(self):
        mocks = self._patch_all()
        self._post_create()
        mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "ok")

    def test_returns_card_with_signed_urls(self):
        self._patch_all()
        response = self._post_create()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "card-1")
        self.assertTrue(body["front_image_url"].startswith("https://signed/"))

    def test_invalid_fields_json_is_400(self):
        self._patch_all()
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        response = client.post("/api/cards", files=files, data={"fields": "not-json"})
        self.assertEqual(response.status_code, 400)

    def test_upload_failure_marks_batch_failed_and_returns_502(self):
        mocks = self._patch_all(
            **{"main.storage.upload_image": MagicMock(side_effect=RuntimeError("Storage down"))}
        )
        response = self._post_create()
        self.assertEqual(response.status_code, 502)
        mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "failed")
        mocks["main.db.insert_card"].assert_not_called()

    def test_missing_file_is_422(self):
        self._patch_all()
        response = client.post(
            "/api/cards", files={"front": ("front.jpg", b"x", "image/jpeg")},
            data={"fields": "{}"},
        )
        self.assertEqual(response.status_code, 422)
```

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_cards_endpoints -v`
  → FAIL (Routen existieren noch nicht → 404/`AttributeError`).

- [ ] **Step 3: Endpoints in `main.py` ergänzen**

Neuer Import am Dateianfang (bei den bestehenden FastAPI-Imports):

```python
import json

from fastapi import Body, FastAPI, File, Form, HTTPException, Response, UploadFile
```

(`Form` ergänzt die bestehende Import-Zeile, nicht duplizieren.)

Beide Routen direkt nach `POST /api/scan` einfügen (vor
`_expand_purchase_items`):

```python
@app.post("/api/cards/recognize")
async def recognize_card_images(front: UploadFile = File(...), back: UploadFile = File(...)):
    with tempfile.TemporaryDirectory(prefix="dcardslab_manual_") as tmp_str:
        tmp = Path(tmp_str)
        front_path = tmp / f"front{Path(front.filename or 'front.jpg').suffix}"
        back_path = tmp / f"back{Path(back.filename or 'back.jpg').suffix}"
        front_path.write_bytes(await front.read())
        back_path.write_bytes(await back.read())
        fields = recognize_card(front_path=front_path, back_path=back_path)
    return JSONResponse(fields)


@app.post("/api/cards")
async def create_card_manual(
    front: UploadFile = File(...), back: UploadFile = File(...), fields: str = Form("{}"),
):
    try:
        parsed_fields = json.loads(fields)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="fields muss gueltiges JSON sein.") from exc

    with tempfile.TemporaryDirectory(prefix="dcardslab_manual_") as tmp_str:
        tmp = Path(tmp_str)
        front_path = tmp / f"front{Path(front.filename or 'front.jpg').suffix}"
        back_path = tmp / f"back{Path(back.filename or 'back.jpg').suffix}"
        front_path.write_bytes(await front.read())
        back_path.write_bytes(await back.read())

        batch_id = db.create_batch(card_count=1)
        try:
            front_image_path = storage.upload_image(batch_id, 1, "front", front_path)
            back_image_path = storage.upload_image(batch_id, 1, "back", back_path)
        except Exception as exc:
            db.update_batch_status(batch_id, "failed")
            raise HTTPException(
                status_code=502, detail=f"Bild-Upload fehlgeschlagen: {type(exc).__name__}: {exc}"
            ) from exc

    card_row = db.insert_card(batch_id, 1, parsed_fields, front_image_path, back_image_path)
    db.update_batch_status(batch_id, "ok")
    return JSONResponse(_attach_signed_urls(card_row))
```

(Beide Routen liegen bewusst **vor** `_expand_purchase_items()`/
`_attach_signed_urls()` in der Lesereihenfolge der Datei nur der
Übersichtlichkeit halber direkt hinter `/api/scan`  — `_attach_signed_urls`
ist zum Zeitpunkt des Funktionsaufrufs zur Laufzeit bereits definiert,
Python löst das beim Modul-Import vollständig auf, keine
Vorwärtsreferenz-Problematik.)

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_cards_endpoints -v`
  → PASS.

- [ ] **Step 5:** `python3 -m unittest discover -s tests -p 'test_*.py'`
  → volle Suite weiterhin grün (Regressionscheck).

- [ ] **Step 6: Commit**

```bash
git add webapp-poc/main.py tests/test_webapp_poc_cards_endpoints.py
git commit -m "Add POST /api/cards/recognize and POST /api/cards for manual card entry"
```

---

### Task 2: `static/card-new.html` (neu) + Link in `cards.html`

**Files:**
- Create: `webapp-poc/static/card-new.html`
- Modify: `webapp-poc/static/cards.html`
- Modify: `webapp-poc/README.md`

**Interfaces:**
- Consumes: `POST /api/cards/recognize`, `POST /api/cards` (Task 1).

Kein Python-Code, daher kein TDD-Zyklus für diesen Task — Verifikation
ist manuelles Testen im Browser (Playwright-gestützt), wie bei den
Frontend-Teilen der Sub-Projekte 2–4.

- [ ] **Step 1: `cards.html` – Link ergänzen**

Oberhalb/neben der bestehenden Suchleiste einen Link einfügen:

```html
<p><a href="card-new.html">+ Neue Karte</a></p>
```

- [ ] **Step 2: `card-new.html` erstellen**

Struktur (angelehnt an `index.html`s Upload+Dreh-Bereich für die Bilder,
`card.html`s `FIELDS`-Array für das Formular):

- Zwei Datei-Inputs (`front`/`back`), Vorschau + Dreh-Buttons pro Seite
  sobald eine Datei gewählt ist — `rotateBlob(blob, direction)` 1:1 aus
  `index.html` übernommen (Canvas-Rotation, `sideBlobs[side]` hält
  jeweils den aktuellen, ggf. gedrehten Blob).
- Button "KI erkennen" (deaktiviert, bis beide `sideBlobs` gesetzt
  sind): `FormData` mit beiden aktuellen Blobs an `POST
  /api/cards/recognize`, Antwort befüllt die Formularfelder unten
  (`form.elements[key].value = result[key] ?? ""` für jedes Feld aus
  `FIELDS`). Während des Requests Button deaktiviert + Statustext
  "Erkenne …".
- `<form id="card-form">` mit denselben Feldern/Labels wie `card.html`s
  `FIELDS`-Array (`title`, `category`, `theme`, `team`, `manufacturer`,
  `set_name`, `season_year`, `card_type`, `variant`, `position`,
  `squad_number`, `club_debut_season`, `card_number`, `serial_number`,
  `print_run`), leer vorbelegt.
- Button "Karte speichern" (deaktiviert, bis beide `sideBlobs` gesetzt
  sind): `FormData` mit beiden aktuellen Blobs + `fields` (JSON-String
  aus den aktuellen Formularwerten) an `POST /api/cards`. Erfolg →
  `window.location.href = `card.html?id=${data.id}``. Fehler → Meldung
  im bestehenden `#status`-Element, Formularinhalt bleibt erhalten.

- [ ] **Step 3: Manuelle Verifikation im Browser**

Lokal starten (`uvicorn main:app --app-dir webapp-poc`), mit Playwright
oder von Hand: zwei Bilder hochladen, drehen, "KI erkennen" klicken
(prüft echten `recognize_card()`-Aufruf falls `ANTHROPIC_API_KEY`
gesetzt ist, sonst greift `EMPTY_FIELDS`-Fallback — beides ein gültiger
Testpfad), Felder von Hand anpassen, "Karte speichern" klicken, prüfen,
dass die Weiterleitung zu `card.html?id=...` mit den korrekten Werten
und beiden Bildern ankommt.

- [ ] **Step 4: `webapp-poc/README.md` aktualisieren**

Im Abschnitt "Was hier passiert" einen Punkt zum manuellen Anlegen
ergänzen (analog zum bestehenden Eintrag für `/api/cards/{id}/rotate`).
Den Eintrag "Manuelles Anlegen einzelner Karten auf `cards.html`" aus
dem Abschnitt "Was absichtlich fehlt" entfernen (jetzt umgesetzt).

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/static/card-new.html webapp-poc/static/cards.html webapp-poc/README.md
git commit -m "Add card-new.html: manual single-card entry with upload, rotate, AI-prefill"
```
