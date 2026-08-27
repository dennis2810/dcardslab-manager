# WebApp eBay-Integration Implementation Plan

**Goal:** Aus `card.html` heraus eBay-Angebote als Entwurf anlegen (mit
automatisch abgeleiteten Sport/Non-Sport-Pflichtfeldern), einzeln oder per
Mehrfachauswahl veröffentlichen oder für einen späteren Zeitpunkt planen,
Verkäufe automatisch synchronisieren.

**Architecture:** `ebay-oauth-server` bleibt Token-Proxy + Setup-Helfer,
bekommt nur einen neuen internen Token-Endpoint. Die gesamte
eBay-Business-Logik lebt neu in `webapp-poc` (`ebay_client.py`,
`ebay_listing.py`, `ebay_scheduler.py`), zwei neue Supabase-Tabellen
(`ebay_listings`, `ebay_sales`), neue `/api/ebay/*`-Endpoints in `main.py`,
eine neue Übersichtsseite (`ebay.html`) und ein erweiterter Bereich in
`card.html`. Kein neuer Service, kein Build-Schritt — gleiches Muster wie
Sub-Projekte 1–3.

**Tech Stack:** FastAPI, Supabase Postgres (bestehend), `httpx` (bereits
Dependency) für eBay-API-Aufrufe, Vanilla JS/HTML.

**Spec:** `docs/superpowers/specs/2026-08-27-webapp-ebay-integration-design.md`
(Status: Freigegeben)

## Hinweis zum Detailgrad dieses Plans

Die Pläne der Sub-Projekte 1–3 haben pro Task den vollständigen Ziel-Code
(inkl. jedes einzelnen Tests) direkt in den Plan geschrieben. Sub-Projekt 4
ist deutlich größer (fünf neue Backend-Module statt zwei, zwei neue
UI-Seiten, ein zusätzlicher externer Service). Damit dieser Plan selbst
handhabbar bleibt, enthält er für die Backend-Module vollständige
Funktionssignaturen, Verhalten und konkrete Testfälle als Liste (statt
jeden Testcase komplett auszuschreiben) — die eigentlichen Testdateien
entstehen 1:1 danach während der TDD-Umsetzung. Für die beiden HTML-Seiten
gilt dasselbe Detailniveau wie die Spec's UI-Design-Abschnitt (Struktur +
Verhalten je Bereich), keine vollständige Markup-Vorabschrift — der
JS-Programmierstil folgt 1:1 `cards.html`/`card.html`/`purchases.html`
(`#status`-Muster, Debounce-Suche, `try/catch` um `sessionStorage`).

## Global Constraints

- Tabellen-/Spaltennamen exakt wie in der Spec (`ebay_listings`,
  `ebay_sales`, siehe Spec-Abschnitt "Datenmodell").
- **Natives Scheduling ist beim Merge dieses Plans NICHT verifiziert.**
  Dieser Coding-Session fehlen echte eBay-Sandbox-Credentials (die liegen
  nur als Env-Vars auf dem NAS-Container). Deshalb: `ebay_client.py` bekommt
  eine Konstante `NATIVE_SCHEDULING_SUPPORTED = False` — solange sie auf
  `False` steht, läuft **jede** Planung über den bereits vollständig
  getesteten App-seitigen Scheduler (Fallback aus der Spec), nie über einen
  ungeprüften eBay-Parameter. Der native Pfad wird in Task 5 als Code
  mitgebaut (klar mit `if NATIVE_SCHEDULING_SUPPORTED:` markiert) und mit
  einem Kommentar dokumentiert, was der Sandbox-Spike (Verifikation live
  auf dem NAS, s. Spec-Abschnitt "Sandbox-Spike") prüfen muss, bevor jemand
  die Konstante auf `True` dreht. **Kein Publish-Aufruf geht ohne diese
  Verifikation live früher als beabsichtigt** — das ist eine bewusste
  Sicherheitsentscheidung, kein technisches Detail: ein falsch-positiv
  angenommenes natives Scheduling würde ein Angebot sofort statt geplant
  live schalten.
- Alle neuen Supabase-/eBay-HTTP-Aufrufe werden in Tests gemockt (kein
  echter Netzwerk-Call in CI) — gleiches Muster wie der gesamte Rest des
  Projekts (`unittest.mock.patch`, `MagicMock`).
- Deutsche Statustexte/Fehlermeldungen im bestehenden Stil.
- `EBAY_ENVIRONMENT` muss in `webapp-poc` und `ebay-oauth-server` denselben
  Wert haben (beide `sandbox`, bis die Produktions-Freigabe explizit
  erfolgt) — sonst passt der vom oauth-server gelieferte Token nicht zur
  API-Basis-URL, die `ebay_client.py` anspricht.
- Kein neues JS-Framework, kein Build-Schritt.

---

### Task 1: Supabase-Schema erweitern (`ebay_listings`, `ebay_sales`)

**Files:**
- Modify: `supabase/schema.sql`
- Modify: `supabase/README.md`

**Interfaces:**
- Produces: Tabellen `ebay_listings`, `ebay_sales` im bestehenden
  Supabase-Projekt (exaktes DDL: Spec-Abschnitt "Datenmodell"). Task 3
  setzt exakt diese Namen/Spalten voraus.

Kein Code, daher kein TDD-Zyklus – Verifikation ist manuelles erneutes
Einspielen durch den Nutzer.

- [ ] **Step 1:** DDL aus der Spec (Abschnitt "Datenmodell", beide
  `create table`-Blöcke + Indizes) unverändert ans Ende von
  `supabase/schema.sql` anhängen.
- [ ] **Step 2:** Nutzer bittet, den kompletten `schema.sql`-Inhalt erneut
  im Supabase SQL Editor auszuführen (`create table if not exists` ist
  idempotent); prüfen, dass `ebay_listings`/`ebay_sales` im Table Editor
  erscheinen, inkl. `unique(card_id)` auf `ebay_listings` und
  `unique(ebay_order_id, ebay_line_item_id)` auf `ebay_sales`.
- [ ] **Step 3:** `card-images`-Bucket auf public read stellen (Spec-
  Abschnitt "Bild-URLs für eBay") — Supabase Dashboard, Storage →
  `card-images` → Bucket public machen. Manuell durch den Nutzer, da kein
  API-Zugriff auf Supabase-Projekteinstellungen aus diesem Repo heraus
  existiert (gleiche Grenze wie beim SQL Editor).
- [ ] **Step 4:** `supabase/README.md` um einen kurzen Hinweis auf die
  beiden neuen Tabellen + den public-read Bucket-Schritt ergänzen (gleiche
  Stelle/Formulierung wie der bestehende Hinweis zu erneutem
  Schema-Einspielen).
- [ ] **Step 5: Commit**

```bash
git add supabase/schema.sql supabase/README.md
git commit -m "Add ebay_listings/ebay_sales tables to Supabase schema"
```

---

### Task 2: `ebay-oauth-server` – interner Token-Endpoint

**Files:**
- Modify: `ebay-oauth-server/app.py`
- Modify: `tests/test_ebay_oauth_server.py`
- Modify: `ebay-oauth-server/README.md`

**Interfaces:**
- Produces: `GET /api/internal/access-token` →
  `{"access_token": str, "environment": str, "expires_in": int}` (200) oder
  `{"authorized": false}` (401, falls kein Token gespeichert oder Refresh
  fehlschlägt). Task 5 (`ebay_client.get_access_token()`) konsumiert exakt
  diese Response-Form.

- [ ] **Step 1: Fehlschlagenden Test schreiben**

