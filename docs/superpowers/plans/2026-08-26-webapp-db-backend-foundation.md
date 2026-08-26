# WebApp DB/Backend-Fundament Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `webapp-poc` bekommt eine echte Persistenzschicht (Supabase Postgres + Storage), sodass `POST /api/scan` gescannte Karten dauerhaft speichert statt nur einmalig JSON zurückzugeben, plus zwei Lese-Endpoints zur Verifikation.

**Architecture:** Zwei neue, fokussierte Module in `webapp-poc/` (`storage.py` für Bild-Kompression/Upload/signierte URLs, `db.py` für Batch-/Karten-Persistenz), beide über einen gemeinsamen `supabase_client.py` mit dem Supabase-Server verbunden. `main.py`'s `/api/scan` ruft beide nach dem bestehenden Crop/Recognize-Schritt auf; neue `GET`-Endpoints lesen zurück.

**Tech Stack:** FastAPI (bestehend), `supabase-py` 2.x (`supabase`, `storage3`, `postgrest` – kommen als Abhängigkeiten von `supabase` mit), Pillow (bestehend, für Kompression), pytest/unittest wie im Rest des Repos.

**Spec:** `docs/superpowers/specs/2026-08-26-webapp-db-backend-foundation-design.md`

## Global Constraints

- Python 3.11 (wie `webapp-poc/Dockerfile` und `.github/workflows/tests.yml`).
- Neue Abhängigkeit: `supabase>=2.0` in `webapp-poc/requirements.txt`; Tests brauchen zusätzlich `httpx` (FastAPI `TestClient`).
- Neue Env-Variablen: `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` (Server-seitiger Service-Role-Key, niemals im Browser). Bestehendes `ANTHROPIC_API_KEY` bleibt unverändert.
- Storage-Bucket-Name exakt `card-images`, privat (nicht public).
- Bild-Kompression vor Upload: max. Kantenlänge 1600px, JPEG-Qualität 85 (Pillow `Image.LANCZOS` fürs Downscaling, wie bereits in `integrations/ai_card_recognition.py` verwendet).
- Signierte URLs: Standard-Gültigkeit 3600 Sekunden (1 Stunde), über `create_signed_url(path, expires_in)`.
- `recognize_card()` darf laut bestehendem Contract nie raisen (siehe `integrations/ai_card_recognition.py` Docstring) – das bleibt unverändert. Neu: DB-/Storage-Fehler pro Karte dürfen ebenfalls nicht den gesamten Batch abbrechen (siehe Spec, Abschnitt Fehlerbehandlung).
- Statustexte/Fehlermeldungen auf Deutsch, im bestehenden Stil (z.B. `"Rückseite für Karte {n:03d} fehlt."`).
- Tabellen-/Spaltennamen exakt wie in der Spec (`scan_batches`, `cards`, Feldliste siehe Task 1).

---

### Task 1: Supabase-Schema (SQL) + Setup-Dokumentation

**Files:**
- Create: `supabase/schema.sql`
- Create: `supabase/README.md`

**Interfaces:**
- Produces: Tabellen `scan_batches`, `cards` und Storage-Bucket `card-images` in einem manuell angelegten Supabase-Projekt. Spätere Tasks (`db.py`, `storage.py`) setzen voraus, dass diese Namen/Spalten exakt so existieren.

Kein Code, daher kein TDD-Zyklus – Verifikation ist manuelles Einspielen durch den Nutzer (Schritt 3).

- [ ] **Step 1: SQL-Schema schreiben**

`supabase/schema.sql`:

```sql
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
```

- [ ] **Step 2: Setup-Doku schreiben**

`supabase/README.md`:

```markdown
# Supabase Setup für DCardsLab WebApp

Einmalige manuelle Einrichtung (kostenloser Free-Tier reicht zum Start:
500 MB DB, 1 GB Storage).

1. Projekt auf https://supabase.com anlegen.
2. Im SQL Editor den Inhalt von `schema.sql` ausführen (legt `scan_batches`
   und `cards` an).
3. Unter Storage einen neuen Bucket `card-images` anlegen, **"Public
   bucket" AUSGESCHALTET lassen** (privat – das Backend erzeugt bei
   Bedarf signierte, zeitlich begrenzte URLs statt dauerhaft offener
   Links).
4. Unter Project Settings → API: `Project URL` und `service_role`
   Secret Key kopieren (NICHT den `anon`-Key – der Service-Role-Key hat
   vollen Server-Zugriff und gehört nur ins Backend-Environment, niemals
   in Frontend-Code).
5. Als Env-Variablen beim Deployment des `webapp-poc`-Containers setzen:
   `SUPABASE_URL` = Project URL, `SUPABASE_SERVICE_KEY` = service_role-Key.

Bekannte Free-Tier-Einschränkung: Projekte pausieren nach 1 Woche ohne
API-Zugriff (im Supabase-Dashboard mit einem Klick reaktivierbar).
```

