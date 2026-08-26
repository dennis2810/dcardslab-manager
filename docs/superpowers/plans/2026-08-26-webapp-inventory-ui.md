# WebApp Inventar-UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Gespeicherte Karten lassen sich in der WebApp ansehen, per Freitext/Status durchsuchen, bearbeiten/vervollständigen und löschen.

**Architecture:** Backend bekommt drei erweiterte/neue Endpoints (`GET /api/cards` mit Filtern, `PATCH /api/cards/{id}`, `DELETE /api/cards/{id}`) auf Basis der bestehenden `db.py`/`storage.py`-Module. Frontend bleibt bei mehreren einfachen HTML-Seiten ohne Build-Schritt (`cards.html` Liste, `card.html` Detail/Bearbeiten), ausgeliefert vom bestehenden `StaticFiles`-Mount.

**Tech Stack:** FastAPI, Supabase Postgres/Storage (bestehend), Vanilla JS/HTML (kein neues Framework).

**Spec:** `docs/superpowers/specs/2026-08-26-webapp-inventory-ui-design.md`

## Global Constraints

- Bearbeitbare Felder sind exakt `db.CARD_FIELDS` (15 Namen) plus `recognition_status` - keine anderen Spalten dürfen über `PATCH` geschrieben werden.
- `GET /api/cards` bleibt ohne Query-Parameter abwärtskompatibel (alle Karten, neueste zuerst) - bestehende Aufrufer (z. B. künftige Skripte) dürfen nicht brechen.
- `q`-Suche durchsucht genau `title`, `team`, `set_name`, `card_number` (case-insensitive Teilstring, Postgres `ILIKE` per OR verknüpft).
- `DELETE` löscht immer zuerst die DB-Zeile, dann die Bilder - ein Bild-Lösch-Fehler darf die Karten-Löschung nicht verhindern (analog zur Fehlerbehandlung aus Sub-Projekt 1: kein Alles-oder-Nichts).
- Kein neues JS-Framework, kein Build-Schritt - reines Vanilla JS wie in `static/index.html`.
- Deutsche Statustexte/Fehlermeldungen im bestehenden Stil.
- Supabase-Client wird in allen Tests gemockt (kein echter Netzwerk-Call in CI), analog zum bestehenden Muster in `tests/test_webapp_poc_db.py` etc.

---

### Task 1: `webapp-poc/db.py` - `update_card`, `delete_card`, gefilterte `list_cards`

**Files:**
- Modify: `webapp-poc/db.py`
- Modify: `tests/test_webapp_poc_db.py`

**Interfaces:**
- Consumes: `supabase_client.get_client()` (bestehend).
- Produces: `update_card(card_id: str, fields: dict) -> dict | None` (nur bekannte Felder werden geschrieben, `None` bei unbekannter ID), `delete_card(card_id: str) -> dict | None` (gibt die gelöschte Zeile zurück, inkl. `front_image_path`/`back_image_path`, oder `None` bei unbekannter ID), `list_cards(q: str | None = None, status: str | None = None) -> list[dict]` (Signaturänderung, beide Parameter optional). Task 3 (`main.py`) ruft alle drei mit exakt dieser Signatur auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

An `tests/test_webapp_poc_db.py` anhängen (nach der bestehenden `GetCardTests`-Klasse, vor `if __name__ == "__main__":`):