An `tests/test_ebay_oauth_server.py` anhängen (gleicher Flask-Stub wie
bereits im Datei-Header installiert; `app.load_token`/
`app.refresh_access_token` werden gepatcht statt echter HTTP-Aufrufe):

```python
from unittest.mock import patch


class InternalAccessTokenTests(unittest.TestCase):
    def test_returns_token_when_authorized(self):
        with patch("app.refresh_access_token", return_value={
            "access_token": "tok-123", "expires_in": 7200,
        }):
            result = oauth_server.internal_access_token()
        self.assertEqual(result["access_token"], "tok-123")
        self.assertEqual(result["environment"], oauth_server.ENVIRONMENT)

    def test_returns_401_shape_when_not_authorized(self):
        with patch("app.refresh_access_token", side_effect=RuntimeError("Kein Refresh Token gespeichert.")):
            result, status = oauth_server.internal_access_token()
        self.assertEqual(status, 401)
        self.assertFalse(result["authorized"])
```

(Der bestehende Flask-Stub im Datei-Header liefert `jsonify(**kw) -> kw`
und `app.get(...)`/`app.post(...)` als No-Op-Decorator — die Route-
Funktion bleibt also unter ihrem Funktionsnamen direkt aufrufbar, exakt
wie bei den bestehenden Tests in dieser Datei.)

- [ ] **Step 2:** `python3 -m unittest tests.test_ebay_oauth_server -v` →
  FAIL (`AttributeError: module 'app' has no attribute
  'internal_access_token'`).

- [ ] **Step 3: Endpoint ergänzen** — direkt vor `@app.get("/")` (nach
  `revoke_local()`) in `ebay-oauth-server/app.py`:

```python
@app.get("/api/internal/access-token")
def internal_access_token():
    try:
        token = refresh_access_token()
    except RuntimeError:
        return jsonify({"authorized": False}), 401
    return jsonify({
        "access_token": token["access_token"],
        "environment": ENVIRONMENT,
        "expires_in": token.get("expires_in"),
    })
```

- [ ] **Step 4:** `python3 -m unittest tests.test_ebay_oauth_server -v` →
  PASS.

- [ ] **Step 5:** `ebay-oauth-server/README.md` um den neuen Endpoint
  ergänzen (kurzer Abschnitt, analog zu `/api/oauth/status`): kein
  Auth-Header, nur im Tailscale-internen NAS-Netz erreichbar, genutzt von
  `webapp-poc`.

- [ ] **Step 6: Commit**

```bash
git add ebay-oauth-server/app.py ebay-oauth-server/README.md tests/test_ebay_oauth_server.py
git commit -m "Add internal access-token endpoint to ebay-oauth-server"
```

---

### Task 3: `webapp-poc/ebay_listing.py` – reine Listing-Logik

**Files:**
- Create: `webapp-poc/ebay_listing.py`
- Create: `tests/test_webapp_poc_ebay_listing.py`

**Interfaces:**
- Consumes: `templates/ebay/eBay-category-listing-template_261328.csv`/
  `..._non_sport.csv` (bestehend, read-only).
- Produces (alle reine Funktionen, kein HTTP/DB):
  - `sku_for_card(card_id: str) -> str` — `f"webapp-{card_id}"`.
  - `generate_title(card: dict, max_len=80) -> str`
  - `generate_description(card: dict) -> str`
  - `derive_listing_type(card: dict) -> "sport" | "non_sport"`
  - `build_aspects(card: dict, listing_type: str) -> dict[str, list[str]]`
  - `required_aspects(listing_type: str) -> list[str]`
  - `missing_aspects(aspects: dict, listing_type: str) -> list[str]`
  - `price_research_links(title: str) -> {"ebay_sold": str, "onepoint": str}`
  - `match_sale_line_item(line_item: dict, listings_by_sku: dict) -> dict | None`
  - `CATEGORY_IDS = {"sport": "261328", "non_sport": "183050"}`

Task 6/7 (`db.py`/`main.py`) rufen alle mit exakt dieser Signatur auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

`tests/test_webapp_poc_ebay_listing.py` (neu):

Testfälle, die die Datei abdecken muss (jede als eigene `unittest`-Methode,
gleicher Stil wie `tests/test_webapp_poc_db.py`):

- `generate_title`: baut einen Titel aus `season_year`/`manufacturer`/
  `set_name`/`title`/`team`/`card_number` zusammen, überspringt leere
  Felder, kürzt auf `max_len`.
- `generate_description`: enthält mindestens `title`, `condition`-Hinweis
  bleibt Platzhaltertext (kein `condition`-Feld auf `cards`, s. Spec).
- `derive_listing_type`: `category="Fußball"` → `"sport"`;
  `category="Pokémon"` (unbekannt) → `"non_sport"`; `category=""` (leer) →
  `"non_sport"`.
- `build_aspects`: `listing_type="sport"` setzt `aspects["Sportart"]`,
  `listing_type="non_sport"` setzt es **nicht**; leere Kartenfelder tauchen
  nicht im Ergebnis auf.
- `required_aspects("sport")`: liest die echte
  `eBay-category-listing-template_261328.csv` und findet `"Sportart"`
  darin (Regressionstest gegen die reale, im Repo liegende Datei — kein
  Mock nötig).
- `missing_aspects`: `aspects={}` bei `listing_type="sport"` liefert
  `["Sportart"]`; vollständige `aspects` liefert `[]`.
- `price_research_links`: URL-Encoding des Titels (Leerzeichen/Sonderzeichen),
  beide Keys vorhanden.
- `match_sale_line_item`: Treffer per `sku`, `None` bei unbekanntem `sku`.

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_ebay_listing -v`
  → FAIL (`ModuleNotFoundError: No module named 'ebay_listing'`).

- [ ] **Step 3: `webapp-poc/ebay_listing.py` implementieren**

Kernpunkte für die Implementierung (siehe Spec-Abschnitt
"Kartentyp-Ableitung" für die Feldzuordnung, portiert aus
`ebay_sandbox_create_offer()`/`ebay_generate_title()`/
`ebay_generate_description()` in `app/dcardlabs_manager.py`):

```python
from pathlib import Path
from urllib.parse import quote_plus
import csv

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "ebay"
CATEGORY_IDS = {"sport": "261328", "non_sport": "183050"}
TEMPLATE_FILES = {
    "sport": TEMPLATES_DIR / "eBay-category-listing-template_261328.csv",
    "non_sport": TEMPLATES_DIR / "eBay-category-listing-template_non_sport.csv",
}
# Nur eine Default-Vermutung fuer die automatische Sport/Non-Sport-Ableitung
# - im Formular immer ueberschreibbar (s. Spec, Abschnitt Kartentyp).
_KNOWN_SPORTS = {
    "Fußball", "Basketball", "Baseball", "Eishockey", "American Football",
    "Tennis", "Boxen", "Golf", "Motorsport", "Formel 1", "Wrestling",
    "Rugby", "Cricket",
}


def sku_for_card(card_id):
    return f"webapp-{card_id}"


def generate_title(card, max_len=80):
    number = card.get("card_number")
    parts = [
        card.get("season_year", ""), card.get("manufacturer", ""),
        card.get("set_name", ""), card.get("title", ""), card.get("team", ""),
        f"#{number}" if number else "", card.get("variant", ""),
    ]
    return " ".join(p for p in parts if p).strip()[:max_len]


def generate_description(card):
    lines = [generate_title(card, max_len=200)]
    for label, key in (("Set", "set_name"), ("Saison", "season_year"),
                        ("Team", "team"), ("Kartennummer", "card_number")):
        value = card.get(key)
        if value:
            lines.append(f"{label}: {value}")
    lines.append("Zustand: siehe Angebot. Versand aus Deutschland.")
    return "\n".join(lines)


