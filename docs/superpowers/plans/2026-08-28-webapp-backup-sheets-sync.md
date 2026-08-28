# WebApp Backup + Google-Sheets-Sync Implementation Plan

**Goal:** Eine Backup-ZIP (alle Supabase-Tabellen + alle Kartenbilder)
per Klick herunterladen können, plus ein manueller, einseitiger
Supabase→Google-Sheets-Sync (Karten/Käufe/eBay) über einen
redirect-basierten Google-OAuth-Flow direkt in `webapp-poc`.

**Architecture:** Kein neuer Service. `google_sheets_client.py` (neu)
portiert den bewährten `ebay-oauth-server`-OAuth-Flow (Redirect +
State-Param + Token-Austausch) auf Google, komplett per `httpx`-REST
ohne neue Python-Pakete. `backup.py` (neu) baut die ZIP in-memory.
`db.py` bekommt CRUD für eine neue Singleton-Settings-Tabelle plus
sechs Lesefunktionen für den Backup-Export. Neue `/api/sheets/*`- und
`/api/backup`-Endpoints in `main.py`, eine neue Seite
`static/settings.html`.

**Tech Stack:** FastAPI, Supabase Postgres (bestehend), `httpx`
(bereits Dependency, keine neuen Pakete), Vanilla JS/HTML.

**Spec:** `docs/superpowers/specs/2026-08-28-webapp-backup-sheets-sync-design.md`
(Status: Freigegeben)

## Global Constraints

- Keine neuen Python-Pakete — `google_sheets_client.py` nutzt
  ausschließlich `httpx` für OAuth-Token-Austausch und die Sheets-API
  v4 (REST), analog zu `ebay_client.py`.
- Google-Refresh-Token liegt in Supabase (`google_sheets_settings`,
  Singleton-Row via `check (id)`), **nicht** in einer lokalen Datei —
  `webapp-poc`s Container hat kein persistentes Volume.
- OAuth-Flow verwendet einen CSRF-`state`-Parameter mit In-Memory-
  Ablauf (gleiches Muster wie `ebay-oauth-server/app.py`s `_states`),
  kein neuer Persistenzbedarf dafür (State lebt nur Sekunden bis
  Minuten).
- Supabase-/Google-HTTP-Aufrufe werden in Tests gemockt (kein echter
  Netzwerk-Call in CI), gleiches Muster wie der Rest des Projekts.
- Deutsche Statustexte/Fehlermeldungen im bestehenden Stil.
- Kein neues JS-Framework, kein Build-Schritt.

---

### Task 1: Supabase-Schema erweitern (`google_sheets_settings`)

**Files:**
- Modify: `supabase/schema.sql`
- Modify: `supabase/README.md`

**Interfaces:**
- Produces: Tabelle `google_sheets_settings` (exaktes DDL: Spec-
  Abschnitt "Datenmodell"). Task 3 (`db.py`) setzt exakt diese
  Spalten voraus.

Kein Code, daher kein TDD-Zyklus.

- [ ] **Step 1:** DDL aus der Spec unverändert ans Ende von
  `supabase/schema.sql` anhängen.
- [ ] **Step 2:** Nutzer bittet, den kompletten `schema.sql`-Inhalt
  erneut im Supabase SQL Editor auszuführen; prüfen, dass
  `google_sheets_settings` im Table Editor erscheint.
- [ ] **Step 3:** `supabase/README.md` um einen kurzen Hinweis auf die
  neue Tabelle ergänzen (gleiche Stelle/Formulierung wie der
  bestehende Hinweis zu erneutem Schema-Einspielen).
- [ ] **Step 4: Commit**

```bash
git add supabase/schema.sql supabase/README.md
git commit -m "Add google_sheets_settings table to Supabase schema"
```

---

### Task 2: `webapp-poc/google_sheets_client.py` – OAuth + Sheets-API-Client

**Files:**
- Create: `webapp-poc/google_sheets_client.py`
- Create: `tests/test_webapp_poc_google_sheets_client.py`