```python
class UpdateCardTests(unittest.TestCase):
    def test_updates_only_provided_fields(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "title": "Korrigierter Name"}
        response = MagicMock()
        response.data = [saved_row]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_card("card-1", {"title": "Korrigierter Name"})
        self.assertEqual(result, saved_row)
        mock_client.table.return_value.update.assert_called_once_with({"title": "Korrigierter Name"})
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with("id", "card-1")

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "team": "FC Bayern"}
        response = MagicMock()
        response.data = [saved_row]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_card("card-1", {"team": "FC Bayern", "not_a_real_column": "x"})
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row, {"team": "FC Bayern"})

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_card("does-not-exist", {"title": "x"})
        self.assertIsNone(result)

    def test_empty_valid_fields_returns_current_card(self):
        mock_client = MagicMock()
        existing = {"id": "card-1", "title": "Unveraendert"}
        response = MagicMock()
        response.data = [existing]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_card("card-1", {"not_a_real_column": "x"})
        self.assertEqual(result, existing)
        mock_client.table.return_value.update.assert_not_called()


class DeleteCardTests(unittest.TestCase):
    def test_deletes_card_and_returns_it(self):
        mock_client = MagicMock()
        existing = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": None}
        select_response = MagicMock()
        select_response.data = [existing]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = select_response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_card("card-1")
        self.assertEqual(result, existing)
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "card-1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        select_response = MagicMock()
        select_response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = select_response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_card("does-not-exist")
        self.assertIsNone(result)
        mock_client.table.return_value.delete.assert_not_called()


class ListCardsFilterTests(unittest.TestCase):
    def test_no_filters_behaves_like_before(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "card-1"}]
        mock_client.table.return_value.select.return_value.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.list_cards()
        self.assertEqual(result, [{"id": "card-1"}])
        mock_client.table.return_value.select.return_value.or_.assert_not_called()

    def test_q_filters_across_four_columns(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_cards(q="Bayern")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertIn("title.ilike.%Bayern%", filter_arg)
        self.assertIn("team.ilike.%Bayern%", filter_arg)
        self.assertIn("set_name.ilike.%Bayern%", filter_arg)
        self.assertIn("card_number.ilike.%Bayern%", filter_arg)

    def test_q_strips_commas_and_parens_before_building_filter(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_cards(q="a,b(c)")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertNotIn(",b(", filter_arg)

    def test_status_filters_by_recognition_status(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        eq_builder = mock_client.table.return_value.select.return_value.eq.return_value
        eq_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_cards(status="prüfen")
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with(
            "recognition_status", "prüfen"
        )


if __name__ == "__main__":
    unittest.main()
```

(Die vorhandene `if __name__ == "__main__": unittest.main()`-Zeile am Dateiende bleibt bestehen - die neuen Klassen kommen davor.)

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_db -v`
Expected: FAIL - `AttributeError: module 'db' has no attribute 'update_card'` (bzw. `delete_card`), und die `list_cards`-Filtertests schlagen fehl, weil `list_cards()` noch keine Parameter akzeptiert.

- [ ] **Step 3: `db.py` erweitern**

In `webapp-poc/db.py` die bestehende `list_cards()`-Funktion ersetzen und `update_card`/`delete_card` ergänzen (ans Dateiende, nach `get_card`):

```python
def list_cards(q=None, status=None):
    query = get_client().table("cards").select("*")
    if q:
        safe_q = q.replace(",", " ").replace("(", " ").replace(")", " ")
        pattern = f"%{safe_q}%"
        query = query.or_(
            f"title.ilike.{pattern},team.ilike.{pattern},"
            f"set_name.ilike.{pattern},card_number.ilike.{pattern}"
        )
    if status:
        query = query.eq("recognition_status", status)
    response = query.order("created_at", desc=True).execute()
    return response.data


def get_card(card_id):
    response = get_client().table("cards").select("*").eq("id", card_id).execute()
    return response.data[0] if response.data else None


def update_card(card_id, fields):
    row = {
        name: value for name, value in fields.items()
        if name in CARD_FIELDS or name == "recognition_status"
    }
    if not row:
        return get_card(card_id)
    response = get_client().table("cards").update(row).eq("id", card_id).execute()
    return response.data[0] if response.data else None


def delete_card(card_id):
    card = get_card(card_id)
    if card is None:
        return None
    get_client().table("cards").delete().eq("id", card_id).execute()
    return card