def derive_listing_type(card):
    return "sport" if str(card.get("category") or "").strip() in _KNOWN_SPORTS else "non_sport"


def build_aspects(card, listing_type):
    aspects = {}
    if listing_type == "sport" and card.get("category"):
        aspects["Sportart"] = [card["category"]]
    for label, key in (
        ("Team / Verein", "team"), ("Hersteller", "manufacturer"),
        ("Set / Serie", "set_name"), ("Saison / Jahr", "season_year"),
        ("Kartennummer", "card_number"),
    ):
        value = str(card.get(key) or "").strip()
        if value:
            aspects[label] = [value]
    return aspects


def required_aspects(listing_type):
    path = TEMPLATE_FILES[listing_type]
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    if len(rows) < 2:
        return []
    labels = []
    for h in rows[1]:
        h = str(h or "").strip()
        if h.startswith("*C:"):
            labels.append(h[len("*C:"):].split(" - (ID:")[0].strip())
    return labels


def missing_aspects(aspects, listing_type):
    return [label for label in required_aspects(listing_type) if not aspects.get(label)]


def price_research_links(title):
    q = quote_plus(title)
    return {
        "ebay_sold": f"https://www.ebay.de/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1",
        "onepoint": f"https://130point.com/sales/?search={q}",
    }


def match_sale_line_item(line_item, listings_by_sku):
    return listings_by_sku.get(line_item.get("sku"))
```

(`required_aspects()`/`missing_aspects()` lesen die CSV bei jedem Aufruf
neu — kein Caching, da diese Funktion nur beim Entwurf-Erstellen und beim
Publish aufgerufen wird, keine Hot-Path-Sorge.)

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_ebay_listing -v`
  → PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/ebay_listing.py tests/test_webapp_poc_ebay_listing.py
git commit -m "Add ebay_listing.py: title/description generation, listing-type derivation, price-research links"
```

---

### Task 4: `webapp-poc/storage.py` – öffentliche Bild-URL

**Files:**
- Modify: `webapp-poc/storage.py`
- Modify: `tests/test_webapp_poc_storage.py`

**Interfaces:**
- Produces: `public_url(object_path: str) -> str`. Task 5
  (`ebay_client`-Aufrufer in `main.py`) nutzt das für die
  Inventory-Item-Bild-URL.

- [ ] **Step 1: Fehlschlagenden Test schreiben** — an
  `tests/test_webapp_poc_storage.py` anhängen:

```python
class PublicUrlTests(unittest.TestCase):
    def test_builds_public_storage_url(self):
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.get_public_url.return_value = (
            "https://project.supabase.co/storage/v1/object/public/card-images/b1/1_front.jpg"
        )
        with patch("storage.get_client", return_value=mock_client):
            result = storage.public_url("b1/1_front.jpg")
        self.assertTrue(result.startswith("https://"))
        mock_client.storage.from_.return_value.get_public_url.assert_called_once_with("b1/1_front.jpg")
```

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_storage -v` →
  FAIL (`AttributeError: module 'storage' has no attribute 'public_url'`).

- [ ] **Step 3:** in `webapp-poc/storage.py`, direkt nach `signed_url()`
  ergänzen:

```python
def public_url(object_path):
    """Baut die dauerhafte, unsignierte Storage-URL - nur sinnvoll,
    solange BUCKET public-read ist (s. Supabase-Setup). Nur fuer die
    eBay-Bilduebergabe genutzt; cards.html/card.html bleiben bei
    signed_url()."""
    return get_client().storage.from_(BUCKET).get_public_url(object_path)
```

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_storage -v` →
  PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/storage.py tests/test_webapp_poc_storage.py
git commit -m "Add storage.public_url() for permanent card-image URLs (eBay)"
```

---

### Task 5: `webapp-poc/ebay_client.py` – eBay-Sell-API-Client

**Files:**
- Create: `webapp-poc/ebay_client.py`
- Create: `tests/test_webapp_poc_ebay_client.py`

**Interfaces:**
- Consumes: `GET {EBAY_OAUTH_SERVER_URL}/api/internal/access-token` (Task 2),
  eBay Sell API (`httpx`, gemockt in Tests über `httpx.MockTransport` bzw.
  `unittest.mock.patch("ebay_client.httpx.Client")`).
- Produces:
  - `EbayNotAuthorizedError`, `EbayApiError` (Exceptions)
  - `NATIVE_SCHEDULING_SUPPORTED = False` (Konstante, s. Global Constraints)
  - `get_access_token() -> str`
  - `condition_id_to_enum(condition: str) -> str`
  - `get_listing_policies(token: str, marketplace_id="EBAY_DE") -> dict` (wirft
    `EbayApiError` mit deutscher Meldung bei fehlender Policy)
  - `put_inventory_item(token, sku, card, image_url)`
  - `create_offer(token, sku, listing) -> offer_id`
  - `update_offer(token, offer_id, listing)`
  - `publish_offer(token, offer_id, scheduled_at=None) -> ebay_listing_id`
  - `get_offer(token, offer_id) -> dict`
  - `withdraw_offer(token, offer_id)`
  - `get_orders(token, created_since_iso: str) -> list[dict]`

Task 8 (`main.py`-Endpoints) und Task 9 (`ebay_scheduler.py`) rufen alle
mit exakt dieser Signatur auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

Testfälle für `tests/test_webapp_poc_ebay_client.py` (gemockter
`httpx`-Transport bzw. gepatchte `httpx.get`/`httpx.Client`-Aufrufe, gleiche
Mock-Tiefe wie bei den Supabase-Mocks im restlichen Projekt):

- `get_access_token`: gibt `access_token` aus einer 200-Antwort zurück;
  wirft `EbayNotAuthorizedError` bei 401.
- `condition_id_to_enum`: bekannte ID → Enum-String; unbekannte ID/Passthrough.
- `get_listing_policies`: alle drei Policy-IDs gefunden → dict mit allen
  drei Keys; eine fehlt → `EbayApiError` mit deutscher Meldung, die den
  fehlenden Policy-Typ nennt.
- `put_inventory_item`/`create_offer`/`update_offer`: prüfen den
  gesendeten JSON-Body (SKU, Preis, Menge, Bild-URL, Aspects,
  `conditionId`) und den aufgerufenen Pfad/HTTP-Methode.
- `publish_offer` ohne `scheduled_at`: ruft `POST
  /offer/{id}/publish` ohne Scheduling-Feld auf, gibt `listingId` zurück.
- `publish_offer` mit `scheduled_at` und `NATIVE_SCHEDULING_SUPPORTED=True`
  (Test patcht die Modul-Konstante hoch): Request-Body enthält das
  Scheduling-Feld.
- `publish_offer` mit `scheduled_at` und `NATIVE_SCHEDULING_SUPPORTED=False`
  (Default): wirft `AssertionError`/eigene Exception statt still das
  Scheduling-Feld wegzulassen — ein Aufrufer, der native Planung anfordert,
  obwohl sie nicht verifiziert ist, ist ein Bug im aufrufenden Code (Task 8
  ruft das nie mit `scheduled_at` auf, solange die Konstante `False` ist,
  s. Task 8).
- `get_offer`/`withdraw_offer`/`get_orders`: Erfolgspfad + `EbayApiError`
  bei Nicht-2xx.