- [ ] **Step 3: Manuell verifizieren (kein automatischer Test möglich)**

In einem eigenen Supabase-Projekt `schema.sql` ausführen, prüfen dass
`scan_batches` und `cards` im Table Editor erscheinen, Bucket
`card-images` als privat anlegen. (Dieser Schritt läuft außerhalb des
Repos, kein CI-Test.)

- [ ] **Step 4: Commit**

```bash
git add supabase/schema.sql supabase/README.md
git commit -m "Add Supabase schema + setup docs for webapp DB foundation"
```

---

### Task 2: `webapp-poc/supabase_client.py` – gemeinsamer Client

**Files:**
- Create: `webapp-poc/supabase_client.py`
- Test: `tests/test_webapp_poc_supabase_client.py`

**Interfaces:**
- Produces: `get_client()` – gibt einen (pro Prozess einmalig erzeugten) `supabase.Client` zurück, gelesen aus `SUPABASE_URL`/`SUPABASE_SERVICE_KEY`. Task 3 (`db.py`) und Task 4 (`storage.py`) importieren `from supabase_client import get_client` und rufen ausschließlich diese Funktion auf – nie `create_client` direkt –, damit Tests durch Patchen von `supabase_client.create_client` beide Module gleichzeitig abdecken können.

- [ ] **Step 1: Fehlschlagenden Test schreiben**

`tests/test_webapp_poc_supabase_client.py`:

```python
"""Tests for webapp-poc/supabase_client.py - the shared Supabase client
factory used by db.py and storage.py."""
import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))
import supabase_client  # noqa: E402


class GetClientTests(unittest.TestCase):
    def setUp(self):
        supabase_client._client = None
        self.addCleanup(setattr, supabase_client, "_client", None)

    def test_creates_client_from_env_vars(self):
        env = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_KEY": "secret-key"}
        with patch.dict(os.environ, env), patch("supabase_client.create_client") as mock_create:
            mock_create.return_value = "the-client"
            result = supabase_client.get_client()
        mock_create.assert_called_once_with("https://example.supabase.co", "secret-key")
        self.assertEqual(result, "the-client")

    def test_reuses_same_client_instance(self):
        env = {"SUPABASE_URL": "https://example.supabase.co", "SUPABASE_SERVICE_KEY": "secret-key"}
        with patch.dict(os.environ, env), patch("supabase_client.create_client") as mock_create:
            mock_create.return_value = "the-client"
            first = supabase_client.get_client()
            second = supabase_client.get_client()
        mock_create.assert_called_once()
        self.assertIs(first, second)

    def test_missing_env_vars_raises_clear_error(self):
        env = dict(os.environ)
        env.pop("SUPABASE_URL", None)
        env.pop("SUPABASE_SERVICE_KEY", None)
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaises(RuntimeError) as ctx:
                supabase_client.get_client()
        self.assertIn("SUPABASE_URL", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Test laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_supabase_client -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'supabase_client'`

- [ ] **Step 3: Minimale Implementierung**

`webapp-poc/supabase_client.py`:

```python
"""Shared Supabase client factory for db.py and storage.py - both talk to
the same project, so the client (and its env-var lookup) lives in one
place instead of being duplicated."""
import os

from supabase import create_client

_client = None


def get_client():
    global _client
    if _client is None:
        url = os.environ.get("SUPABASE_URL", "").strip()
        key = os.environ.get("SUPABASE_SERVICE_KEY", "").strip()
        if not url or not key:
            raise RuntimeError(
                "SUPABASE_URL und SUPABASE_SERVICE_KEY müssen gesetzt sein."
            )
        _client = create_client(url, key)
    return _client
```

- [ ] **Step 4: Test laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_supabase_client -v`
Expected: PASS (3 Tests)

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/supabase_client.py tests/test_webapp_poc_supabase_client.py
git commit -m "Add shared Supabase client factory for webapp-poc"
```

---

### Task 3: `webapp-poc/storage.py` – Bild-Kompression + Upload + signierte URLs

**Files:**
- Create: `webapp-poc/storage.py`
- Test: `tests/test_webapp_poc_storage.py`

**Interfaces:**
- Consumes: `supabase_client.get_client()` (Task 2).
- Produces: `compress_image(path) -> bytes`, `upload_image(batch_id: str, position: int, side: str, path) -> str` (gibt den Objekt-Pfad im Bucket zurück, z.B. `"<batch_id>/3_front.jpg"`), `signed_url(object_path: str, expires_in: int = 3600) -> str`. Task 5 (`main.py`) ruft `upload_image()` und `signed_url()` auf; Task 6 (`GET`-Endpoints) ruft nur `signed_url()` auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

`tests/test_webapp_poc_storage.py`:

```python
"""Tests for webapp-poc/storage.py - image compression + Supabase Storage
upload/signed-URL wrappers."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))
import storage  # noqa: E402


def _write_fixture_image(tmp_path, size=(2400, 3200)):
    img = Image.new("RGB", size, color=(200, 50, 50))
    path = tmp_path / "fixture.png"
    img.save(path, format="PNG")
    return path


class CompressImageTests(unittest.TestCase):
    def test_downscales_large_image_to_max_edge(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _write_fixture_image(Path(tmp))
            data = storage.compress_image(fixture)
        result_img = Image.open(io.BytesIO(data))
        self.assertLessEqual(max(result_img.size), storage._MAX_EDGE)
        self.assertEqual(result_img.format, "JPEG")

    def test_leaves_small_image_edge_length_unchanged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _write_fixture_image(Path(tmp), size=(400, 300))
            data = storage.compress_image(fixture)
        result_img = Image.open(io.BytesIO(data))
        self.assertEqual(result_img.size, (400, 300))


class UploadImageTests(unittest.TestCase):
    def test_uploads_compressed_bytes_to_expected_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _write_fixture_image(Path(tmp))
            mock_client = MagicMock()
            with patch("storage.get_client", return_value=mock_client):
                object_path = storage.upload_image("batch-123", 3, "front", fixture)

        self.assertEqual(object_path, "batch-123/3_front.jpg")
        mock_client.storage.from_.assert_called_once_with(storage.BUCKET)
        upload_call = mock_client.storage.from_.return_value.upload
        upload_call.assert_called_once()
        args, kwargs = upload_call.call_args
        self.assertEqual(args[0], "batch-123/3_front.jpg")
        self.assertEqual(kwargs["file_options"]["content-type"], "image/jpeg")

    def test_propagates_upload_errors_to_caller(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _write_fixture_image(Path(tmp))
            mock_client = MagicMock()
            mock_client.storage.from_.return_value.upload.side_effect = RuntimeError("bucket down")
            with patch("storage.get_client", return_value=mock_client):
                with self.assertRaises(RuntimeError):
                    storage.upload_image("batch-123", 3, "front", fixture)


class SignedUrlTests(unittest.TestCase):
    def test_returns_signed_url_from_client_response(self):
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.create_signed_url.return_value = {
            "signedURL": "https://example.supabase.co/signed/abc"
        }
        with patch("storage.get_client", return_value=mock_client):
            url = storage.signed_url("batch-123/3_front.jpg")
        self.assertEqual(url, "https://example.supabase.co/signed/abc")
        mock_client.storage.from_.return_value.create_signed_url.assert_called_once_with(
            "batch-123/3_front.jpg", 3600
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_storage -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'storage'`

- [ ] **Step 3: Minimale Implementierung**

`webapp-poc/storage.py`:

```python
"""Card image compression + Supabase Storage upload/signed-URL wrappers.
Images are compressed client-side before upload (see _MAX_EDGE/_JPEG_QUALITY)
so the free-tier 1GB storage quota lasts - full-resolution scanner output
is overkill for web display and eBay listing photos."""
from pathlib import Path

from PIL import Image

from supabase_client import get_client

BUCKET = "card-images"
_MAX_EDGE = 1600
_JPEG_QUALITY = 85


def compress_image(path):
    img = Image.open(path).convert("RGB")
    if max(img.size) > _MAX_EDGE:
        img.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.LANCZOS)
    import io
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=_JPEG_QUALITY)
    return buf.getvalue()


def upload_image(batch_id, position, side, path):
    """side is 'front' or 'back'. Returns the object path within BUCKET.
    Raises whatever the Supabase client raises on failure - callers decide
    how to handle a failed upload for one card without aborting the batch."""
    data = compress_image(Path(path))
    object_path = f"{batch_id}/{position}_{side}.jpg"
    get_client().storage.from_(BUCKET).upload(
        object_path, data, file_options={"content-type": "image/jpeg", "upsert": "true"}
    )
    return object_path


def signed_url(object_path, expires_in=3600):
    response = get_client().storage.from_(BUCKET).create_signed_url(object_path, expires_in)
    return response["signedURL"]
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_storage -v`
Expected: PASS (5 Tests)

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/storage.py tests/test_webapp_poc_storage.py
git commit -m "Add webapp-poc storage module: compress + upload + signed URLs"
```

---

### Task 4: `webapp-poc/db.py` – Batch-/Karten-Persistenz

**Files:**
- Create: `webapp-poc/db.py`
- Test: `tests/test_webapp_poc_db.py`

**Interfaces:**
- Consumes: `supabase_client.get_client()` (Task 2).
- Produces: `CARD_FIELDS` (Liste der 15 Text-Feldnamen, siehe unten), `create_batch(card_count: int) -> str` (Batch-ID), `update_batch_status(batch_id: str, status: str) -> None`, `insert_card(batch_id: str, position_in_batch: int, fields: dict, front_image_path: str | None, back_image_path: str | None) -> dict` (die eingefügte Zeile inkl. `id`), `list_cards() -> list[dict]`, `get_card(card_id: str) -> dict | None`. Task 5 (`main.py`) ruft alle fünf Funktionen auf; Task 6 (`GET`-Endpoints) ruft `list_cards()`/`get_card()` auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

`tests/test_webapp_poc_db.py`:

```python
"""Tests for webapp-poc/db.py - scan_batches/cards persistence via the
Supabase Postgres client."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))
import db  # noqa: E402


def _mock_table(client, table_name, execute_return_data):
    """Wire client.table(table_name)....execute() to return an object
    whose .data is execute_return_data, mirroring postgrest-py's APIResponse."""
    response = MagicMock()
    response.data = execute_return_data
    builder = client.table.return_value
    builder.insert.return_value.execute.return_value = response
    builder.update.return_value.eq.return_value.execute.return_value = response
    builder.select.return_value.order.return_value.execute.return_value = response
    builder.select.return_value.eq.return_value.execute.return_value = response
    return response