```

(`get_card` bleibt unverändert - hier nur als Kontext gezeigt, wo die neuen Funktionen ansetzen. `list_cards` wird komplett ersetzt.)

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_db -v`
Expected: PASS (17 Tests gesamt in der Datei: 7 bestehende + 4 `UpdateCardTests` + 2 `DeleteCardTests` + 4 `ListCardsFilterTests`)

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/db.py tests/test_webapp_poc_db.py
git commit -m "Add db.update_card/delete_card, extend list_cards with q/status filters"
```

---

### Task 2: `webapp-poc/storage.py` - `delete_images`

**Files:**
- Modify: `webapp-poc/storage.py`
- Modify: `tests/test_webapp_poc_storage.py`

**Interfaces:**
- Consumes: `supabase_client.get_client()` (bestehend).
- Produces: `delete_images(paths: list[str | None]) -> None` (filtert `None`-Werte raus, kein Aufruf bei leerer Liste). Task 3 (`main.py`) ruft dies mit `[front_image_path, back_image_path]` auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

An `tests/test_webapp_poc_storage.py` anhängen (vor `if __name__ == "__main__":`):

```python
class DeleteImagesTests(unittest.TestCase):
    def test_removes_given_paths(self):
        mock_client = MagicMock()
        with patch("storage.get_client", return_value=mock_client):
            storage.delete_images(["b1/1_front.jpg", "b1/1_back.jpg"])
        mock_client.storage.from_.assert_called_once_with(storage.BUCKET)
        mock_client.storage.from_.return_value.remove.assert_called_once_with(
            ["b1/1_front.jpg", "b1/1_back.jpg"]
        )

    def test_filters_out_none_paths(self):
        mock_client = MagicMock()
        with patch("storage.get_client", return_value=mock_client):
            storage.delete_images(["b1/1_front.jpg", None])
        mock_client.storage.from_.return_value.remove.assert_called_once_with(["b1/1_front.jpg"])

    def test_noop_when_no_paths(self):
        mock_client = MagicMock()
        with patch("storage.get_client", return_value=mock_client):
            storage.delete_images([])
        mock_client.storage.from_.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_storage -v`
Expected: FAIL - `AttributeError: module 'storage' has no attribute 'delete_images'`

- [ ] **Step 3: `storage.py` erweitern**

Ans Ende von `webapp-poc/storage.py` anfügen:

```python
def delete_images(paths):
    """Removes zero or more objects from BUCKET in one call. None entries
    (a card missing one side's image) are skipped, not passed to the
    Supabase client."""
    paths = [p for p in paths if p]
    if not paths:
        return
    get_client().storage.from_(BUCKET).remove(paths)
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_storage -v`
Expected: PASS (8 Tests: 5 bestehende + 3 neue)

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/storage.py tests/test_webapp_poc_storage.py
git commit -m "Add storage.delete_images for card deletion"
```

---

### Task 3: Backend-Endpoints - gefiltertes `GET /api/cards`, `PATCH`, `DELETE`

**Files:**
- Modify: `webapp-poc/main.py`
- Modify: `tests/test_webapp_poc_cards_endpoints.py`

**Interfaces:**
- Consumes: `db.list_cards(q, status)`, `db.update_card(card_id, fields)`, `db.delete_card(card_id)` (Task 1), `storage.delete_images(paths)` (Task 2), `storage.signed_url` (bestehend).
- Produces: `GET /api/cards?q=&status=` (erweitert), `PATCH /api/cards/{id}` (neu, gibt aktualisierte Karte inkl. signierter URLs zurück, 404 bei unbekannter ID), `DELETE /api/cards/{id}` (neu, 204 bei Erfolg, 404 bei unbekannter ID, Bild-Lösch-Fehler blockiert die DB-Löschung nicht).

- [ ] **Step 1: Fehlschlagende Tests schreiben**

An `tests/test_webapp_poc_cards_endpoints.py` anhängen (vor `if __name__ == "__main__":`):