- Alle Fehlerpfade: eBays Rohtext landet unverändert in
  `EbayApiError.args[0]`.

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_ebay_client -v`
  → FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: `webapp-poc/ebay_client.py` implementieren**

Portiert `_CONDITION_ID_TO_ENUM`, `get_listing_policies()`-Logik 1:1 aus
`ebay-oauth-server/app.py` (Zeilen ~180–238), auf `httpx` statt `urllib`
umgestellt. Gerüst:

```python
import os
import httpx

EBAY_OAUTH_SERVER_URL = os.environ.get("EBAY_OAUTH_SERVER_URL", "http://ebay-oauth-server:8080").rstrip("/")
EBAY_ENVIRONMENT = os.environ.get("EBAY_ENVIRONMENT", "sandbox").strip().lower()
EBAY_API_BASE = "https://api.sandbox.ebay.com" if EBAY_ENVIRONMENT == "sandbox" else "https://api.ebay.com"
MARKETPLACE_ID = "EBAY_DE"

# s. Global Constraints: erst nach echtem Sandbox-Spike auf True setzen.
NATIVE_SCHEDULING_SUPPORTED = False


class EbayNotAuthorizedError(Exception):
    pass


class EbayApiError(Exception):
    pass


def get_access_token():
    try:
        response = httpx.get(f"{EBAY_OAUTH_SERVER_URL}/api/internal/access-token", timeout=30)
    except httpx.HTTPError as exc:
        raise EbayApiError(f"eBay-OAuth-Server nicht erreichbar: {exc}") from exc
    if response.status_code == 401:
        raise EbayNotAuthorizedError("eBay ist nicht verbunden — bitte zuerst den OAuth-Flow abschließen.")
    response.raise_for_status()
    return response.json()["access_token"]


def _headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
        "Accept-Language": "de-DE",
        "Content-Language": "de-DE",
    }


def _request(method, token, path, json_body=None):
    try:
        response = httpx.request(method, EBAY_API_BASE + path, headers=_headers(token), json=json_body, timeout=45)
    except httpx.HTTPError as exc:
        raise EbayApiError(f"eBay nicht erreichbar: {exc}") from exc
    if response.status_code >= 400:
        raise EbayApiError(response.text)
    return response


# ... condition_id_to_enum (Mapping 1:1 aus ebay-oauth-server/app.py) ...
# ... get_listing_policies (Logik 1:1 portiert, auf _request umgestellt) ...


def put_inventory_item(token, sku, card, image_url):
    payload = {
        "product": {"title": card.get("title", ""), "imageUrls": [image_url] if image_url else []},
        "availability": {"shipToLocationAvailability": {"quantity": 1}},
    }
    _request("PUT", token, f"/sell/inventory/v1/inventory_item/{sku}", payload)


def create_offer(token, sku, listing):
    payload = _offer_payload(sku, listing)
    response = _request("POST", token, "/sell/inventory/v1/offer", payload)
    return response.json()["offerId"]


def update_offer(token, offer_id, listing):
    payload = _offer_payload(listing["sku"], listing)
    _request("PUT", token, f"/sell/inventory/v1/offer/{offer_id}", payload)


def _offer_payload(sku, listing):
    return {
        "sku": sku,
        "marketplaceId": MARKETPLACE_ID,
        "format": "FIXED_PRICE",
        "availableQuantity": listing.get("quantity", 1),
        "categoryId": listing["category_id"],
        "listingDescription": listing.get("description", ""),
        "pricingSummary": {"price": {"value": str(listing.get("price", 0)), "currency": "EUR"}},
        "listingPolicies": listing.get("policies", {}),
    }


def publish_offer(token, offer_id, scheduled_at=None):
    payload = {}
    if scheduled_at is not None:
        if not NATIVE_SCHEDULING_SUPPORTED:
            raise EbayApiError("Natives eBay-Scheduling ist nicht verifiziert - Aufrufer haette scheduling_mode='app' waehlen muessen.")
        payload["listingStartDate"] = scheduled_at  # Platzhalter-Feldname, s. Sandbox-Spike
    response = _request("POST", token, f"/sell/inventory/v1/offer/{offer_id}/publish", payload or None)
    return response.json().get("listingId")


def get_offer(token, offer_id):
    return _request("GET", token, f"/sell/inventory/v1/offer/{offer_id}").json()


def withdraw_offer(token, offer_id):
    _request("POST", token, f"/sell/inventory/v1/offer/{offer_id}/withdraw", {})


def get_orders(token, created_since_iso):
    filter_q = f"creationdate:[{created_since_iso}..]"
    response = _request("GET", token, f"/sell/fulfillment/v1/order?filter={filter_q}&limit=200")
    return response.json().get("orders", [])
```

(`_offer_payload`s `listingStartDate` ist ein **Platzhaltername** — der
Sandbox-Spike muss das echte Feld/Verhalten der Offer-Ressource
verifizieren, bevor `NATIVE_SCHEDULING_SUPPORTED` je auf `True` gesetzt
wird; solange die Konstante `False` ist, wirft `publish_offer` bei
`scheduled_at is not None` sofort einen Fehler statt einen falschen
Payload an eBay zu schicken.)

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_ebay_client -v`
  → PASS.

- [ ] **Step 5:** `webapp-poc/requirements.txt` prüfen — `httpx` ist
  bereits vorhanden, keine Änderung nötig.

- [ ] **Step 6: Commit**

```bash
git add webapp-poc/ebay_client.py tests/test_webapp_poc_ebay_client.py
git commit -m "Add ebay_client.py: eBay Sell API client (inventory item, offer, publish, orders)"
```

---

### Task 6: `webapp-poc/db.py` – Persistenz für `ebay_listings`/`ebay_sales`

**Files:**
- Modify: `webapp-poc/db.py`
- Modify: `tests/test_webapp_poc_db.py`

**Interfaces:**
- Produces:
  - `EBAY_LISTING_FIELDS` (Whitelist, analog `CARD_FIELDS`/
    `PURCHASE_FIELDS`)
  - `create_ebay_listing(card_id, sku, fields) -> dict`
  - `get_ebay_listing(listing_id) -> dict | None`
  - `get_ebay_listing_for_card(card_id) -> dict | None`
  - `list_ebay_listings(status=None, q=None) -> list[dict]`
  - `update_ebay_listing(listing_id, fields) -> dict | None` (Whitelist
    zusätzlich `status`/`scheduled_at`/`scheduling_mode`/`ebay_offer_id`/
    `ebay_listing_id`/`last_error`/`published_at`)
  - `delete_ebay_listing(listing_id) -> dict | None`
  - `list_due_scheduled_listings(scheduling_mode) -> list[dict]`
    (`status='Geplant' AND scheduling_mode=<mode>`, `scheduled_at <=
    now()` **client-seitig gefiltert** — analog zum bestehenden Muster im
    Projekt, das komplexe PostgREST-Filter vermeidet)
  - `list_native_scheduled_listings() -> list[dict]`
    (`status='Geplant' AND scheduling_mode='native'`, für den
    Status-Poll)
  - `latest_sale_sync_cursor() -> str | None`
    (`max(created_at)` aus `ebay_sales`, `None` falls Tabelle leer)
  - `upsert_ebay_sale(fields) -> dict`
    (`upsert` auf `(ebay_order_id, ebay_line_item_id)`)

Task 8/9 rufen alle mit exakt dieser Signatur auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

Gleicher Aufbau wie die bestehenden `PurchaseTests`-Klassen in
`tests/test_webapp_poc_db.py` (inkl. `_mock_client_for_tables()`-Helper,
bereits vorhanden aus Sub-Projekt 3). Testfälle:

- `create_ebay_listing`: Insert-Row enthält `card_id`/`sku`, ignoriert
  unbekannte Felder.