class CreateBatchTests(unittest.TestCase):
    def test_inserts_batch_and_returns_id(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "scan_batches", [{"id": "batch-1", "card_count": 9, "status": "pending"}])
        with patch("db.get_client", return_value=mock_client):
            batch_id = db.create_batch(card_count=9)
        self.assertEqual(batch_id, "batch-1")
        mock_client.table.assert_any_call("scan_batches")
        insert_call = mock_client.table.return_value.insert
        insert_call.assert_called_once_with({"card_count": 9, "status": "pending"})


class UpdateBatchStatusTests(unittest.TestCase):
    def test_updates_status_by_id(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "scan_batches", [{"id": "batch-1", "status": "ok"}])
        with patch("db.get_client", return_value=mock_client):
            db.update_batch_status("batch-1", "ok")
        update_call = mock_client.table.return_value.update
        update_call.assert_called_once_with({"status": "ok"})
        update_call.return_value.eq.assert_called_once_with("id", "batch-1")


class InsertCardTests(unittest.TestCase):
    def test_inserts_card_with_all_fields(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "batch_id": "batch-1", "position_in_batch": 3, "title": "Max Mustermann"}
        _mock_table(mock_client, "cards", [saved_row])
        fields = dict.fromkeys(db.CARD_FIELDS, "")
        fields["title"] = "Max Mustermann"
        fields["is_numbered"] = 1
        fields["confidence"] = 90
        fields["status"] = "ok"

        with patch("db.get_client", return_value=mock_client):
            result = db.insert_card("batch-1", 3, fields, "batch-1/3_front.jpg", "batch-1/3_back.jpg")

        self.assertEqual(result, saved_row)
        insert_call = mock_client.table.return_value.insert
        row = insert_call.call_args[0][0]
        self.assertEqual(row["batch_id"], "batch-1")
        self.assertEqual(row["position_in_batch"], 3)
        self.assertEqual(row["title"], "Max Mustermann")
        self.assertIs(row["is_numbered"], True)
        self.assertEqual(row["recognition_status"], "ok")
        self.assertEqual(row["front_image_path"], "batch-1/3_front.jpg")
        self.assertEqual(row["back_image_path"], "batch-1/3_back.jpg")

    def test_is_numbered_false_when_zero(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "cards", [{"id": "card-1"}])
        fields = dict.fromkeys(db.CARD_FIELDS, "")
        fields["is_numbered"] = 0
        fields["confidence"] = 0
        fields["status"] = "nicht erkannt"

        with patch("db.get_client", return_value=mock_client):
            db.insert_card("batch-1", 1, fields, None, None)

        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertIs(row["is_numbered"], False)
        self.assertIsNone(row["front_image_path"])
        self.assertIsNone(row["back_image_path"])


class ListCardsTests(unittest.TestCase):
    def test_returns_all_cards_newest_first(self):
        mock_client = MagicMock()
        rows = [{"id": "card-2"}, {"id": "card-1"}]
        _mock_table(mock_client, "cards", rows)
        with patch("db.get_client", return_value=mock_client):
            result = db.list_cards()
        self.assertEqual(result, rows)
        mock_client.table.return_value.select.return_value.order.assert_called_once_with(
            "created_at", desc=True
        )