**Interfaces:**
- Consumes: `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/
  `GOOGLE_REDIRECT_URI` (Env-Vars), Googles OAuth2-Token-Endpoint,
  Sheets-API v4 (beide per `httpx`, in Tests gemockt).
- Produces:
  - `GoogleNotConnectedError`, `GoogleApiError` (Exceptions)
  - `SCOPES = "https://www.googleapis.com/auth/spreadsheets"`
  - `authorization_url(state: str) -> str`
  - `exchange_code(code: str) -> dict` (roh: `access_token`,
    `refresh_token`, `expires_in`, ...)
  - `refresh_access_token(refresh_token: str) -> str` (Access-Token)
  - `sync_to_sheets(access_token: str, spreadsheet_id: str, tabs: dict[str, tuple[list, list[list]]]) -> None`
    — `tabs` ist `{titel: (headers, rows)}`, ein Aufruf pro Tab
    (Clear + Update + Sheet-Anlegen falls fehlt + Frozen-Row).

Task 5 (`main.py`) ruft alle mit exakt dieser Signatur auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

```python
"""Tests for webapp-poc/google_sheets_client.py. All HTTP is mocked,
same depth as tests/test_webapp_poc_ebay_client.py."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import google_sheets_client  # noqa: E402


def _response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text or str(json_data or "")
    return resp


class AuthorizationUrlTests(unittest.TestCase):
    def test_includes_state_and_scope(self):
        with patch.object(google_sheets_client, "CLIENT_ID", "cid"), \
             patch.object(google_sheets_client, "REDIRECT_URI", "https://x/callback"):
            url = google_sheets_client.authorization_url("state-123")
        self.assertIn("state=state-123", url)
        self.assertIn("client_id=cid", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)


class ExchangeCodeTests(unittest.TestCase):
    def test_returns_token_dict(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(200, {"access_token": "a", "refresh_token": "r"})):
            token = google_sheets_client.exchange_code("auth-code")
        self.assertEqual(token["refresh_token"], "r")

    def test_raises_on_error(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(400, {"error": "invalid_grant"}, text="invalid_grant")):
            with self.assertRaises(google_sheets_client.GoogleApiError):
                google_sheets_client.exchange_code("bad-code")


class RefreshAccessTokenTests(unittest.TestCase):
    def test_returns_access_token(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(200, {"access_token": "fresh"})):
            token = google_sheets_client.refresh_access_token("refresh-tok")
        self.assertEqual(token, "fresh")

    def test_raises_not_connected_on_invalid_grant(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(400, {"error": "invalid_grant"}, text="invalid_grant")):
            with self.assertRaises(google_sheets_client.GoogleNotConnectedError):
                google_sheets_client.refresh_access_token("stale-tok")


class SyncToSheetsTests(unittest.TestCase):
    def _meta_response(self, titles):
        return _response(200, {"sheets": [
            {"properties": {"title": t, "sheetId": i}} for i, t in enumerate(titles)
        ]})

    def test_creates_missing_tab_then_writes_values(self):
        responses = [
            self._meta_response([]),  # get spreadsheet metadata
            _response(200, {}),  # batchUpdate: addSheet
            self._meta_response(["Karten"]),  # re-fetch metadata for sheetId
            _response(200, {}),  # values.clear
            _response(200, {}),  # values.update
            _response(200, {}),  # batchUpdate: freeze header row
        ]
        with patch("google_sheets_client.httpx.request", side_effect=responses) as mock_request:
            google_sheets_client.sync_to_sheets("tok", "sheet-1", {"Karten": (["ID", "Titel"], [["1", "Max"]])})
        self.assertGreaterEqual(mock_request.call_count, 4)

    def test_raises_on_google_error(self):
        with patch("google_sheets_client.httpx.request", return_value=_response(403, {"error": "forbidden"}, text="forbidden")):
            with self.assertRaises(google_sheets_client.GoogleApiError):
                google_sheets_client.sync_to_sheets("tok", "sheet-1", {"Karten": ([], [])})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_google_sheets_client -v`
  → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: `webapp-poc/google_sheets_client.py` implementieren**

```python
"""httpx-based Google OAuth2 + Sheets API v4 client - no google-auth/
google-api-python-client SDK, same lightweight REST approach as
ebay_client.py. The desktop app's integrations/google_sheets_sync.py
uses the heavier SDK because it needs InstalledAppFlow's local-browser
flow; webapp-poc runs headless on the NAS and only needs plain OAuth2
authorization-code exchange + REST calls, both trivial over httpx."""
import os
from urllib.parse import urlencode

import httpx

CLIENT_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
CLIENT_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
REDIRECT_URI = os.environ.get("GOOGLE_REDIRECT_URI", "").strip()

AUTH_BASE = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SHEETS_BASE = "https://sheets.googleapis.com/v4/spreadsheets"
SCOPES = "https://www.googleapis.com/auth/spreadsheets"


class GoogleNotConnectedError(Exception):
    """No valid refresh token, or Google rejected it (revoked access)."""


class GoogleApiError(Exception):
    """Google rejected a request; args[0] is the raw error text."""


def authorization_url(state):
    params = {
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "response_type": "code",
        "scope": SCOPES,
        "access_type": "offline",
        # Forces Google to issue a refresh_token even on a re-connect,
        # not just on the very first consent for this account.
        "prompt": "consent",
        "state": state,
    }
    return f"{AUTH_BASE}?{urlencode(params)}"


def exchange_code(code):
    response = httpx.post(TOKEN_URL, data={
        "code": code, "client_id": CLIENT_ID, "client_secret": CLIENT_SECRET,
        "redirect_uri": REDIRECT_URI, "grant_type": "authorization_code",
    }, timeout=30)
    if response.status_code >= 400:
        raise GoogleApiError(response.text)
    return response.json()


def refresh_access_token(refresh_token):
    response = httpx.post(TOKEN_URL, data={
        "refresh_token": refresh_token, "client_id": CLIENT_ID,
        "client_secret": CLIENT_SECRET, "grant_type": "refresh_token",
    }, timeout=30)
    if response.status_code >= 400:
        # invalid_grant means the refresh token was revoked/expired -
        # the user must reconnect, distinct from a transient API error.
        if "invalid_grant" in response.text:
            raise GoogleNotConnectedError(
                "Google-Verbindung ist abgelaufen — bitte auf der Einstellungen-Seite erneut verbinden."
            )
        raise GoogleApiError(response.text)
    return response.json()["access_token"]


def _headers(access_token):
    return {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}


def _sheets_request(method, access_token, path, json_body=None):
    response = httpx.request(method, SHEETS_BASE + path, headers=_headers(access_token), json=json_body, timeout=45)
    if response.status_code >= 400:
        raise GoogleApiError(response.text)
    return response


def _ensure_tab(access_token, spreadsheet_id, title):
    meta = _sheets_request("GET", access_token, f"/{spreadsheet_id}").json()
    existing = {s["properties"]["title"]: s["properties"]["sheetId"] for s in meta.get("sheets", [])}
    if title in existing:
        return existing[title]
    _sheets_request("POST", access_token, f"/{spreadsheet_id}:batchUpdate", {
        "requests": [{"addSheet": {"properties": {"title": title}}}],
    })
    meta = _sheets_request("GET", access_token, f"/{spreadsheet_id}").json()
    return next(s["properties"]["sheetId"] for s in meta["sheets"] if s["properties"]["title"] == title)


def _write_tab(access_token, spreadsheet_id, title, headers, rows):
    sheet_id = _ensure_tab(access_token, spreadsheet_id, title)
    _sheets_request("POST", access_token, f"/{spreadsheet_id}/values/'{title}'!A:ZZ:clear")
    _sheets_request("PUT", access_token, f"/{spreadsheet_id}/values/'{title}'!A1?valueInputOption=USER_ENTERED", {
        "values": [headers] + rows,
    })
    _sheets_request("POST", access_token, f"/{spreadsheet_id}:batchUpdate", {
        "requests": [{
            "updateSheetProperties": {
                "properties": {"sheetId": sheet_id, "gridProperties": {"frozenRowCount": 1}},
                "fields": "gridProperties.frozenRowCount",
            },
        }],
    })


def sync_to_sheets(access_token, spreadsheet_id, tabs):
    """tabs: {title: (headers, rows)}."""
    for title, (headers, rows) in tabs.items():
        _write_tab(access_token, spreadsheet_id, title, headers, rows)
```

(`_write_tab()`s Clear/Update/Freeze-Reihenfolge entspricht 1:1
`integrations/google_sheets_sync.py`s `write_tab()`, nur als REST-
Aufrufe statt SDK-Methodenaufrufe.)

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_google_sheets_client -v`
  → PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/google_sheets_client.py tests/test_webapp_poc_google_sheets_client.py
git commit -m "Add google_sheets_client.py: httpx-based Google OAuth + Sheets API v4 client"
```

---

### Task 3: `webapp-poc/db.py` – Settings-CRUD + Backup-Lesefunktionen

**Files:**
- Modify: `webapp-poc/db.py`
- Modify: `tests/test_webapp_poc_db.py`

**Interfaces:**
- Produces:
  - `get_google_sheets_settings() -> dict | None`
  - `save_google_sheets_settings(fields: dict) -> dict` (`upsert` mit
    `id=True`, erlaubte Felder: `refresh_token`, `spreadsheet_id`,
    `connected_at`, `last_synced_at`)
  - `all_scan_batches() -> list[dict]`, `all_cards() -> list[dict]`,
    `all_purchases() -> list[dict]`, `all_purchase_items() -> list[dict]`,
    `all_ebay_listings() -> list[dict]`, `all_ebay_sales() -> list[dict]`
    (je ein ungefiltertes `select("*")`, fürs Backup — Task 4 nutzt sie).

- [ ] **Step 1: Fehlschlagende Tests schreiben**

Gleicher Aufbau wie bestehende `db.py`-Testklassen (`_mock_client_for_tables()`-
Helper). Testfälle:

- `get_google_sheets_settings`: `None` bei leerer Tabelle, sonst die
  eine Zeile.
- `save_google_sheets_settings`: `upsert`-Aufruf enthält `id=True` und
  nur erlaubte Felder.
- Jede `all_*()`-Funktion: ruft `select("*")` auf der jeweiligen
  Tabelle auf, gibt `response.data` zurück.

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_db -v` → FAIL.

- [ ] **Step 3: Funktionen ergänzen**

```python
GOOGLE_SHEETS_SETTINGS_FIELDS = {"refresh_token", "spreadsheet_id", "connected_at", "last_synced_at"}


def get_google_sheets_settings():
    response = get_client().table("google_sheets_settings").select("*").execute()
    return response.data[0] if response.data else None


def save_google_sheets_settings(fields):
    row = {name: value for name, value in fields.items() if name in GOOGLE_SHEETS_SETTINGS_FIELDS}
    row["id"] = True
    response = get_client().table("google_sheets_settings").upsert(row).execute()
    return response.data[0]


def all_scan_batches():
    return get_client().table("scan_batches").select("*").execute().data


def all_cards():
    return get_client().table("cards").select("*").execute().data


def all_purchases():
    return get_client().table("purchases").select("*").execute().data


def all_purchase_items():
    return get_client().table("purchase_items").select("*").execute().data


def all_ebay_listings():
    return get_client().table("ebay_listings").select("*").execute().data


def all_ebay_sales():
    return get_client().table("ebay_sales").select("*").execute().data
```

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_db -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/db.py tests/test_webapp_poc_db.py
git commit -m "Add google_sheets_settings persistence and backup read functions to db.py"
```

---

### Task 4: `webapp-poc/backup.py` – Backup-ZIP-Erzeugung

**Files:**
- Create: `webapp-poc/backup.py`
- Create: `tests/test_webapp_poc_backup.py`

**Interfaces:**
- Consumes: `db.all_*()` (Task 3), `storage.py`s Supabase-Client (für
  Bild-Downloads).
- Produces: `build_backup_zip() -> bytes` (fertige ZIP-Datei als Bytes).

- [ ] **Step 1: Fehlschlagende Tests schreiben**

```python
"""Tests for webapp-poc/backup.py."""
import io
import sys
import zipfile
from pathlib import Path
from unittest.mock import MagicMock, patch
import unittest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import backup  # noqa: E402


def _cards():
    return [
        {"id": "c1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"},
        {"id": "c2", "front_image_path": None, "back_image_path": None},
    ]


class BuildBackupZipTests(unittest.TestCase):
    def _patch_db(self, **overrides):
        patches = {
            "backup.db.all_scan_batches": MagicMock(return_value=[{"id": "b1"}]),
            "backup.db.all_cards": MagicMock(return_value=_cards()),
            "backup.db.all_purchases": MagicMock(return_value=[]),
            "backup.db.all_purchase_items": MagicMock(return_value=[]),
            "backup.db.all_ebay_listings": MagicMock(return_value=[]),
            "backup.db.all_ebay_sales": MagicMock(return_value=[]),
        }
        patches.update(overrides)
        patchers = [patch(target, new) for target, new in patches.items()]
        for p in patchers:
            self.addCleanup(p.stop)
        return {target: p.start() for target, p in zip(patches, patchers)}

    def test_contains_all_table_json_files(self):
        self._patch_db()
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.return_value = b"fake-image-bytes"
        with patch("backup.get_client", return_value=mock_client):
            data = backup.build_backup_zip()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        for table in ("scan_batches", "cards", "purchases", "purchase_items", "ebay_listings", "ebay_sales"):
            self.assertIn(f"{table}.json", names)

    def test_includes_images_for_cards_that_have_them(self):
        self._patch_db()
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.return_value = b"fake-image-bytes"
        with patch("backup.get_client", return_value=mock_client):
            data = backup.build_backup_zip()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        self.assertIn("images/b1/1_front.jpg", names)
        self.assertIn("images/b1/1_back.jpg", names)

    def test_skips_image_that_fails_to_download_instead_of_crashing(self):
        self._patch_db()
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.side_effect = RuntimeError("Storage down")
        with patch("backup.get_client", return_value=mock_client):
            data = backup.build_backup_zip()  # must not raise
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        self.assertIn("cards.json", names)  # tables still present
        self.assertNotIn("images/b1/1_front.jpg", names)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_backup -v` → FAIL.

- [ ] **Step 3: `webapp-poc/backup.py` implementieren**

```python
"""Builds a single ZIP backup of every Supabase table (as JSON) plus
every card image - an independent copy outside Supabase, since the
Free-Tier project pauses after a week without API access (see
supabase/README.md)."""
import io
import json
import zipfile

import db
from storage import BUCKET
from supabase_client import get_client

_TABLES = {
    "scan_batches": db.all_scan_batches,
    "cards": db.all_cards,
    "purchases": db.all_purchases,
    "purchase_items": db.all_purchase_items,
    "ebay_listings": db.all_ebay_listings,
    "ebay_sales": db.all_ebay_sales,
}


def build_backup_zip():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        cards = []
        for table, reader in _TABLES.items():
            rows = reader()
            if table == "cards":
                cards = rows
            zf.writestr(f"{table}.json", json.dumps(rows, ensure_ascii=False, indent=2, default=str))

        for card in cards:
            for path_key in ("front_image_path", "back_image_path"):
                object_path = card.get(path_key)
                if not object_path:
                    continue
                try:
                    data = get_client().storage.from_(BUCKET).download(object_path)
                except Exception:
                    # One failed image (transient Storage hiccup, deleted
                    # object) must not abort the whole backup - the JSON
                    # tables are still the primary value here.
                    continue
                zf.writestr(f"images/{object_path}", data)
    return buf.getvalue()
```

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_backup -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/backup.py tests/test_webapp_poc_backup.py
git commit -m "Add backup.py: in-memory ZIP of all tables + card images"
```

---

### Task 5: `webapp-poc/main.py` – `/api/sheets/*` und `/api/backup`

**Files:**
- Modify: `webapp-poc/main.py`
- Create: `tests/test_webapp_poc_sheets_endpoints.py`

**Interfaces:**
- Consumes: Task 2 (`google_sheets_client`), Task 3
  (`db.get_google_sheets_settings`/`save_google_sheets_settings`),
  Task 4 (`backup.build_backup_zip`).
- Produces (exakt wie Spec-Abschnitt "API-Endpoints"): `GET
  /api/sheets/status`, `GET /api/sheets/oauth/start`, `GET
  /api/sheets/oauth/callback`, `POST /api/sheets/settings`, `POST
  /api/sheets/sync`, `GET /api/backup`.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

Testfälle für `tests/test_webapp_poc_sheets_endpoints.py` (gleicher
Aufbau wie `tests/test_webapp_poc_ebay_endpoints.py`):

- `GET /api/sheets/status`: `connected=false` ohne gespeicherte
  Settings; `connected=true` inkl. `spreadsheet_id`/`connected_at`/
  `last_synced_at`, sobald ein `refresh_token` gesetzt ist.
- `GET /api/sheets/oauth/start`: liefert einen 3xx-Redirect zu einer
  URL, die mit `google_sheets_client.AUTH_BASE` beginnt (State-Param
  wird intern erzeugt, nicht extern prüfbar außer als nicht-leer).
- `GET /api/sheets/oauth/callback`: mit `code` → tauscht Code,
  speichert `refresh_token`/`connected_at`, Redirect zu
  `/settings.html`; mit `error`-Query-Param → Redirect zu
  `/settings.html?sheets_error=...`, kein `exchange_code`-Aufruf.
- `POST /api/sheets/settings`: speichert `spreadsheet_id`; 400 bei
  leerem Body-Feld.
- `POST /api/sheets/sync`: 401 ohne `refresh_token`; 400 ohne
  `spreadsheet_id`; Erfolg ruft `google_sheets_client.sync_to_sheets`
  mit vier Tabs (`Karten`, `Käufe`, `eBay`, `Sync_Info`) auf und
  aktualisiert `last_synced_at`; `GoogleApiError` → 502.
- `GET /api/backup`: `Content-Type: application/zip`,
  `Content-Disposition` enthält `attachment` und `.zip`.

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_sheets_endpoints -v`
  → FAIL (Routen fehlen).

- [ ] **Step 3: Endpoints ergänzen**

Neue Imports:

```python
import secrets
import time

import backup
import google_sheets_client
from fastapi.responses import RedirectResponse
```

Modul-weiter State-Speicher (analog `ebay-oauth-server/app.py`s
`_states`):

```python
_sheets_oauth_states = {}
_SHEETS_STATE_TTL_SECONDS = 600


def _new_sheets_oauth_state():
    now = time.time()
    for key, created in list(_sheets_oauth_states.items()):
        if created < now - _SHEETS_STATE_TTL_SECONDS:
            _sheets_oauth_states.pop(key, None)
    state = secrets.token_urlsafe(32)
    _sheets_oauth_states[state] = now
    return state


def _consume_sheets_oauth_state(state):
    created = _sheets_oauth_states.pop(state, None)
    return created is not None and created >= time.time() - _SHEETS_STATE_TTL_SECONDS
```

Routen:

```python
@app.get("/api/sheets/status")
async def sheets_status():
    settings = db.get_google_sheets_settings() or {}
    return JSONResponse({
        "connected": bool(settings.get("refresh_token")),
        "spreadsheet_id": settings.get("spreadsheet_id", ""),
        "connected_at": settings.get("connected_at"),
        "last_synced_at": settings.get("last_synced_at"),
    })


@app.get("/api/sheets/oauth/start")
async def sheets_oauth_start():
    state = _new_sheets_oauth_state()
    return RedirectResponse(google_sheets_client.authorization_url(state))


@app.get("/api/sheets/oauth/callback")
async def sheets_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return RedirectResponse(f"/settings.html?sheets_error={error}")
    if not code or not state or not _consume_sheets_oauth_state(state):
        return RedirectResponse("/settings.html?sheets_error=ungueltiger_oauth_state")
    try:
        token = google_sheets_client.exchange_code(code)
    except google_sheets_client.GoogleApiError as exc:
        return RedirectResponse(f"/settings.html?sheets_error={exc}")
    db.save_google_sheets_settings({
        "refresh_token": token.get("refresh_token", ""),
        "connected_at": datetime.now(timezone.utc).isoformat(),
    })
    return RedirectResponse("/settings.html")


@app.post("/api/sheets/settings")
async def update_sheets_settings(body: dict = Body(...)):
    spreadsheet_id = (body.get("spreadsheet_id") or "").strip()
    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="spreadsheet_id darf nicht leer sein.")
    updated = db.save_google_sheets_settings({"spreadsheet_id": spreadsheet_id})
    return JSONResponse(updated)


def _sheets_tabs():
    cards = db.all_cards()
    purchases = db.all_purchases()
    items_by_purchase = {}
    for item in db.all_purchase_items():
        items_by_purchase[item["purchase_id"]] = items_by_purchase.get(item["purchase_id"], 0) + 1
    listings = db.all_ebay_listings()
    sales_by_listing = {s["listing_id"]: s for s in db.all_ebay_sales() if s.get("listing_id")}

    card_headers = ["id", "title", "category", "team", "manufacturer", "set_name", "season_year", "card_number", "recognition_status", "created_at"]
    card_rows = [[str(c.get(h, "")) for h in card_headers] for c in cards]

    purchase_headers = ["id", "purchase_date", "platform", "seller", "total_price", "notes", "Anzahl Karten"]
    purchase_rows = [
        [str(p.get(h, "")) for h in purchase_headers[:-1]] + [str(items_by_purchase.get(p["id"], 0))]
        for p in purchases
    ]

    ebay_headers = ["id", "title", "price", "status", "scheduled_at", "sale_date", "gross_price"]
    ebay_rows = []
    for listing in listings:
        sale = sales_by_listing.get(listing["id"], {})
        ebay_rows.append([
            str(listing.get("id", "")), str(listing.get("title", "")), str(listing.get("price", "")),
            str(listing.get("status", "")), str(listing.get("scheduled_at") or ""),
            str(sale.get("sale_date") or ""), str(sale.get("gross_price") or ""),
        ])

    sync_info = (["Information", "Wert"], [
        ["Quelle", "DCardsLab Supabase"],
        ["Synchronisiert", datetime.now(timezone.utc).isoformat(timespec="seconds")],
        ["Richtung", "Supabase -> Google Sheets"],
        ["Hinweis", "Supabase ist die Master-Datenbank; Sheets ist die externe Auswertungsansicht."],
    ])

    return {
        "Karten": (card_headers, card_rows), "Käufe": (purchase_headers, purchase_rows),
        "eBay": (ebay_headers, ebay_rows), "Sync_Info": sync_info,
    }


@app.post("/api/sheets/sync")
async def sync_to_sheets():
    settings = db.get_google_sheets_settings() or {}
    if not settings.get("refresh_token"):
        raise HTTPException(status_code=401, detail="Google Sheets ist nicht verbunden — bitte zuerst auf der Einstellungen-Seite verbinden.")
    if not settings.get("spreadsheet_id"):
        raise HTTPException(status_code=400, detail="Bitte zuerst eine Google-Sheets-Tabellen-ID hinterlegen.")

    try:
        access_token = google_sheets_client.refresh_access_token(settings["refresh_token"])
        google_sheets_client.sync_to_sheets(access_token, settings["spreadsheet_id"], _sheets_tabs())
    except google_sheets_client.GoogleNotConnectedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except google_sheets_client.GoogleApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    synced_at = datetime.now(timezone.utc).isoformat()
    db.save_google_sheets_settings({"last_synced_at": synced_at})
    return JSONResponse({"synced_at": synced_at})


@app.get("/api/backup")
async def download_backup():
    data = backup.build_backup_zip()
    filename = f"dcardslab-backup-{datetime.now(timezone.utc).date().isoformat()}.zip"
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
```

(`secrets`/`time` sind Python-Standardbibliothek, keine neue
Dependency. `_sheets_oauth_states` ist bewusst ein simples
Modul-Dict wie in `ebay-oauth-server/app.py` — ein einzelner
Webapp-Prozess, kein Multi-Worker-Deployment vorgesehen.)

- [ ] **Step 4:** `python3 -m unittest discover -s tests -p 'test_*.py' -v`
  → PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/main.py tests/test_webapp_poc_sheets_endpoints.py
git commit -m "Add /api/sheets/* and /api/backup endpoints"
```

---

### Task 6: `static/settings.html` (neu) + Link in `cards.html` + Doku

**Files:**
- Create: `webapp-poc/static/settings.html`
- Modify: `webapp-poc/static/cards.html`
- Modify: `webapp-poc/README.md`

**Interfaces:**
- Consumes: `GET /api/sheets/status`, `GET /api/sheets/oauth/start`
  (als Link, nicht `fetch()`), `POST /api/sheets/settings`, `POST
  /api/sheets/sync`, `GET /api/backup` (als Link).

Kein Python-Code, daher kein TDD-Zyklus für diesen Task —
Verifikation ist manuelles Testen im Browser (Playwright-gestützt für
Statustext/Buttons, wie bei Sub-Projekt 5; der eigentliche
Google-Consent-Screen nur mit einem echten Google-Konto manuell).

- [ ] **Step 1: `cards.html` – Link ergänzen**

```html
<p><a href="settings.html">Einstellungen &rarr;</a></p>
```

- [ ] **Step 2: `settings.html` erstellen**

Struktur (`#status`-Muster wie alle anderen Seiten):

- **Google Sheets**-Bereich: `GET /api/sheets/status` beim Laden.
  `connected=false` → Hinweistext + `<a href="/api/sheets/oauth/start">Mit
  Google verbinden</a>` (normaler Link, kein `fetch()` — der Browser
  muss den vollen Redirect-Flow zu Google durchlaufen). `connected=true`
  → "Verbunden seit \<`connected_at`\>", Eingabefeld für
  `spreadsheet_id` (vorbelegt), Button "Speichern" (`POST
  /api/sheets/settings`), Button "Jetzt synchronisieren" (`POST
  /api/sheets/sync`, zeigt `last_synced_at` nach Erfolg). Ein
  `sheets_error`-Query-Param (`URLSearchParams(location.search)`) wird
  beim Laden als Fehlermeldung angezeigt.
- **Backup**-Bereich: `<a href="/api/backup">Backup herunterladen</a>`
  (normaler Link — Browser lädt die ZIP direkt herunter, kein
  `fetch()`/Blob-Handling nötig).

- [ ] **Step 3: Manuelle Verifikation im Browser**

Mit Playwright: `settings.html` lädt, zeigt "Nicht verbunden" ohne
gesetzte Settings (API gemockt/leer), Backup-Link vorhanden und zeigt
auf `/api/backup`. Der volle OAuth-Consent-Flow und ein echter
Sheets-Sync brauchen einen echten Google-Account + eingerichteten
OAuth-Client (Nutzer-Setup laut README) — das wird wie beim
eBay-Sandbox-Test erst auf dem NAS-Deployment live verifiziert.

- [ ] **Step 4: `webapp-poc/README.md` aktualisieren**

Neuer Abschnitt "Google-Sheets-Sync & Backup einrichten" (analog zu
`ebay-oauth-server/README.md`s eBay-Setup): Google-Cloud-Console-
Schritte für den **Web-Application**-OAuth-Client (s. Spec, Abschnitt
"Google-OAuth-Flow"), `GOOGLE_CLIENT_ID`/`GOOGLE_CLIENT_SECRET`/
`GOOGLE_REDIRECT_URI` als neue Env-Vars in der Docker-Run/-Compose-
Doku ergänzen. Den Punkt "Google Drive/Sheets-Sync, Backups" aus "Was
absichtlich fehlt" entfernen (jetzt umgesetzt); neuen Punkt unter "Was
hier passiert" ergänzen.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/static/settings.html webapp-poc/static/cards.html webapp-poc/README.md
git commit -m "Add settings.html: Google Sheets connect/sync and backup download"
```