- `get_ebay_listing_for_card`: `None`, falls kein Angebot verknüpft;
  einzelnes Objekt (nicht Liste), falls verknüpft.
- `list_ebay_listings`: `status`-Filter → `.eq("status", ...)`; `q`-Filter
  → `.or_(...)` über `title`.
- `update_ebay_listing`: nur erlaubte Felder landen im Update-Payload,
  `None` bei unbekannter ID.
- `delete_ebay_listing`: löscht und gibt die gelöschte Zeile zurück,
  `None` bei unbekannter ID.
- `list_due_scheduled_listings`: filtert `scheduled_at <= now()`
  client-seitig aus einer größeren zurückgegebenen Menge (Test setzt
  `scheduled_at` einmal in der Vergangenheit, einmal in der Zukunft in den
  Mock-Daten, erwartet nur die fällige Zeile im Ergebnis).
- `upsert_ebay_sale`: ruft `.upsert(row, on_conflict="ebay_order_id,ebay_line_item_id")`
  auf (Assertion auf den `on_conflict`-Parameter).
- `latest_sale_sync_cursor`: `None` bei leerer Tabelle, sonst der
  `created_at`-Wert der ersten (nach `created_at desc` sortierten) Zeile.

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_db -v` → FAIL.

- [ ] **Step 3: Funktionen in `db.py` ergänzen**

```python
EBAY_LISTING_FIELDS = [
    "title", "description", "condition", "condition_id",
    "listing_type", "category_id", "aspects", "price", "quantity",
]
EBAY_LISTING_WRITABLE_STATUS_FIELDS = {
    "status", "scheduled_at", "scheduling_mode",
    "ebay_offer_id", "ebay_listing_id", "last_error", "published_at",
}
EBAY_LISTING_NUMERIC_FIELDS = {"price", "quantity"}


def create_ebay_listing(card_id, sku, fields):
    row = _blank_numeric_to_none(
        {name: fields[name] for name in EBAY_LISTING_FIELDS if name in fields},
        EBAY_LISTING_NUMERIC_FIELDS,
    )
    row.update({"card_id": card_id, "sku": sku})
    response = get_client().table("ebay_listings").insert(row).execute()
    return response.data[0]


def get_ebay_listing(listing_id):
    response = get_client().table("ebay_listings").select("*").eq("id", listing_id).execute()
    return response.data[0] if response.data else None


def get_ebay_listing_for_card(card_id):
    response = get_client().table("ebay_listings").select("*").eq("card_id", card_id).execute()
    return response.data[0] if response.data else None


def list_ebay_listings(status=None, q=None):
    query = get_client().table("ebay_listings").select("*")
    if status:
        query = query.eq("status", status)
    if q:
        query = query.or_(_ilike_search_filter(q, ["title"]))
    response = query.order("updated_at", desc=True).execute()
    return response.data


def update_ebay_listing(listing_id, fields):
    allowed = set(EBAY_LISTING_FIELDS) | EBAY_LISTING_WRITABLE_STATUS_FIELDS
    row = _blank_numeric_to_none(
        {name: value for name, value in fields.items() if name in allowed},
        EBAY_LISTING_NUMERIC_FIELDS,
    )
    if not row:
        return get_ebay_listing(listing_id)
    response = get_client().table("ebay_listings").update(row).eq("id", listing_id).execute()
    return response.data[0] if response.data else None


def delete_ebay_listing(listing_id):
    response = get_client().table("ebay_listings").select("id").eq("id", listing_id).execute()
    if not response.data:
        return None
    get_client().table("ebay_listings").delete().eq("id", listing_id).execute()
    return response.data[0]


def list_due_scheduled_listings(scheduling_mode):
    from datetime import datetime, timezone
    response = (
        get_client().table("ebay_listings").select("*")
        .eq("status", "Geplant").eq("scheduling_mode", scheduling_mode).execute()
    )
    now = datetime.now(timezone.utc)
    return [row for row in response.data if row.get("scheduled_at") and row["scheduled_at"] <= now.isoformat()]


def list_native_scheduled_listings():
    response = (
        get_client().table("ebay_listings").select("*")
        .eq("status", "Geplant").eq("scheduling_mode", "native").execute()
    )
    return response.data


def latest_sale_sync_cursor():
    response = get_client().table("ebay_sales").select("created_at").order("created_at", desc=True).limit(1).execute()
    return response.data[0]["created_at"] if response.data else None


def upsert_ebay_sale(fields):
    response = (
        get_client().table("ebay_sales")
        .upsert(fields, on_conflict="ebay_order_id,ebay_line_item_id")
        .execute()
    )
    return response.data[0]
```

(Wiederverwendet `_blank_numeric_to_none`/`_ilike_search_filter`, beide
bereits in `db.py` aus Sub-Projekt 2/3 vorhanden.)

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_db -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/db.py tests/test_webapp_poc_db.py
git commit -m "Add ebay_listings/ebay_sales persistence functions to db.py"
```

---

### Task 7: `webapp-poc/main.py` – `/api/ebay/*`-Endpoints

**Files:**
- Modify: `webapp-poc/main.py`
- Create: `tests/test_webapp_poc_ebay_endpoints.py`

**Interfaces:**
- Consumes: alles aus Task 3/5/6.
- Produces (exakt wie Spec-Abschnitt "API-Endpoints"):
  `POST /api/cards/{card_id}/ebay-listing`, `GET/PATCH/DELETE
  /api/ebay/listings[/{id}]`, `POST /api/ebay/listings/{id}/publish`,
  `POST /api/ebay/listings/{id}/unschedule`, `POST
  /api/ebay/listings/publish-bulk`, `GET /api/ebay/oauth/status`, `POST
  /api/ebay/sync-sales`.

Ein interner Helper `_publish_listing(listing, scheduled_at=None) -> dict`
bündelt den kompletten Publish-Ablauf (Pflicht-Aspekte validieren, Policies
auflösen, Inventory Item + Offer anlegen/aktualisieren, `publish`
aufrufen, DB-Update) — sowohl der `/publish`-Endpoint als auch
`publish-bulk` und der spätere Scheduler (Task 9) rufen ihn auf, statt die
Logik zu duplizieren.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

Testfälle für `tests/test_webapp_poc_ebay_endpoints.py` (gleicher Aufbau
wie `tests/test_webapp_poc_purchases_endpoints.py`: `TestClient(main.app)`,
`main.db`/`main.ebay_client`/`main.ebay_listing` gepatcht):

- `POST /api/cards/{id}/ebay-listing`: 404 bei unbekannter Karte; 409 bei
  bereits existierendem Angebot; ohne Body generiert es Titel/
  Beschreibung/`listing_type`/`aspects` aus der Karte (Assertion auf die
  an `db.create_ebay_listing` übergebenen Felder); Response enthält
  `required_aspects`.
- `GET /api/ebay/listings`: reicht `status`/`q` an `db.list_ebay_listings`
  durch.
- `GET /api/ebay/listings/{id}`: 404 bei unbekannter ID.
- `PATCH /api/ebay/listings/{id}`: aktualisiert Felder; löst bei
  `status == "Veroeffentlicht"` automatisch `_publish_listing` erneut aus
  (Assertion, dass `ebay_client.create_offer`/`update_offer`/
  `publish_offer` erneut aufgerufen werden); Re-Publish-Fehler setzt
  `status="Fehler"`, gibt aber die aktualisierten Feldwerte zurück (kein
  Rollback).
- `DELETE /api/ebay/listings/{id}`: 409 bei `status != "Entwurf"`; löscht
  sonst.