class GetCardTests(unittest.TestCase):
    def test_returns_card_when_found(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "cards", [{"id": "card-1", "title": "Karte"}])
        with patch("db.get_client", return_value=mock_client):
            result = db.get_card("card-1")
        self.assertEqual(result, {"id": "card-1", "title": "Karte"})

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "cards", [])
        with patch("db.get_client", return_value=mock_client):
            result = db.get_card("does-not-exist")
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_db -v`
Expected: FAIL mit `ModuleNotFoundError: No module named 'db'`

- [ ] **Step 3: Minimale Implementierung**

`webapp-poc/db.py`:

```python
"""scan_batches/cards persistence via the Supabase Postgres client.
Field names mirror integrations/ai_card_recognition.py's recognize_card()
output 1:1 - duplicated here rather than imported, so this module has no
import-order dependency on integrations/ being on sys.path first."""
from supabase_client import get_client

CARD_FIELDS = [
    "title", "category", "theme", "manufacturer", "set_name",
    "season_year", "card_type", "variant", "team", "position",
    "squad_number", "club_debut_season", "card_number",
    "serial_number", "print_run",
]


def create_batch(card_count):
    response = get_client().table("scan_batches").insert(
        {"card_count": card_count, "status": "pending"}
    ).execute()
    return response.data[0]["id"]


def update_batch_status(batch_id, status):
    get_client().table("scan_batches").update({"status": status}).eq("id", batch_id).execute()


def insert_card(batch_id, position_in_batch, fields, front_image_path, back_image_path):
    row = {name: fields.get(name, "") for name in CARD_FIELDS}
    row.update({
        "batch_id": batch_id,
        "position_in_batch": position_in_batch,
        "is_numbered": bool(fields.get("is_numbered")),
        "confidence": fields.get("confidence"),
        "recognition_status": fields.get("status", ""),
        "front_image_path": front_image_path,
        "back_image_path": back_image_path,
    })
    response = get_client().table("cards").insert(row).execute()
    return response.data[0]


def list_cards():
    response = get_client().table("cards").select("*").order("created_at", desc=True).execute()
    return response.data


def get_card(card_id):
    response = get_client().table("cards").select("*").eq("id", card_id).execute()
    return response.data[0] if response.data else None
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_db -v`
Expected: PASS (7 Tests)

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/db.py tests/test_webapp_poc_db.py
git commit -m "Add webapp-poc db module: scan_batches/cards persistence"
```

---

### Task 5: `POST /api/scan` persistiert statt nur zurückzugeben

**Files:**
- Modify: `webapp-poc/main.py`
- Test: `tests/test_webapp_poc_scan_endpoint.py`

**Interfaces:**
- Consumes: `db.create_batch`, `db.update_batch_status`, `db.insert_card` (Task 4); `storage.upload_image`, `storage.signed_url` (Task 3).
- Produces: `POST /api/scan` Response-Shape `{"batch_id": str, "cards": [{"number": int, "id": str, "front_image_url": str|None, "back_image_url": str|None, "image_error": str|None, ...recognition-Felder, "status": str}]}` - Task 6 liest dieselben Karten-Felder über `GET /api/cards`.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

`tests/test_webapp_poc_scan_endpoint.py`:

```python
"""Tests for POST /api/scan persisting to Supabase (webapp-poc/main.py)."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

for _name in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scanner"))
sys.path.insert(0, str(REPO_ROOT / "integrations"))
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)


def _fake_crop(upload_path, out_dir, quality, rotate):
    """Stand-in for scanner.process(): writes 9 tiny fake card files
    numbered 001..009 into out_dir, mirroring the real crop's output
    contract (a list of 9 file paths) without running OpenCV."""
    out_dir = Path(out_dir)
    files = []
    for n in range(1, 10):
        p = out_dir / f"{n:03d}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0fake-card-image")
        files.append(str(p))
    return files


def _fake_recognize(front_path=None, back_path=None):
    return {
        "title": "Max Mustermann", "category": "Fußball", "theme": "", "manufacturer": "Topps",
        "set_name": "", "season_year": "2024", "card_type": "", "variant": "", "team": "FC Test",
        "position": "", "squad_number": "", "club_debut_season": "", "card_number": "12",
        "serial_number": "", "print_run": "", "is_numbered": 0, "confidence": 90, "raw": "",
        "status": "ok",
    }


class ScanEndpointPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.batch_ids = iter(["batch-1"])
        self.card_counter = 0

        def fake_insert_card(batch_id, position, fields, front_path, back_path):
            self.card_counter += 1
            return {"id": f"card-{position}", "batch_id": batch_id, "position_in_batch": position}

        patches = {
            "main.scanner.process": MagicMock(side_effect=_fake_crop),
            "main.recognize_card": MagicMock(side_effect=_fake_recognize),
            "main.db.create_batch": MagicMock(return_value="batch-1"),
            "main.db.update_batch_status": MagicMock(),
            "main.db.insert_card": MagicMock(side_effect=fake_insert_card),
            "main.storage.upload_image": MagicMock(side_effect=lambda batch_id, pos, side, path: f"{batch_id}/{pos}_{side}.jpg"),
            "main.storage.signed_url": MagicMock(side_effect=lambda object_path, **_: f"https://signed/{object_path}"),
        }
        self._patchers = [patch(target, new) for target, new in patches.items()]
        self.mocks = {}
        for target, p in zip(patches, self._patchers):
            self.mocks[target] = p.start()
            self.addCleanup(p.stop)

    def _post_scan(self):
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        return client.post("/api/scan", files=files)

    def test_creates_one_batch_for_the_scan(self):
        self._post_scan()
        self.mocks["main.db.create_batch"].assert_called_once_with(card_count=9)

    def test_uploads_both_images_for_every_card(self):
        self._post_scan()
        self.assertEqual(self.mocks["main.storage.upload_image"].call_count, 18)

    def test_inserts_nine_cards(self):
        self._post_scan()
        self.assertEqual(self.mocks["main.db.insert_card"].call_count, 9)

    def test_marks_batch_ok_when_all_cards_succeed(self):
        self._post_scan()
        self.mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "ok")

    def test_response_includes_batch_id_and_card_ids_and_urls(self):
        response = self._post_scan()
        body = response.json()
        self.assertEqual(body["batch_id"], "batch-1")
        self.assertEqual(len(body["cards"]), 9)
        first = body["cards"][0]
        self.assertEqual(first["id"], "card-1")
        self.assertEqual(first["front_image_url"], "https://signed/batch-1/1_front.jpg")
        self.assertEqual(first["title"], "Max Mustermann")

    def test_image_upload_failure_marks_only_that_card(self):
        def upload_side_effect(batch_id, pos, side, path):
            if pos == 5 and side == "front":
                raise RuntimeError("bucket down")
            return f"{batch_id}/{pos}_{side}.jpg"
        self.mocks["main.storage.upload_image"].side_effect = upload_side_effect

        response = self._post_scan()

        body = response.json()
        self.assertEqual(self.mocks["main.db.insert_card"].call_count, 9)
        failed_card = next(c for c in body["cards"] if c["number"] == 5)
        self.assertIn("bucket down", failed_card["image_error"])
        ok_card = next(c for c in body["cards"] if c["number"] == 1)
        self.assertNotIn("image_error", ok_card)
        self.mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "partial")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_scan_endpoint -v`
Expected: FAIL - `main.db`/`main.storage` existieren noch nicht (`AttributeError` bzw. `ModuleNotFoundError`), da `main.py` `db`/`storage` noch nicht importiert.

- [ ] **Step 3: `main.py` um Persistenz erweitern**

`webapp-poc/main.py` - Imports ergänzen (nach der bestehenden `ai_card_recognition`-Import-Zeile):

```python
import db  # noqa: E402
import storage  # noqa: E402
```

`scan()` komplett ersetzen durch:

```python
@app.post("/api/scan")
async def scan(front: UploadFile = File(...), back: UploadFile = File(...)):
    with tempfile.TemporaryDirectory(prefix="dcardslab_poc_") as tmp_str:
        tmp = Path(tmp_str)
        front_path = tmp / f"front{Path(front.filename or 'front.jpg').suffix}"
        back_path = tmp / f"back{Path(back.filename or 'back.jpg').suffix}"
        front_path.write_bytes(await front.read())
        back_path.write_bytes(await back.read())

        front_dir, back_dir = tmp / "front_cards", tmp / "back_cards"
        front_dir.mkdir()
        back_dir.mkdir()

        try:
            front_files = _crop_side(front_path, front_dir)
            back_files = _crop_side(back_path, back_dir)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        back_map = {int(p.stem): p for p in back_files}
        batch_id = db.create_batch(card_count=len(front_files))

        def process_one(fp):
            number = int(fp.stem)
            bp = back_map.get(number)
            if bp is None:
                fields = dict(EMPTY_FIELDS, status=f"Rückseite für Karte {number:03d} fehlt.")
                card_row = db.insert_card(batch_id, number, fields, None, None)
                return {"number": number, **fields, "id": card_row["id"]}

            fields = recognize_card(front_path=fp, back_path=bp)

            front_image_path = back_image_path = None
            image_error = None
            try:
                front_image_path = storage.upload_image(batch_id, number, "front", fp)
                back_image_path = storage.upload_image(batch_id, number, "back", bp)
            except Exception as exc:
                image_error = str(exc)

            card_row = db.insert_card(batch_id, number, fields, front_image_path, back_image_path)
            result = {"number": number, **fields, "id": card_row["id"]}
            if front_image_path:
                result["front_image_url"] = storage.signed_url(front_image_path)
            if back_image_path:
                result["back_image_url"] = storage.signed_url(back_image_path)
            if image_error:
                result["image_error"] = image_error
            return result

        # Same pattern as pair_and_ocr() in the desktop app: recognize_card()
        # is a network round-trip, so a handful of cards run concurrently
        # instead of 9 sequential API calls.
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(process_one, front_files))

    results.sort(key=lambda r: r["number"])

    success_count = sum(
        1 for r in results if r.get("status") == "ok" and "image_error" not in r
    )
    if success_count == len(results):
        batch_status = "ok"
    elif success_count == 0:
        batch_status = "failed"
    else:
        batch_status = "partial"
    db.update_batch_status(batch_id, batch_status)

    return JSONResponse({"batch_id": batch_id, "cards": results})
```