```python
class ListCardsFilterEndpointTests(unittest.TestCase):
    def test_passes_query_params_to_db(self):
        with patch("main.db.list_cards", return_value=[]) as mock_list:
            response = client.get("/api/cards?q=Bayern&status=pr%C3%BCfen")
        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(q="Bayern", status="prüfen")


class UpdateCardEndpointTests(unittest.TestCase):
    def test_updates_and_returns_card_with_signed_urls(self):
        updated = {
            "id": "card-1", "title": "Korrigiert",
            "front_image_path": "b1/1_front.jpg", "back_image_path": None,
        }
        with patch("main.db.update_card", return_value=updated) as mock_update, \
             patch("main.storage.signed_url", return_value="https://signed/b1/1_front.jpg"):
            response = client.patch("/api/cards/card-1", json={"title": "Korrigiert"})
        self.assertEqual(response.status_code, 200)
        mock_update.assert_called_once_with("card-1", {"title": "Korrigiert"})
        body = response.json()
        self.assertEqual(body["front_image_url"], "https://signed/b1/1_front.jpg")

    def test_returns_404_when_not_found(self):
        with patch("main.db.update_card", return_value=None):
            response = client.patch("/api/cards/does-not-exist", json={"title": "x"})
        self.assertEqual(response.status_code, 404)


class DeleteCardEndpointTests(unittest.TestCase):
    def test_deletes_card_and_its_images(self):
        deleted = {
            "id": "card-1", "front_image_path": "b1/1_front.jpg",
            "back_image_path": "b1/1_back.jpg",
        }
        with patch("main.db.delete_card", return_value=deleted) as mock_delete, \
             patch("main.storage.delete_images") as mock_delete_images:
            response = client.delete("/api/cards/card-1")
        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with("card-1")
        mock_delete_images.assert_called_once_with(["b1/1_front.jpg", "b1/1_back.jpg"])

    def test_returns_404_when_not_found(self):
        with patch("main.db.delete_card", return_value=None):
            response = client.delete("/api/cards/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_image_delete_failure_does_not_block_card_deletion(self):
        deleted = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": None}
        with patch("main.db.delete_card", return_value=deleted), \
             patch("main.storage.delete_images", side_effect=RuntimeError("bucket down")):
            response = client.delete("/api/cards/card-1")
        self.assertEqual(response.status_code, 204)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_cards_endpoints -v`
Expected: FAIL - `list_cards`-Route ignoriert Query-Parameter noch (Test schlägt bei `assert_called_once_with` fehl), `PATCH`/`DELETE` auf `/api/cards/{id}` geben 405 Method Not Allowed (Routen existieren noch nicht).

- [ ] **Step 3: Endpoints in `main.py` ergänzen**

Import-Zeile erweitern (`from fastapi import ...`):

```python
from fastapi import Body, FastAPI, File, HTTPException, Response, UploadFile
```

Die bestehende `list_cards`-Route ersetzen:

```python
@app.get("/api/cards")
async def list_cards(q: str | None = None, status: str | None = None):
    cards = [_attach_signed_urls(c) for c in db.list_cards(q=q, status=status)]
    return JSONResponse({"cards": cards})
```

Nach der bestehenden `get_card`-Route (`GET /api/cards/{card_id}`) ergänzen:

```python
@app.patch("/api/cards/{card_id}")
async def update_card(card_id: str, fields: dict = Body(...)):
    updated = db.update_card(card_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    return JSONResponse(_attach_signed_urls(updated))


@app.delete("/api/cards/{card_id}", status_code=204)
async def delete_card(card_id: str):
    deleted = db.delete_card(card_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    paths = [p for p in (deleted.get("front_image_path"), deleted.get("back_image_path")) if p]
    if paths:
        try:
            storage.delete_images(paths)
        except Exception:
            pass
    return Response(status_code=204)
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_cards_endpoints -v`
Expected: PASS (9 Tests: 3 bestehende + 6 neue)

- [ ] **Step 5: Bestehende Tests gegenprüfen (Regression)**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (alle Tests im Repo, keine Regression)

- [ ] **Step 6: Commit**

```bash
git add webapp-poc/main.py tests/test_webapp_poc_cards_endpoints.py
git commit -m "Add PATCH/DELETE /api/cards/{id}, extend GET /api/cards with q/status filters"
```

---

### Task 4: `webapp-poc/static/cards.html` - Karten-Liste

**Files:**
- Create: `webapp-poc/static/cards.html`

**Interfaces:**
- Consumes: `GET /api/cards?q=&status=` (Task 3) - erwartet `{"cards": [{"id", "title", "team", "season_year", "recognition_status", "front_image_url", ...}]}`.
- Produces: Links zu `card.html?id=<id>` - Task 5 liest den `id`-Query-Parameter aus genau dieser URL-Form.

Kein Backend-Code, daher kein TDD-Zyklus - Verifikation ist manuelles Testen im Browser (Schritt 2).

- [ ] **Step 1: Seite erstellen**

`webapp-poc/static/cards.html`:

```html
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>DCardLabs – Karten</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.3rem; }
  .controls { display: flex; gap: 0.75rem; margin-bottom: 1.5rem; flex-wrap: wrap; }
  input, select { padding: 0.4rem 0.6rem; font-size: 1rem; }
  .grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 1rem; }
  .card { border: 1px solid #ccc; border-radius: 6px; padding: 0.5rem; text-decoration: none; color: inherit; display: block; }
  .card img { width: 100%; height: 140px; object-fit: cover; border-radius: 4px; background: #f0f0f0; }
  .card .title { font-weight: 600; margin-top: 0.4rem; }
  .card .meta { font-size: 0.8rem; color: #555; }
  .status-pruefen { color: #b06000; font-weight: 600; }
  .status-nicht_erkannt { color: #b00020; font-weight: 600; }
  #status-msg { font-style: italic; color: #555; }
</style>
</head>
<body>
  <h1>DCardLabs – Karten</h1>
  <p><a href="index.html">&larr; Zum Scan-Formular</a></p>

  <div class="controls">
    <input type="text" id="q" placeholder="Suche (Titel, Team, Set, Kartennr.)">
    <select id="status">
      <option value="">Alle Status</option>
      <option value="ok">ok</option>
      <option value="prüfen">prüfen</option>
      <option value="nicht erkannt">nicht erkannt</option>
    </select>
  </div>

  <p id="status-msg"></p>
  <div id="grid" class="grid"></div>

<script>
const qInput = document.getElementById("q");
const statusSelect = document.getElementById("status");
const statusMsg = document.getElementById("status-msg");
const grid = document.getElementById("grid");

function statusClass(status) {
  if (status === "prüfen") return "status-pruefen";
  if (status === "nicht erkannt") return "status-nicht_erkannt";
  return "";
}

async function loadCards() {
  const params = new URLSearchParams();
  if (qInput.value.trim()) params.set("q", qInput.value.trim());
  if (statusSelect.value) params.set("status", statusSelect.value);

  statusMsg.textContent = "Lädt …";
  try {
    const res = await fetch("/api/cards?" + params.toString());
    const body = await res.json();
    if (!res.ok) {
      statusMsg.textContent = "Fehler: " + (body.detail || res.statusText);
      return;
    }
    statusMsg.textContent = `${body.cards.length} Karte(n)`;
    render(body.cards);
  } catch (err) {
    statusMsg.textContent = "Fehler: " + err;
  }
}

function render(cards) {
  grid.innerHTML = "";
  for (const card of cards) {
    const a = document.createElement("a");
    a.className = "card";
    a.href = `card.html?id=${encodeURIComponent(card.id)}`;

    const img = document.createElement("img");
    img.src = card.front_image_url || "";
    img.alt = card.title || "";
    a.appendChild(img);

    const title = document.createElement("div");
    title.className = "title";
    title.textContent = card.title || "(ohne Titel)";
    a.appendChild(title);

    const meta = document.createElement("div");
    meta.className = "meta";
    meta.textContent = [card.team, card.season_year].filter(Boolean).join(" · ");
    a.appendChild(meta);

    const statusEl = document.createElement("div");
    statusEl.className = "meta " + statusClass(card.recognition_status);
    statusEl.textContent = card.recognition_status || "";
    a.appendChild(statusEl);

    grid.appendChild(a);
  }
}

qInput.addEventListener("input", () => {
  clearTimeout(qInput._debounce);
  qInput._debounce = setTimeout(loadCards, 300);
});
statusSelect.addEventListener("change", loadCards);

loadCards();
</script>
</body>
</html>
```

- [ ] **Step 2: Manuell verifizieren**

`uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir webapp-poc` starten (Env-Variablen wie gewohnt gesetzt), im Browser `http://localhost:8000/cards.html` öffnen. Erwartet: Liste der über `POST /api/scan` bereits gespeicherten Karten erscheint mit Bild, Titel, Team/Saison, Status. Suchfeld und Status-Dropdown filtern die Liste (Netzwerk-Tab zeigt `GET /api/cards?q=...`/`?status=...`). Klick auf eine Karte führt zu `card.html?id=<uuid>` (Seite existiert erst nach Task 5, 404 an dieser Stelle ist bis dahin erwartet).

- [ ] **Step 3: Commit**

```bash
git add webapp-poc/static/cards.html
git commit -m "Add cards.html: card list with search/status filter"
```