- `POST /api/ebay/listings/{id}/publish` ohne Body: Erfolgspfad setzt
  `status="Veroeffentlicht"`, `ebay_offer_id`/`ebay_listing_id`/
  `published_at`; `ebay_client.EbayApiError` → 502, `status="Fehler"`,
  `last_error` gesetzt; fehlende Pflicht-Aspekte → 422 mit den fehlenden
  Feldnamen, **kein** eBay-Aufruf (Assertion:
  `ebay_client.put_inventory_item.assert_not_called()`).
- `POST /api/ebay/listings/{id}/publish` mit `scheduled_at` in der
  Zukunft: `scheduling_mode="app"` (weil `NATIVE_SCHEDULING_SUPPORTED` in
  Task 5 `False` ist), `status="Geplant"`, **kein** Aufruf von
  `ebay_client.create_offer`/`put_inventory_item` (App-Modus legt noch
  nichts bei eBay an).
- `POST /api/ebay/listings/{id}/unschedule`: 409 bei `status !=
  "Geplant"`; setzt sonst `status="Entwurf"`, `scheduled_at=None`.
- `POST /api/ebay/listings/publish-bulk`: gemischtes Ergebnis (eine ID
  erfolgreich, eine wirft `EbayApiError`) → `results`-Liste mit beiden
  Einträgen, HTTP 200, zweiter Aufruf wird trotz Fehler beim ersten
  ausgeführt (kein Abbruch).
- `GET /api/ebay/oauth/status`: reicht die Antwort von
  `httpx.get(EBAY_OAUTH_SERVER_URL + "/api/oauth/status")` 1:1 durch
  (gemockt).
- `POST /api/ebay/sync-sales`: Bestellzeile mit bekanntem `sku` →
  `db.upsert_ebay_sale` aufgerufen, zugehöriges Listing auf
  `status="Verkauft"`; Bestellzeile mit unbekanntem `sku` → landet nicht
  in `upsert_ebay_sale`-Aufrufen, zählt in `skipped`; Antwort
  `{"synced": n, "skipped": n}`; `EbayNotAuthorizedError` von
  `ebay_client.get_access_token` → 401 mit deutscher Meldung.

- [ ] **Step 2:** `python3 -m unittest discover -s tests -v` → FAIL (neue
  Routen fehlen).

- [ ] **Step 3: Endpoints in `main.py` ergänzen**

Neue Imports am Dateianfang:

```python
from datetime import datetime, timezone
import httpx

import ebay_client
import ebay_listing
```

Helper (vor den neuen Routen einfügen):

```python
def _listing_with_card(listing):
    listing = dict(listing)
    card = db.get_card(listing["card_id"]) or {}
    listing["card"] = {"id": listing["card_id"], "title": card.get("title", "")}
    front_path = card.get("front_image_path")
    if front_path:
        try:
            listing["card"]["front_image_url"] = storage.signed_url(front_path)
        except Exception:
            pass
    return listing


def _publish_listing(listing, scheduled_at=None):
    listing_type = listing["listing_type"]
    missing = ebay_listing.missing_aspects(listing.get("aspects") or {}, listing_type)
    if missing:
        raise HTTPException(status_code=422, detail="Pflichtfelder fehlen: " + ", ".join(missing))

    if scheduled_at is not None:
        mode = "native" if ebay_client.NATIVE_SCHEDULING_SUPPORTED else "app"
        updates = {"status": "Geplant", "scheduled_at": scheduled_at, "scheduling_mode": mode}
        if mode == "app":
            return db.update_ebay_listing(listing["id"], updates)
        # native: faellt durch auf den normalen Publish-Ablauf unten, der
        # Offer wird JETZT angelegt (s. Spec, Abschnitt Scheduling).

    try:
        token = ebay_client.get_access_token()
        policies = ebay_client.get_listing_policies(token)
        image_url = None
        card = db.get_card(listing["card_id"]) or {}
        if card.get("front_image_path"):
            image_url = storage.public_url(card["front_image_path"])
        ebay_client.put_inventory_item(token, listing["sku"], card, image_url)
        offer_id = listing.get("ebay_offer_id")
        payload = {**listing, "policies": policies}
        if offer_id:
            ebay_client.update_offer(token, offer_id, payload)
        else:
            offer_id = ebay_client.create_offer(token, listing["sku"], payload)
        ebay_listing_id = ebay_client.publish_offer(
            token, offer_id, scheduled_at=scheduled_at if scheduled_at is not None and ebay_client.NATIVE_SCHEDULING_SUPPORTED else None
        )
    except ebay_client.EbayNotAuthorizedError as exc:
        db.update_ebay_listing(listing["id"], {"status": "Fehler", "last_error": str(exc)})
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ebay_client.EbayApiError as exc:
        db.update_ebay_listing(listing["id"], {"status": "Fehler", "last_error": str(exc)})
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    updates = {
        "ebay_offer_id": offer_id, "ebay_listing_id": ebay_listing_id or "",
        "last_error": "",
    }
    if scheduled_at is not None:
        updates.update({"status": "Geplant", "scheduled_at": scheduled_at, "scheduling_mode": "native"})
    else:
        updates.update({"status": "Veroeffentlicht", "published_at": datetime.now(timezone.utc).isoformat()})
    return db.update_ebay_listing(listing["id"], updates)
```

Routen (ans Ende von `main.py`, vor dem `static_dir`-Mount):