`EMPTY_FIELDS` importieren (ergänzt die bestehende Import-Zeile):

```python
from ai_card_recognition import recognize_card, EMPTY_FIELDS  # noqa: E402
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_scan_endpoint -v`
Expected: PASS (6 Tests)

- [ ] **Step 5: Bestehende Tests gegenprüfen (Regression)**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (alle bisherigen + neuen Tests, keine Regression in `test_ai_card_recognition.py` etc.)

- [ ] **Step 6: Commit**

```bash
git add webapp-poc/main.py tests/test_webapp_poc_scan_endpoint.py
git commit -m "Persist scan results to Supabase in POST /api/scan"
```

---

### Task 6: `GET /api/cards` und `GET /api/cards/{id}`

**Files:**
- Modify: `webapp-poc/main.py`
- Test: `tests/test_webapp_poc_cards_endpoints.py`

**Interfaces:**
- Consumes: `db.list_cards`, `db.get_card` (Task 4), `storage.signed_url` (Task 3).
- Produces: `GET /api/cards` → `{"cards": [...]}` (jede Karte mit frisch signierten `front_image_url`/`back_image_url`, falls ein `*_image_path` gesetzt ist); `GET /api/cards/{id}` → einzelnes Karten-Objekt oder 404.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

`tests/test_webapp_poc_cards_endpoints.py`:

```python
"""Tests for GET /api/cards and GET /api/cards/{id} (webapp-poc/main.py)."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

for _name in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scanner"))
sys.path.insert(0, str(REPO_ROOT / "integrations"))
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)


class ListCardsEndpointTests(unittest.TestCase):
    def test_returns_cards_with_signed_urls(self):
        rows = [
            {"id": "card-1", "title": "Karte 1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"},
            {"id": "card-2", "title": "Karte 2", "front_image_path": None, "back_image_path": None},
        ]
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.storage.signed_url", side_effect=lambda p, **_: f"https://signed/{p}"):
            response = client.get("/api/cards")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["cards"]), 2)
        self.assertEqual(body["cards"][0]["front_image_url"], "https://signed/b1/1_front.jpg")
        self.assertNotIn("front_image_url", body["cards"][1])


class GetCardEndpointTests(unittest.TestCase):
    def test_returns_card_with_signed_urls(self):
        row = {"id": "card-1", "title": "Karte 1", "front_image_path": "b1/1_front.jpg", "back_image_path": None}
        with patch("main.db.get_card", return_value=row), \
             patch("main.storage.signed_url", return_value="https://signed/b1/1_front.jpg"):
            response = client.get("/api/cards/card-1")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["front_image_url"], "https://signed/b1/1_front.jpg")

    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_card", return_value=None):
            response = client.get("/api/cards/does-not-exist")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_cards_endpoints -v`
Expected: FAIL mit 404 "Not Found" (Routen existieren noch nicht) bzw. `AttributeError` für `main.db.list_cards`.

- [ ] **Step 3: Endpoints implementieren**

`webapp-poc/main.py` - vor der `static_dir`-Zeile ergänzen:

```python
def _attach_signed_urls(card):
    card = dict(card)
    front_path = card.get("front_image_path")
    back_path = card.get("back_image_path")
    if front_path:
        card["front_image_url"] = storage.signed_url(front_path)
    if back_path:
        card["back_image_url"] = storage.signed_url(back_path)
    return card


@app.get("/api/cards")
async def list_cards():
    cards = [_attach_signed_urls(c) for c in db.list_cards()]
    return JSONResponse({"cards": cards})


@app.get("/api/cards/{card_id}")
async def get_card(card_id: str):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    return JSONResponse(_attach_signed_urls(card))
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_cards_endpoints -v`
Expected: PASS (3 Tests)

- [ ] **Step 5: Bestehende Tests gegenprüfen (Regression)**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (alle Tests, inkl. Task 5)

- [ ] **Step 6: Commit**

```bash
git add webapp-poc/main.py tests/test_webapp_poc_cards_endpoints.py
git commit -m "Add GET /api/cards and GET /api/cards/{id} endpoints"
```

---

### Task 7: Deployment-Wiring (Dependencies, Docker, CI, Doku)

**Files:**
- Modify: `webapp-poc/requirements.txt`
- Modify: `docker-compose.webapp-poc.yml`
- Modify: `webapp-poc/README.md`
- Modify: `.github/workflows/tests.yml`