---

### Task 5: `webapp-poc/static/card.html` - Detail/Bearbeiten

**Files:**
- Create: `webapp-poc/static/card.html`

**Interfaces:**
- Consumes: `GET /api/cards/{id}`, `PATCH /api/cards/{id}`, `DELETE /api/cards/{id}` (Task 3); liest `id` aus dem URL-Query-Parameter, wie von `cards.html` (Task 4) verlinkt.

Kein Backend-Code, daher kein TDD-Zyklus - Verifikation ist manuelles Testen im Browser (Schritt 2).

- [ ] **Step 1: Seite erstellen**

`webapp-poc/static/card.html`:

```html
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>DCardLabs – Karte bearbeiten</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 700px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.3rem; }
  .images { display: flex; gap: 1rem; margin-bottom: 1.5rem; }
  .images img { width: 45%; border-radius: 6px; background: #f0f0f0; }
  form { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
  label { display: flex; flex-direction: column; font-size: 0.85rem; font-weight: 600; }
  input { font-weight: normal; padding: 0.4rem; margin-top: 0.2rem; }
  .actions { grid-column: 1 / -1; display: flex; gap: 0.75rem; margin-top: 1rem; }
  button { padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
  #delete-btn { background: #b00020; color: white; border: none; border-radius: 4px; }
  .error { color: #b00020; font-weight: 600; }
</style>
</head>
<body>
  <h1>Karte bearbeiten</h1>
  <p><a href="cards.html">&larr; Zur Liste</a></p>

  <div class="images">
    <img id="front-img" alt="Vorderseite">
    <img id="back-img" alt="Rückseite">
  </div>

  <form id="edit-form">
    <p id="load-status">Lädt …</p>
  </form>

<script>
const FIELDS = [
  ["title", "Titel"], ["category", "Kategorie"], ["theme", "Thema"],
  ["team", "Team"], ["manufacturer", "Hersteller"], ["set_name", "Set"],
  ["season_year", "Saison"], ["card_type", "Typ"], ["variant", "Variante"],
  ["position", "Position"], ["squad_number", "Trikotnummer"],
  ["club_debut_season", "Debütsaison"], ["card_number", "Kartennr."],
  ["serial_number", "Seriennr."], ["print_run", "Auflage"],
];

const params = new URLSearchParams(location.search);
const cardId = params.get("id");
const form = document.getElementById("edit-form");

if (!cardId) {
  form.innerHTML = '<p class="error">Keine Karten-ID angegeben.</p>';
} else {
  loadCard();
}

async function loadCard() {
  try {
    const res = await fetch(`/api/cards/${encodeURIComponent(cardId)}`);
    const card = await res.json();
    if (!res.ok) {
      form.innerHTML = `<p class="error">Fehler: ${card.detail || res.statusText}</p>`;
      return;
    }
    render(card);
  } catch (err) {
    form.innerHTML = `<p class="error">Fehler: ${err}</p>`;
  }
}

function render(card) {
  document.getElementById("front-img").src = card.front_image_url || "";
  document.getElementById("back-img").src = card.back_image_url || "";

  form.innerHTML = "";
  for (const [key, label] of FIELDS) {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const input = document.createElement("input");
    input.type = "text";
    input.name = key;
    input.value = card[key] ?? "";
    wrapper.appendChild(input);
    form.appendChild(wrapper);
  }

  const actions = document.createElement("div");
  actions.className = "actions";

  const saveBtn = document.createElement("button");
  saveBtn.type = "submit";
  saveBtn.textContent = "Speichern";
  actions.appendChild(saveBtn);

  const deleteBtn = document.createElement("button");
  deleteBtn.type = "button";
  deleteBtn.id = "delete-btn";
  deleteBtn.textContent = "Löschen";
  deleteBtn.addEventListener("click", onDelete);
  actions.appendChild(deleteBtn);

  form.appendChild(actions);

  const statusP = document.createElement("p");
  statusP.id = "save-status";
  form.appendChild(statusP);
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fields = {};
  for (const [key] of FIELDS) {
    fields[key] = form.elements[key].value;
  }
  const statusP = document.getElementById("save-status");
  statusP.textContent = "Speichert …";
  statusP.className = "";
  try {
    const res = await fetch(`/api/cards/${encodeURIComponent(cardId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    const body = await res.json();
    if (!res.ok) {
      statusP.textContent = "Fehler: " + (body.detail || res.statusText);
      statusP.className = "error";
      return;
    }
    statusP.textContent = "Gespeichert.";
  } catch (err) {
    statusP.textContent = "Fehler: " + err;
    statusP.className = "error";
  }
});