```python
@app.post("/api/cards/{card_id}/ebay-listing")
async def create_ebay_listing(card_id: str, fields: dict = Body(default={})):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    if db.get_ebay_listing_for_card(card_id) is not None:
        raise HTTPException(status_code=409, detail="Für diese Karte existiert bereits ein eBay-Angebot.")

    listing_type = fields.get("listing_type") or ebay_listing.derive_listing_type(card)
    row = {
        "title": fields.get("title") or ebay_listing.generate_title(card),
        "description": fields.get("description") or ebay_listing.generate_description(card),
        "condition": fields.get("condition", "NM"),
        "condition_id": fields.get("condition_id", "4000"),
        "listing_type": listing_type,
        "category_id": fields.get("category_id") or ebay_listing.CATEGORY_IDS[listing_type],
        "aspects": fields.get("aspects") or ebay_listing.build_aspects(card, listing_type),
        "price": fields.get("price", 0),
        "quantity": fields.get("quantity", 1),
    }
    sku = ebay_listing.sku_for_card(card_id)
    listing = db.create_ebay_listing(card_id, sku, row)
    listing["required_aspects"] = ebay_listing.required_aspects(listing_type)
    return JSONResponse(_listing_with_card(listing))


@app.get("/api/ebay/listings")
async def list_ebay_listings(status: str | None = None, q: str | None = None):
    listings = [_listing_with_card(l) for l in db.list_ebay_listings(status=status, q=q)]
    return JSONResponse({"listings": listings})


@app.get("/api/ebay/listings/{listing_id}")
async def get_ebay_listing(listing_id: str):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    return JSONResponse(_listing_with_card(listing))


@app.patch("/api/ebay/listings/{listing_id}")
async def update_ebay_listing(listing_id: str, fields: dict = Body(...)):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    updated = db.update_ebay_listing(listing_id, fields)
    if updated["status"] == "Veroeffentlicht":
        updated = _publish_listing(updated)
    return JSONResponse(_listing_with_card(updated))


@app.delete("/api/ebay/listings/{listing_id}", status_code=204)
async def delete_ebay_listing(listing_id: str):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    if listing["status"] != "Entwurf":
        raise HTTPException(status_code=409, detail="Nur Entwürfe können gelöscht werden.")
    db.delete_ebay_listing(listing_id)
    return Response(status_code=204)


@app.post("/api/ebay/listings/{listing_id}/publish")
async def publish_ebay_listing(listing_id: str, body: dict = Body(default={})):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    scheduled_at = body.get("scheduled_at")
    if scheduled_at and scheduled_at <= datetime.now(timezone.utc).isoformat():
        scheduled_at = None
    updated = _publish_listing(listing, scheduled_at=scheduled_at)
    return JSONResponse(_listing_with_card(updated))


@app.post("/api/ebay/listings/{listing_id}/unschedule")
async def unschedule_ebay_listing(listing_id: str):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    if listing["status"] != "Geplant":
        raise HTTPException(status_code=409, detail="Nur geplante Angebote können storniert werden.")
    if listing["scheduling_mode"] == "native" and listing.get("ebay_offer_id"):
        token = ebay_client.get_access_token()
        ebay_client.withdraw_offer(token, listing["ebay_offer_id"])
    updated = db.update_ebay_listing(listing_id, {
        "status": "Entwurf", "scheduled_at": None, "scheduling_mode": "",
    })
    return JSONResponse(_listing_with_card(updated))


@app.post("/api/ebay/listings/publish-bulk")
async def publish_ebay_listings_bulk(body: dict = Body(...)):
    results = []
    for listing_id in body.get("listing_ids", []):
        listing = db.get_ebay_listing(listing_id)
        if listing is None:
            results.append({"listing_id": listing_id, "status": "Fehler", "error": "Nicht gefunden."})
            continue
        try:
            updated = _publish_listing(listing)
            results.append({"listing_id": listing_id, "status": updated["status"]})
        except HTTPException as exc:
            results.append({"listing_id": listing_id, "status": "Fehler", "error": str(exc.detail)})
    return JSONResponse({"results": results})


@app.get("/api/ebay/oauth/status")
async def ebay_oauth_status():
    response = httpx.get(f"{ebay_client.EBAY_OAUTH_SERVER_URL}/api/oauth/status", timeout=15)
    return JSONResponse(response.json(), status_code=response.status_code)


@app.post("/api/ebay/sync-sales")
async def sync_ebay_sales():
    try:
        token = ebay_client.get_access_token()
    except ebay_client.EbayNotAuthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    cursor = db.latest_sale_sync_cursor()
    since = cursor or (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    orders = ebay_client.get_orders(token, since)
    listings_by_sku = {l["sku"]: l for l in db.list_ebay_listings()}

    synced = skipped = 0
    for order in orders:
        for line_item in order.get("lineItems", []):
            listing = ebay_listing.match_sale_line_item(line_item, listings_by_sku)
            if listing is None:
                skipped += 1
                continue
            db.upsert_ebay_sale({
                "listing_id": listing["id"], "card_id": listing["card_id"],
                "ebay_order_id": order.get("orderId", ""),
                "ebay_line_item_id": line_item.get("lineItemId", ""),
                "sale_date": order.get("creationDate"),
                "quantity": line_item.get("quantity", 1),
                "gross_price": float((line_item.get("total") or {}).get("value", 0) or 0),
            })
            db.update_ebay_listing(listing["id"], {"status": "Verkauft"})
            synced += 1
    return JSONResponse({"synced": synced, "skipped": skipped})
```

(`timedelta` zusätzlich aus `datetime` importieren.)

- [ ] **Step 4:** `python3 -m unittest discover -s tests -v` → PASS.

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/main.py tests/test_webapp_poc_ebay_endpoints.py
git commit -m "Add /api/ebay/* endpoints: listing CRUD, publish, publish-bulk, sync-sales"
```

---

### Task 8: `webapp-poc/ebay_scheduler.py` – App-seitiger Scheduler

**Files:**
- Create: `webapp-poc/ebay_scheduler.py`
- Modify: `webapp-poc/main.py` (Startup-Hook)
- Create: `tests/test_webapp_poc_ebay_scheduler.py`

**Interfaces:**
- Consumes: `db.list_due_scheduled_listings("app")`,
  `db.list_native_scheduled_listings()`, `ebay_client.get_offer`,
  `main._publish_listing` (Kreisimport vermeiden: `ebay_scheduler.py`
  bekommt die Publish-Funktion als Parameter injiziert statt `main`
  direkt zu importieren, s. Step 3).

- [ ] **Step 1: Fehlschlagende Tests schreiben**

Testfälle für `tests/test_webapp_poc_ebay_scheduler.py`:

- `run_once(publish_fn)`: ruft `publish_fn(listing)` für jede fällige
  `app`-geplante Zeile aus `list_due_scheduled_listings`; ein Fehler bei
  einer Zeile (z. B. `publish_fn` wirft) bricht die Schleife nicht ab,
  restliche fällige Zeilen werden trotzdem versucht (Fehler wird
  geloggt/verschluckt — der eigentliche Statuswechsel auf `"Fehler"`
  passiert bereits in `_publish_listing` selbst, s. Task 7).
- `run_once(publish_fn)`: für jede Zeile aus `list_native_scheduled_listings`
  ruft es `ebay_client.get_offer` auf; meldet die Antwort ein aktives
  Listing (Kriterium: eine konkrete, im Test gemockte Antwortform, z. B.
  `{"status": "PUBLISHED"}` — exaktes Feld hängt vom echten
  Sandbox-Spike-Ergebnis ab, hier als Platzhalter-Check `"listingId" in
  offer` implementiert), setzt `db.update_ebay_listing(..., {"status":
  "Veroeffentlicht"})`; sonst unverändert.

- [ ] **Step 2:** `python3 -m unittest tests.test_webapp_poc_ebay_scheduler -v`
  → FAIL.

- [ ] **Step 3: `webapp-poc/ebay_scheduler.py` implementieren**

```python
"""Background scheduler for app-side eBay listing scheduling (fallback,
s. Spec 'Scheduling'). publish_fn wird von main.py injiziert statt hier
main zu importieren - main.py importiert bereits dieses Modul, ein
Rueckimport wuerde einen Zirkelimport erzeugen."""
import asyncio
import logging

import db
import ebay_client

logger = logging.getLogger("ebay_scheduler")
INTERVAL_SECONDS = 300


def run_once(publish_fn):
    for listing in db.list_due_scheduled_listings("app"):
        try:
            publish_fn(listing)
        except Exception:
            logger.exception("App-seitiges Scheduled-Publish fehlgeschlagen fuer %s", listing.get("id"))

    for listing in db.list_native_scheduled_listings():
        try:
            token = ebay_client.get_access_token()
            offer = ebay_client.get_offer(token, listing["ebay_offer_id"])
            if offer.get("listingId"):
                db.update_ebay_listing(listing["id"], {"status": "Veroeffentlicht"})
        except Exception:
            logger.exception("Status-Abgleich fuer geplantes Angebot %s fehlgeschlagen", listing.get("id"))


async def run_forever(publish_fn):
    while True:
        run_once(publish_fn)
        await asyncio.sleep(INTERVAL_SECONDS)
```

In `main.py`, nach den bestehenden Imports:

```python
import ebay_scheduler

@app.on_event("startup")
async def _start_ebay_scheduler():
    asyncio.create_task(ebay_scheduler.run_forever(lambda listing: _publish_listing(listing)))