**Interfaces:** Keine neuen Code-Interfaces - reine Konfiguration/Doku, damit die vorherigen Tasks in CI laufen und auf dem NAS deploybar sind.

- [ ] **Step 1: `webapp-poc/requirements.txt` erweitern**

Ergänzen (nach `pydantic>=2.0`):

```
supabase>=2.0
httpx>=0.27
```

(`httpx` wird von FastAPIs `TestClient` gebraucht, ist aber keine Laufzeit-Abhängigkeit von `main.py` selbst - hier trotzdem mit rein, weil `webapp-poc/requirements.txt` die einzige Datei ist, die sowohl das Docker-Image als auch die CI-Tests installieren.)

- [ ] **Step 2: `docker-compose.webapp-poc.yml` um Supabase-Env-Variablen erweitern**

```yaml
services:
  dcardslab-webapp-poc:
    build:
      context: .
      dockerfile: webapp-poc/Dockerfile
    container_name: dcardslab-webapp-poc
    ports:
      - "8000:8000"
    environment:
      - ANTHROPIC_API_KEY=${ANTHROPIC_API_KEY}
      - SUPABASE_URL=${SUPABASE_URL}
      - SUPABASE_SERVICE_KEY=${SUPABASE_SERVICE_KEY}
    restart: unless-stopped
```

- [ ] **Step 3: `.github/workflows/tests.yml` - webapp-poc-Dependencies installieren**

Den bestehenden Schritt `Install AI recognition test dependencies` ersetzen durch:

```yaml
      - name: Install webapp-poc test dependencies
        run: pip install -r webapp-poc/requirements.txt
```

(deckt `anthropic`/`pydantic` weiterhin ab, da beide schon in
`webapp-poc/requirements.txt` stehen - plus `fastapi`, `opencv-python-headless`,
`supabase`, `httpx` für die neuen Tests.)

- [ ] **Step 4: `webapp-poc/README.md` aktualisieren**

Im Abschnitt "Was hier passiert" ergänzen (nach der bestehenden Liste):

```markdown
- Persistiert jede gescannte Karte in Supabase Postgres (`scan_batches`,
  `cards`) und lädt die (komprimierten) Kartenbilder in Supabase Storage
  hoch - siehe `supabase/README.md` für die einmalige Projekt-Einrichtung.
- `GET /api/cards` und `GET /api/cards/{id}` lesen gespeicherte Karten
  inkl. frisch signierter Bild-URLs zurück.
```

Im Abschnitt "Was absichtlich fehlt" den Punkt "Keine Datenbank/Persistenz..."
entfernen (nicht mehr zutreffend) und durch einen Verweis auf die
Folge-Sub-Projekte ersetzen:

```markdown
## Was absichtlich fehlt (kommt in späteren Sub-Projekten)

- Inventar-UI zum Bearbeiten/Korrigieren gespeicherter Karten
  (Sub-Projekt 2).
- Käufe/Purchases (Sub-Projekt 3).
- eBay-Listing-Erstellung/-Export/-Sales-Sync (Sub-Projekt 4).
- Google Drive/Sheets-Sync, Backups (Sub-Projekt 5).
- Kein Build-Frontend (React/Next) - weiterhin nur die statische Testseite.
```

Im Abschnitt "Starten auf dem NAS (Docker)" bei `docker run` ergänzen:

```bash
docker run -d --name dcardslab-webapp-poc -p 8000:8000 \
  -e ANTHROPIC_API_KEY=dein-api-key \
  -e SUPABASE_URL=dein-supabase-project-url \
  -e SUPABASE_SERVICE_KEY=dein-supabase-service-role-key \
  dcardslab-webapp-poc
```

- [ ] **Step 5: Vollständigen Testlauf verifizieren**

Run: `pip install -r webapp-poc/requirements.txt && python3 -m unittest discover -s tests -v`
Expected: PASS (alle Tests im Repo, inkl. aller in Task 1-6 neu hinzugekommenen)

- [ ] **Step 6: Commit**

```bash
git add webapp-poc/requirements.txt docker-compose.webapp-poc.yml webapp-poc/README.md .github/workflows/tests.yml
git commit -m "Wire Supabase env vars, deps and CI for webapp DB foundation"
```

---

## Nach Abschluss

Sub-Projekt 1 ist fertig, wenn: `POST /api/scan` gescannte Karten
dauerhaft in Supabase speichert (DB-Zeilen + Bilder im Storage-Bucket),
`GET /api/cards`/`GET /api/cards/{id}` das zurücklesen können, alle Tests
grün sind und der Nutzer das Supabase-Projekt einmalig gemäß
`supabase/README.md` eingerichtet hat. Nächster Schritt: eigene
Spec/Plan für Sub-Projekt 2 (Inventar-Verwaltung im Web-Frontend).