async function onDelete() {
  if (!confirm("Diese Karte wirklich löschen?")) return;
  try {
    const res = await fetch(`/api/cards/${encodeURIComponent(cardId)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) {
      const body = await res.json();
      alert("Fehler beim Löschen: " + (body.detail || res.statusText));
      return;
    }
    location.href = "cards.html";
  } catch (err) {
    alert("Fehler beim Löschen: " + err);
  }
}
</script>
</body>
</html>
```

- [ ] **Step 2: Manuell verifizieren**

Server läuft weiter (aus Task 4). Über `cards.html` auf eine Karte klicken → `card.html?id=<uuid>` zeigt beide Bilder + vorausgefüllte Felder. Ein Feld ändern, "Speichern" klicken → Erfolgsmeldung, in Supabase (Table Editor) prüfen, dass die Änderung angekommen ist. "Löschen" klicken (mit Bestätigungsdialog) → landet wieder auf `cards.html`, Karte ist weg, in Supabase Storage prüfen, dass die zugehörigen Bilddateien ebenfalls gelöscht wurden.

- [ ] **Step 3: Commit**

```bash
git add webapp-poc/static/card.html
git commit -m "Add card.html: card detail view with edit/delete"
```

---

### Task 6: Navigation verlinken + Doku aktualisieren

**Files:**
- Modify: `webapp-poc/static/index.html`
- Modify: `webapp-poc/README.md`

**Interfaces:** Keine neuen Code-Interfaces - reine Verlinkung/Doku.

- [ ] **Step 1: Link von `index.html` zur Karten-Liste ergänzen**

In `webapp-poc/static/index.html`, direkt nach dem einleitenden `<p>`-Absatz (vor dem `<form id="scan-form">`), einfügen:

```html
  <p><a href="cards.html">Zur Karten-Liste &rarr;</a></p>
```

- [ ] **Step 2: `webapp-poc/README.md` aktualisieren**

Im Abschnitt "Was hier passiert" ergänzen (nach der bestehenden Zeile zu `GET /api/cards`/`GET /api/cards/{id}`):

```markdown
- `static/cards.html` zeigt gespeicherte Karten als durchsuchbare/
  filterbare Liste (Freitext über Titel/Team/Set/Kartennummer,
  Status-Filter); `static/card.html` zeigt eine einzelne Karte im
  Detail zum Bearbeiten/Vervollständigen fehlender Felder oder Löschen.
- `PATCH /api/cards/{id}` aktualisiert einzelne Felder einer Karte,
  `DELETE /api/cards/{id}` löscht eine Karte inkl. ihrer Bilder im
  Storage.
```

Im Abschnitt "Was absichtlich fehlt (kommt in späteren Sub-Projekten)" die Zeile

```markdown
- Inventar-UI zum Bearbeiten/Korrigieren gespeicherter Karten
  (Sub-Projekt 2).
```

entfernen (nicht mehr zutreffend - Sub-Projekt 2 ist damit erledigt).

- [ ] **Step 3: Vollständigen Testlauf verifizieren**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (alle Tests im Repo, keine Regression - dieser Task ändert nur HTML/Markdown, keinen Python-Code)

- [ ] **Step 4: Commit**

```bash
git add webapp-poc/static/index.html webapp-poc/README.md
git commit -m "Link card list from scan page, update webapp-poc README for Sub-Projekt 2"
```

---

## Nach Abschluss

Sub-Projekt 2 ist fertig, wenn: gespeicherte Karten in `cards.html` sichtbar
und durchsuchbar/filterbar sind, sich in `card.html` bearbeiten/
vervollständigen und löschen lassen (inkl. zugehöriger Bilder im
Storage), und alle Tests grün sind. Nächster Schritt: eigene Spec/Plan
für Sub-Projekt 3 (Käufe/Purchases).