```

(`import asyncio` ergänzen, falls noch nicht vorhanden.)

- [ ] **Step 4:** `python3 -m unittest tests.test_webapp_poc_ebay_scheduler -v`
  → PASS.

- [ ] **Step 5:** `python3 -m unittest discover -s tests -v` → PASS (keine
  Regression durch den Startup-Hook — `TestClient` löst
  `@app.on_event("startup")` nicht automatisch aus, außer über den
  `with TestClient(...) as client:`-Kontextmanager, den die bestehenden
  Tests nicht nutzen; keine Anpassung an bestehenden Tests nötig).

- [ ] **Step 6: Commit**

```bash
git add webapp-poc/ebay_scheduler.py webapp-poc/main.py tests/test_webapp_poc_ebay_scheduler.py
git commit -m "Add ebay_scheduler.py: background loop for app-side scheduled publishing"
```

---

### Task 9: `webapp-poc/static/ebay.html` – Angebots-Übersicht

**Files:**
- Create: `webapp-poc/static/ebay.html`

**Interfaces:**
- Consumes: `GET /api/ebay/listings`, `GET /api/ebay/oauth/status`, `POST
  /api/ebay/listings/publish-bulk`, `POST /api/ebay/sync-sales` (Task 7/8).

Kein Backend-Code, daher kein TDD-Zyklus – Verifikation ist manuelles
Testen im Browser.

- [ ] **Step 1: Seite erstellen** — Struktur/Verhalten exakt wie
  Spec-Abschnitt "UI-Design → `ebay.html`": OAuth-Status-Banner,
  Status-Filter (inkl. `Geplant`) + Freitextsuche (Debounce wie
  `cards.html`), Tabelle mit Checkbox-Spalte + Thumbnail/Titel/Preis/
  Status/`scheduled_at`, "Ausgewählte veröffentlichen" (aktiv ab ≥1
  Checkbox mit Status `Entwurf`/`Fehler`) → `publish-bulk` + Ergebnisliste
  pro Zeile, "Verkäufe synchronisieren" → `sync-sales` + Statusmeldung.
  Gleicher CSS-/JS-Stil wie `cards.html`/`purchases.html` (kein
  Framework, `#status`-Textmuster, `try/catch` um jeden `sessionStorage`-
  Zugriff).

- [ ] **Step 2: Manuell verifizieren** — Server starten, `/ebay.html`
  öffnen. Erwartet: leere Liste initial, Status-Banner zeigt "Nicht
  verbunden" (kein eBay-Token im Dev-Setup), Filter/Suche funktionieren
  gegen echte `GET /api/ebay/listings`-Antworten sobald über `card.html`
  (Task 10) Entwürfe existieren.

- [ ] **Step 3: Commit**

```bash
git add webapp-poc/static/ebay.html
git commit -m "Add ebay.html: listing overview with bulk-publish and sales sync"
```

---

### Task 10: `webapp-poc/static/card.html` – erweiterter "eBay"-Bereich

**Files:**
- Modify: `webapp-poc/static/card.html`

**Interfaces:**
- Consumes: `POST /api/cards/{id}/ebay-listing`, `GET/PATCH/DELETE
  /api/ebay/listings/{id}`, `POST /api/ebay/listings/{id}/publish`, `POST
  /api/ebay/listings/{id}/unschedule` (Task 7).

Kein Backend-Code, daher kein TDD-Zyklus – Verifikation ist manuelles
Testen im Browser.

- [ ] **Step 1: Bereich ergänzen** — Struktur/Verhalten exakt wie
  Spec-Abschnitt "UI-Design → `card.html`": neuer Abschnitt unterhalb des
  bestehenden "Kauf"-Bereichs, ein Zustand pro `status`
  (kein Angebot/Entwurf/Geplant/Veröffentlicht/Verkauft/Fehler), inkl.
  Kartentyp-Dropdown, Aspects-Key-Value-Liste, "Preis recherchieren"-Link
  (zwei `target="_blank"`-Links aus der `required_aspects`/
  `price_research_links`-Antwort von `POST .../ebay-listing`),
  Datum/Uhrzeit-Feld + "Planen"/"Veröffentlichen"-Umschaltung je nachdem,
  ob das Feld gesetzt ist. Gleicher CSS-/JS-Stil wie der bestehende
  "Kauf"-Bereich in derselben Datei (Wiederverwendung der vorhandenen
  `<style>`-Klassen wo sinnvoll).

- [ ] **Step 2: Manuell verifizieren** — `card.html?id=<uuid>` einer
  bestehenden Karte öffnen. "eBay-Angebot erstellen" ausklappen, Felder
  sind vorausgefüllt (Netzwerk-Tab: `POST /api/cards/{id}/ebay-listing`
  ohne Body beim ersten Ausklappen, oder ein "Vorschlag laden"-Button —
  Detailentscheidung beim Implementieren, konsistent mit dem
  "Kauf"-Bereich). Entwurf speichern, Kartentyp-Dropdown umschalten ändert
  die Aspects-Vorschau. Preis + Datum setzen, "Planen" klicken → Status
  wechselt auf "Geplant" (kein echter eBay-Aufruf im Dev-Setup ohne
  Sandbox-Credentials — Fehlerpfad `EbayNotAuthorizedError`/502 sauber als
  Meldung im `#status`-Bereich sichtbar, das ist der erwartete Zustand
  ohne konfigurierten oauth-server).

- [ ] **Step 3: Commit**

```bash
git add webapp-poc/static/card.html
git commit -m "Add eBay listing section to card.html: create, edit, schedule, publish"
```

---

### Task 11: Deployment-Doku, Env-Vars, `webapp-poc/README.md`

**Files:**
- Modify: `webapp-poc/README.md`
- Modify: `docker-compose.webapp-poc.yml`
- Modify: `webapp-poc/requirements.txt` (nur falls Task 5/7 doch eine neue
  Dependency braucht — nach aktuellem Stand nicht der Fall, `httpx` reicht)

**Interfaces:** keine (reine Dokumentation/Konfiguration).

- [ ] **Step 1:** `docker-compose.webapp-poc.yml` um `EBAY_OAUTH_SERVER_URL`
  und `EBAY_ENVIRONMENT` als Env-Vars für den `webapp-poc`-Service
  ergänzen (Default-Werte wie in `ebay_client.py`, überschreibbar).
- [ ] **Step 2:** `webapp-poc/README.md`: den bestehenden Abschnitt "Was
  absichtlich fehlt" um die jetzt erledigte eBay-Integration kürzen/
  entfernen, neuen Abschnitt mit den neuen Endpoints/Seiten ergänzen
  (gleiches Format wie die bestehende Liste), plus einen Hinweis auf
  `NATIVE_SCHEDULING_SUPPORTED=False` und den offenen Sandbox-Spike als
  bekannte Einschränkung, bis jemand mit echtem NAS-Zugriff ihn
  durchführt.
- [ ] **Step 3: Commit**

```bash
git add webapp-poc/README.md docker-compose.webapp-poc.yml
git commit -m "Document eBay integration deployment: env vars, open sandbox-spike caveat"
```

---

### Task 12: Whole-Branch Review

- [ ] **Step 1:** `python3 -m unittest discover -s tests -v` — vollständiger
  Lauf, keine Regression in Sub-Projekt 1–3.
- [ ] **Step 2:** `code-review`-Skill über den gesamten Branch-Diff laufen
  lassen (Fokus: Korrektheit + Reuse/Simplification, wie in den
  vorherigen drei Sub-Projekten).
- [ ] **Step 3:** Gefundene Probleme beheben, Tests erneut grün.
- [ ] **Step 4:** Nutzer über den Stand informieren: was ist fertig, was
  bleibt offen (Sandbox-Spike für natives Scheduling — muss auf dem NAS
  mit echten Credentials passieren, nicht aus dieser Session heraus
  möglich), nächste Schritte fürs NAS-Deployment (Schema erneut einspielen,
  Bucket public stellen, Container neu bauen, `EBAY_*`-Env-Vars setzen).
