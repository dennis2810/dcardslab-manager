# WebApp Käufe/Purchases Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Käufe (Einzelkauf oder Sammelkauf/Lot) lassen sich erfassen, durchsuchen, bearbeiten und löschen, und einzelne Karten lassen sich einem Kauf zuordnen — sowohl über eine eigene Käufe-Liste/-Detailseite als auch direkt aus `card.html` heraus.

**Architecture:** Zwei neue Supabase-Tabellen (`purchases`, `purchase_items`), neue Funktionen in `webapp-poc/db.py`, neue Endpoints in `webapp-poc/main.py`, drei neue/erweiterte Vanilla-JS-Seiten (`purchases.html`, `purchase.html`, erweitertes `card.html`). Gleiches Muster wie Sub-Projekt 1/2, kein neuer Service, kein Build-Schritt.

**Tech Stack:** FastAPI, Supabase Postgres (bestehend), Vanilla JS/HTML (kein neues Framework).

**Spec:** `docs/superpowers/specs/2026-08-27-webapp-purchases-design.md`

## Global Constraints

- Tabellen-/Spaltennamen exakt wie in der Spec: `purchases` (`purchase_date` **not null**, `platform`, `seller`, `shipping`, `total_price`, `notes`), `purchase_items` (`purchase_id`, `card_id` **unique**, `allocated_cost`, `quantity`, `notes`).
- `unique(card_id)` auf `purchase_items` — eine Karte gehört höchstens einem Kauf. `db.py` prüft das per Vorab-`select` (kein Rückgriff auf das Parsen von Postgres-Fehlercodes) und wirft `db.CardAlreadyLinkedError`, die `main.py` in einen 409 mit deutscher Fehlermeldung übersetzt.
- `POST /api/purchases` mit `items` ist alles-oder-nichts: schlägt ein Item fehl (z. B. `CardAlreadyLinkedError`), werden bereits eingefügte Items **und** der Kauf selbst per Kompensations-Löschung wieder entfernt (keine echte DB-Transaktion nötig — kein neues Pattern für dieses Projekt, konsistent mit der bestehenden Best-Effort-Fehlerbehandlung z. B. bei `POST /api/scan`).
- `purchase_date` ist im Frontend-Formular ein `required`-Feld (HTML5) statt serverseitiger Validierung — die `not null`-Spalte ist das Backstop, kein zusätzlicher Python-Code nötig (konsistent mit dem Rest des Projekts, das auf minimale serverseitige Validierung setzt).
- Bearbeitbare Kauf-Felder sind exakt `db.PURCHASE_FIELDS`, bearbeitbare Item-Felder exakt `db.PURCHASE_ITEM_FIELDS` — analog zur bestehenden `CARD_FIELDS`-Whitelist in `update_card`.
- `GET /api/cards/{id}` bekommt ein zusätzliches `purchase`-Feld (`null` oder flaches Objekt inkl. `item_id`, siehe Task 3), `GET /api/cards` (Liste) ein `has_purchase`-Bool je Karte — beide zusätzlich zu allen bestehenden Feldern, keine Breaking Changes an bestehenden Response-Feldern.
- Kein neues JS-Framework, kein Build-Schritt - reines Vanilla JS wie in `static/cards.html`/`card.html`.
- Deutsche Statustexte/Fehlermeldungen im bestehenden Stil.
- Supabase-Client wird in allen Tests gemockt (kein echter Netzwerk-Call in CI). **Wichtig:** Funktionen, die mehr als eine Tabelle anfassen (z. B. `create_purchase` → `purchases` **und** `purchase_items`), brauchen einen Mock, der `client.table(name)` je nach `name` unterschiedliche Builder zurückgibt (`side_effect=lambda name: {...}[name]`) — der bestehende `_mock_table()`-Helper in `tests/test_webapp_poc_db.py` reicht dafür nicht, er ignoriert den Tabellennamen. Siehe Task 2, Step 1 für das konkrete Pattern.
- Vor-/Zurück-Navigation in `purchase.html` ist reines Frontend (`sessionStorage`), kein Backend-Endpoint, kein TDD-Zyklus dafür nötig — analog zu `card.html`s bereits gemergter Navigation.

---

### Task 1: Supabase-Schema erweitern (`purchases`, `purchase_items`)

**Files:**
- Modify: `supabase/schema.sql`
- Modify: `supabase/README.md`

**Interfaces:**
- Produces: Tabellen `purchases`, `purchase_items` im bestehenden Supabase-Projekt. Task 2 (`db.py`) setzt voraus, dass diese Namen/Spalten/Constraints exakt so existieren.

Kein Code, daher kein TDD-Zyklus – Verifikation ist manuelles erneutes Einspielen durch den Nutzer (Schritt 2).

- [ ] **Step 1: SQL an `supabase/schema.sql` anhängen**

Ans Ende der Datei anfügen (nach dem bestehenden `cards`-Index):

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

- [ ] **Step 2: Manuell verifizieren (kein automatischer Test möglich)**

Den kompletten (aktualisierten) Inhalt von `schema.sql` erneut im Supabase SQL Editor des bestehenden Projekts ausführen (`create table if not exists` ist idempotent, die bestehenden `scan_batches`/`cards`-Tabellen bleiben unangetastet). Prüfen, dass `purchases` und `purchase_items` im Table Editor erscheinen, inkl. Foreign Keys zu `cards`/`purchases` und dem Unique-Constraint auf `purchase_items.card_id`.

- [ ] **Step 3: `supabase/README.md` ergänzen**

Nach der bestehenden Schritt-2-Beschreibung (SQL Editor / `schema.sql` ausführen) einen Hinweis ergänzen:

```markdown
   (Bei einem Schema-Update für ein bereits bestehendes Projekt: einfach
   den kompletten, aktuellen Inhalt von `schema.sql` erneut ausführen —
   `create table if not exists` überspringt bereits vorhandene Tabellen.)
```

- [ ] **Step 4: Commit**

```bash
git add supabase/schema.sql supabase/README.md
git commit -m "Add purchases/purchase_items tables to Supabase schema"
```

---

### Task 2: `webapp-poc/db.py` – Käufe-/Purchase-Items-Persistenz

**Files:**
- Modify: `webapp-poc/db.py`
- Modify: `tests/test_webapp_poc_db.py`

**Interfaces:**
- Consumes: `supabase_client.get_client()` (bestehend).
- Produces: `create_purchase(fields, items=None) -> dict` (inkl. `"items"`-Liste; wirft `CardAlreadyLinkedError`, räumt dabei bereits eingefügte Items+Kauf wieder ab), `list_purchases(q=None) -> list[dict]` (inkl. `item_count` je Eintrag), `get_purchase(purchase_id) -> dict | None` (inkl. `"items"`), `update_purchase(purchase_id, fields) -> dict | None` (inkl. `"items"`), `delete_purchase(purchase_id) -> dict | None`, `add_purchase_item(purchase_id, fields) -> dict` (wirft `CardAlreadyLinkedError`), `update_purchase_item(purchase_id, item_id, fields) -> dict | None`, `delete_purchase_item(purchase_id, item_id) -> dict | None`, `get_purchase_for_card(card_id) -> dict | None`, `cards_with_purchase(card_ids) -> set[str]`, `get_cards_by_ids(card_ids) -> list[dict]`. Task 3 (`main.py`) ruft alle mit exakt dieser Signatur auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

An `tests/test_webapp_poc_db.py` anhängen (vor `if __name__ == "__main__":`). Für `create_purchase` wird ein Mock gebraucht, der `client.table(name)` je nach Tabellenname unterschiedliche Builder liefert (der bestehende `_mock_table()`-Helper reicht dafür nicht):

```python
def _mock_client_for_tables(**table_builders):
    """client.table(name) liefert table_builders[name] statt eines einzigen
    geteilten Mocks - noetig fuer Funktionen, die mehr als eine Tabelle
    anfassen (z.B. create_purchase() -> purchases UND purchase_items)."""
    client = MagicMock()
    client.table.side_effect = lambda name: table_builders[name]
    return client


class CreatePurchaseTests(unittest.TestCase):
    def test_creates_purchase_without_items(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "purchases", [{"id": "purchase-1", "purchase_date": "2026-08-27"}])
        with patch("db.get_client", return_value=mock_client):
            result = db.create_purchase({"purchase_date": "2026-08-27"})
        self.assertEqual(result["id"], "purchase-1")
        self.assertEqual(result["items"], [])
        mock_client.table.return_value.insert.assert_called_once_with(
            {"purchase_date": "2026-08-27"}
        )

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "purchases", [{"id": "purchase-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_purchase({"purchase_date": "2026-08-27", "not_a_real_column": "x"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertNotIn("not_a_real_column", row)

    def test_creates_purchase_with_single_item(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "purchase-1"}]
        purchases_builder.insert.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        dup_check = MagicMock()
        dup_check.data = []
        items_builder.select.return_value.eq.return_value.execute.return_value = dup_check
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1", "purchase_id": "purchase-1", "card_id": "card-1"}]
        items_builder.insert.return_value.execute.return_value = insert_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.create_purchase({"purchase_date": "2026-08-27"}, items=[{"card_id": "card-1"}])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["card_id"], "card-1")

    def test_rolls_back_purchase_and_items_when_an_item_is_already_linked(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "purchase-1"}]
        purchases_builder.insert.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        not_linked = MagicMock()
        not_linked.data = []
        already_linked = MagicMock()
        already_linked.data = [{"id": "existing-item"}]
        items_builder.select.return_value.eq.return_value.execute.side_effect = [not_linked, already_linked]
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1", "purchase_id": "purchase-1", "card_id": "card-1"}]
        items_builder.insert.return_value.execute.return_value = insert_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            with self.assertRaises(db.CardAlreadyLinkedError):
                db.create_purchase(
                    {"purchase_date": "2026-08-27"},
                    items=[{"card_id": "card-1"}, {"card_id": "card-2"}],
                )
        items_builder.delete.return_value.eq.assert_called_once_with("id", "item-1")
        purchases_builder.delete.return_value.eq.assert_called_once_with("id", "purchase-1")


class ListPurchasesTests(unittest.TestCase):
    def test_computes_item_count_per_purchase(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "p1"}, {"id": "p2"}]
        purchases_builder.select.return_value.order.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{"purchase_id": "p1"}, {"purchase_id": "p1"}, {"purchase_id": "p2"}]
        items_builder.select.return_value.in_.return_value.execute.return_value = items_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.list_purchases()
        counts = {p["id"]: p["item_count"] for p in result}
        self.assertEqual(counts, {"p1": 2, "p2": 1})

    def test_q_filters_platform_seller_notes(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_purchases(q="eBay")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertIn("platform.ilike.%eBay%", filter_arg)
        self.assertIn("seller.ilike.%eBay%", filter_arg)
        self.assertIn("notes.ilike.%eBay%", filter_arg)


class GetPurchaseTests(unittest.TestCase):
    def test_returns_purchase_with_items(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "p1", "platform": "eBay"}]
        purchases_builder.select.return_value.eq.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{"id": "item-1", "card_id": "card-1"}]
        items_builder.select.return_value.eq.return_value.execute.return_value = items_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.get_purchase("p1")
        self.assertEqual(result["platform"], "eBay")
        self.assertEqual(result["items"], [{"id": "item-1", "card_id": "card-1"}])

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_purchase("does-not-exist")
        self.assertIsNone(result)


class UpdatePurchaseTests(unittest.TestCase):
    def test_updates_only_provided_fields_and_reattaches_items(self):
        purchases_builder = MagicMock()
        update_response = MagicMock()
        update_response.data = [{"id": "p1", "platform": "Kleinanzeigen"}]
        purchases_builder.update.return_value.eq.return_value.execute.return_value = update_response

        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{"id": "item-1"}]
        items_builder.select.return_value.eq.return_value.execute.return_value = items_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase("p1", {"platform": "Kleinanzeigen"})
        self.assertEqual(result["platform"], "Kleinanzeigen")
        self.assertEqual(result["items"], [{"id": "item-1"}])

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase("does-not-exist", {"platform": "x"})
        self.assertIsNone(result)


class DeletePurchaseTests(unittest.TestCase):
    def test_deletes_and_returns_purchase(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "p1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_purchase("p1")
        self.assertEqual(result, {"id": "p1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "p1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_purchase("does-not-exist")
        self.assertIsNone(result)
        mock_client.table.return_value.delete.assert_not_called()


class AddPurchaseItemTests(unittest.TestCase):
    def test_links_card_to_purchase(self):
        mock_client = MagicMock()
        dup_check = MagicMock()
        dup_check.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = dup_check
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1", "purchase_id": "p1", "card_id": "card-1"}]
        mock_client.table.return_value.insert.return_value.execute.return_value = insert_response
        with patch("db.get_client", return_value=mock_client):
            result = db.add_purchase_item("p1", {"card_id": "card-1", "allocated_cost": 12.5})
        self.assertEqual(result["card_id"], "card-1")
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["allocated_cost"], 12.5)
        self.assertEqual(row["quantity"], 1)  # default

    def test_raises_when_card_already_linked(self):
        mock_client = MagicMock()
        dup_check = MagicMock()
        dup_check.data = [{"id": "existing-item"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = dup_check
        with patch("db.get_client", return_value=mock_client):
            with self.assertRaises(db.CardAlreadyLinkedError):
                db.add_purchase_item("p1", {"card_id": "card-1"})
        mock_client.table.return_value.insert.assert_not_called()


class UpdatePurchaseItemTests(unittest.TestCase):
    def test_updates_only_provided_fields(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "item-1", "notes": "LP statt NM"}]
        mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase_item("p1", "item-1", {"notes": "LP statt NM"})
        self.assertEqual(result["notes"], "LP statt NM")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase_item("p1", "does-not-exist", {"notes": "x"})
        self.assertIsNone(result)


class DeletePurchaseItemTests(unittest.TestCase):
    def test_deletes_and_returns_item(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "item-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_purchase_item("p1", "item-1")
        self.assertEqual(result, {"id": "item-1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "item-1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_purchase_item("p1", "does-not-exist")
        self.assertIsNone(result)
        mock_client.table.return_value.delete.assert_not_called()


class GetPurchaseForCardTests(unittest.TestCase):
    def test_returns_flat_purchase_info_when_linked(self):
        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{
            "id": "item-1", "purchase_id": "p1",
            "allocated_cost": 12.5, "quantity": 1, "notes": "",
        }]
        items_builder.select.return_value.eq.return_value.execute.return_value = items_response

        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{
            "id": "p1", "purchase_date": "2026-08-27", "platform": "eBay", "seller": "cardguy88",
        }]
        purchases_builder.select.return_value.eq.return_value.execute.return_value = purchases_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.get_purchase_for_card("card-1")
        self.assertEqual(result["purchase_id"], "p1")
        self.assertEqual(result["item_id"], "item-1")
        self.assertEqual(result["platform"], "eBay")
        self.assertEqual(result["allocated_cost"], 12.5)

    def test_returns_none_when_not_linked(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_purchase_for_card("card-1")
        self.assertIsNone(result)


class CardsWithPurchaseTests(unittest.TestCase):
    def test_returns_set_of_linked_card_ids(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"card_id": "card-1"}, {"card_id": "card-2"}]
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.cards_with_purchase(["card-1", "card-2", "card-3"])
        self.assertEqual(result, {"card-1", "card-2"})

    def test_empty_input_skips_query(self):
        mock_client = MagicMock()
        with patch("db.get_client", return_value=mock_client):
            result = db.cards_with_purchase([])
        self.assertEqual(result, set())
        mock_client.table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

(Die vorhandene `if __name__ == "__main__": unittest.main()`-Zeile am Dateiende bleibt bestehen - die neuen Klassen kommen davor.)

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_db -v`
Expected: FAIL - `AttributeError: module 'db' has no attribute 'create_purchase'` (und die übrigen neuen Funktionen/`CardAlreadyLinkedError`).

- [ ] **Step 3: `db.py` erweitern**

Import ergänzen (nach den bestehenden Imports):

```python
from collections import Counter
```

Ans Ende von `webapp-poc/db.py` anfügen:

```python
PURCHASE_FIELDS = ["purchase_date", "platform", "seller", "shipping", "total_price", "notes"]
PURCHASE_ITEM_DEFAULTS = {"allocated_cost": 0, "quantity": 1, "notes": ""}


class CardAlreadyLinkedError(Exception):
    """Raised by add_purchase_item() when card_id bereits eine
    purchase_items-Zeile hat (unique(card_id) im Schema). Die Karten-ID
    steht in exc.args[0]."""


def create_purchase(fields, items=None):
    row = {name: fields[name] for name in PURCHASE_FIELDS if name in fields}
    response = get_client().table("purchases").insert(row).execute()
    purchase = response.data[0]
    inserted_items = []
    try:
        for item_fields in items or []:
            inserted_items.append(add_purchase_item(purchase["id"], item_fields))
    except CardAlreadyLinkedError:
        # Alles-oder-nichts: bereits eingefuegte Items und den Kauf selbst
        # wieder entfernen, statt einen halb verknuepften Kauf zurueckzulassen.
        for inserted in inserted_items:
            get_client().table("purchase_items").delete().eq("id", inserted["id"]).execute()
        get_client().table("purchases").delete().eq("id", purchase["id"]).execute()
        raise
    purchase["items"] = inserted_items
    return purchase


def list_purchases(q=None):
    query = get_client().table("purchases").select("*")
    if q:
        safe_q = q.replace(",", " ").replace("(", " ").replace(")", " ")
        pattern = f"%{safe_q}%"
        query = query.or_(
            f"platform.ilike.{pattern},seller.ilike.{pattern},notes.ilike.{pattern}"
        )
    response = query.order("purchase_date", desc=True).execute()
    purchases = response.data
    if not purchases:
        return purchases
    ids = [p["id"] for p in purchases]
    items_response = get_client().table("purchase_items").select("purchase_id").in_("purchase_id", ids).execute()
    counts = Counter(item["purchase_id"] for item in items_response.data)
    for p in purchases:
        p["item_count"] = counts.get(p["id"], 0)
    return purchases


def _list_purchase_items(purchase_id):
    response = get_client().table("purchase_items").select("*").eq("purchase_id", purchase_id).execute()
    return response.data


def get_purchase(purchase_id):
    response = get_client().table("purchases").select("*").eq("id", purchase_id).execute()
    if not response.data:
        return None
    purchase = response.data[0]
    purchase["items"] = _list_purchase_items(purchase_id)
    return purchase


def update_purchase(purchase_id, fields):
    row = {name: value for name, value in fields.items() if name in PURCHASE_FIELDS}
    if not row:
        return get_purchase(purchase_id)
    response = get_client().table("purchases").update(row).eq("id", purchase_id).execute()
    if not response.data:
        return None
    purchase = response.data[0]
    purchase["items"] = _list_purchase_items(purchase_id)
    return purchase


def delete_purchase(purchase_id):
    response = get_client().table("purchases").select("id").eq("id", purchase_id).execute()
    if not response.data:
        return None
    get_client().table("purchases").delete().eq("id", purchase_id).execute()
    return response.data[0]


def add_purchase_item(purchase_id, fields):
    card_id = fields.get("card_id")
    existing = get_client().table("purchase_items").select("id").eq("card_id", card_id).execute()
    if existing.data:
        raise CardAlreadyLinkedError(card_id)
    row = {name: fields.get(name, default) for name, default in PURCHASE_ITEM_DEFAULTS.items()}
    row.update({"purchase_id": purchase_id, "card_id": card_id})
    response = get_client().table("purchase_items").insert(row).execute()
    return response.data[0] if response.data else None


def update_purchase_item(purchase_id, item_id, fields):
    row = {name: value for name, value in fields.items() if name in PURCHASE_ITEM_DEFAULTS}
    query = get_client().table("purchase_items")
    if not row:
        response = query.select("*").eq("id", item_id).eq("purchase_id", purchase_id).execute()
    else:
        response = query.update(row).eq("id", item_id).eq("purchase_id", purchase_id).execute()
    return response.data[0] if response.data else None


def delete_purchase_item(purchase_id, item_id):
    query = get_client().table("purchase_items")
    response = query.select("id").eq("id", item_id).eq("purchase_id", purchase_id).execute()
    if not response.data:
        return None
    get_client().table("purchase_items").delete().eq("id", item_id).execute()
    return response.data[0]


def get_purchase_for_card(card_id):
    items_response = get_client().table("purchase_items").select("*").eq("card_id", card_id).execute()
    if not items_response.data:
        return None
    item = items_response.data[0]
    purchases_response = get_client().table("purchases").select("*").eq("id", item["purchase_id"]).execute()
    if not purchases_response.data:
        return None
    purchase = purchases_response.data[0]
    return {
        "purchase_id": purchase["id"],
        "item_id": item["id"],
        "purchase_date": purchase.get("purchase_date", ""),
        "platform": purchase.get("platform", ""),
        "seller": purchase.get("seller", ""),
        "allocated_cost": item.get("allocated_cost", 0),
        "quantity": item.get("quantity", 1),
        "notes": item.get("notes", ""),
    }


def cards_with_purchase(card_ids):
    if not card_ids:
        return set()
    response = get_client().table("purchase_items").select("card_id").in_("card_id", card_ids).execute()
    return {row["card_id"] for row in response.data}


def get_cards_by_ids(card_ids):
    if not card_ids:
        return []
    response = get_client().table("cards").select("id,title,front_image_path").in_("id", card_ids).execute()
    return response.data
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest tests.test_webapp_poc_db -v`
Expected: PASS (alle bestehenden + alle neuen Klassen aus Step 1)

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/db.py tests/test_webapp_poc_db.py
git commit -m "Add purchases/purchase_items persistence functions to db.py"
```

---

### Task 3: Backend-Endpoints – Käufe-CRUD, Item-Verknüpfung, erweiterte Cards-Endpoints

**Files:**
- Modify: `webapp-poc/main.py`
- Create: `tests/test_webapp_poc_purchases_endpoints.py`
- Modify: `tests/test_webapp_poc_cards_endpoints.py`

**Interfaces:**
- Consumes: alle Funktionen aus Task 2, plus `storage.signed_url` (bestehend).
- Produces: `POST`/`GET`/`PATCH`/`DELETE /api/purchases[/{id}]`, `POST`/`PATCH`/`DELETE /api/purchases/{id}/items[/{item_id}]`, erweitertes `GET /api/cards` (`has_purchase`) und `GET /api/cards/{id}` (`purchase`). Task 4-6 (Frontend) rufen exakt diese Endpoints/Felder auf.

- [ ] **Step 1: Fehlschlagende Tests schreiben**

`tests/test_webapp_poc_purchases_endpoints.py` (neu, gleicher Aufbau wie `tests/test_webapp_poc_cards_endpoints.py`):

```python
"""Tests for /api/purchases[...] (webapp-poc/main.py)."""
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
import db  # noqa: E402

client = TestClient(main.app)


class CreatePurchaseEndpointTests(unittest.TestCase):
    def test_creates_purchase_with_card_summaries(self):
        created = {
            "id": "p1", "purchase_date": "2026-08-27",
            "items": [{"id": "item-1", "card_id": "card-1", "allocated_cost": 10}],
        }
        with patch("main.db.create_purchase", return_value=created) as mock_create, \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "card-1", "title": "Karte 1", "front_image_path": "b1/1_front.jpg"}]), \
             patch("main.storage.signed_url", return_value="https://signed/b1/1_front.jpg"):
            response = client.post("/api/purchases", json={
                "purchase_date": "2026-08-27", "items": [{"card_id": "card-1"}],
            })
        self.assertEqual(response.status_code, 200)
        mock_create.assert_called_once_with(
            {"purchase_date": "2026-08-27"}, [{"card_id": "card-1"}]
        )
        body = response.json()
        self.assertEqual(body["items"][0]["card"]["title"], "Karte 1")
        self.assertEqual(body["items"][0]["card"]["front_image_url"], "https://signed/b1/1_front.jpg")

    def test_returns_409_when_card_already_linked(self):
        with patch("main.db.create_purchase", side_effect=db.CardAlreadyLinkedError("card-1")):
            response = client.post("/api/purchases", json={
                "purchase_date": "2026-08-27", "items": [{"card_id": "card-1"}],
            })
        self.assertEqual(response.status_code, 409)


class ListPurchasesEndpointTests(unittest.TestCase):
    def test_passes_query_param_to_db(self):
        with patch("main.db.list_purchases", return_value=[]) as mock_list:
            response = client.get("/api/purchases?q=eBay")
        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(q="eBay")


class GetPurchaseEndpointTests(unittest.TestCase):
    def test_returns_purchase_with_expanded_items(self):
        purchase = {"id": "p1", "items": [{"id": "item-1", "card_id": "card-1"}]}
        with patch("main.db.get_purchase", return_value=purchase), \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "card-1", "title": "Karte 1", "front_image_path": None}]):
            response = client.get("/api/purchases/p1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"][0]["card"]["title"], "Karte 1")

    def test_returns_404_when_not_found(self):
        with patch("main.db.get_purchase", return_value=None):
            response = client.get("/api/purchases/does-not-exist")
        self.assertEqual(response.status_code, 404)


class UpdatePurchaseEndpointTests(unittest.TestCase):
    def test_updates_and_returns_purchase(self):
        updated = {"id": "p1", "platform": "Kleinanzeigen", "items": []}
        with patch("main.db.update_purchase", return_value=updated) as mock_update:
            response = client.patch("/api/purchases/p1", json={"platform": "Kleinanzeigen"})
        self.assertEqual(response.status_code, 200)
        mock_update.assert_called_once_with("p1", {"platform": "Kleinanzeigen"})

    def test_returns_404_when_not_found(self):
        with patch("main.db.update_purchase", return_value=None):
            response = client.patch("/api/purchases/does-not-exist", json={"platform": "x"})
        self.assertEqual(response.status_code, 404)


class DeletePurchaseEndpointTests(unittest.TestCase):
    def test_deletes_purchase(self):
        with patch("main.db.delete_purchase", return_value={"id": "p1"}) as mock_delete:
            response = client.delete("/api/purchases/p1")
        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with("p1")

    def test_returns_404_when_not_found(self):
        with patch("main.db.delete_purchase", return_value=None):
            response = client.delete("/api/purchases/does-not-exist")
        self.assertEqual(response.status_code, 404)


class AddPurchaseItemEndpointTests(unittest.TestCase):
    def test_links_card_to_purchase(self):
        item = {"id": "item-1", "purchase_id": "p1", "card_id": "card-1"}
        with patch("main.db.get_purchase", return_value={"id": "p1", "items": []}), \
             patch("main.db.get_card", return_value={"id": "card-1"}), \
             patch("main.db.add_purchase_item", return_value=item) as mock_add, \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "card-1", "title": "Karte 1", "front_image_path": None}]):
            response = client.post("/api/purchases/p1/items", json={"card_id": "card-1"})
        self.assertEqual(response.status_code, 200)
        mock_add.assert_called_once_with("p1", {"card_id": "card-1"})

    def test_returns_404_when_purchase_not_found(self):
        with patch("main.db.get_purchase", return_value=None):
            response = client.post("/api/purchases/does-not-exist/items", json={"card_id": "card-1"})
        self.assertEqual(response.status_code, 404)

    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_purchase", return_value={"id": "p1", "items": []}), \
             patch("main.db.get_card", return_value=None):
            response = client.post("/api/purchases/p1/items", json={"card_id": "does-not-exist"})
        self.assertEqual(response.status_code, 404)

    def test_returns_409_when_card_already_linked(self):
        with patch("main.db.get_purchase", return_value={"id": "p1", "items": []}), \
             patch("main.db.get_card", return_value={"id": "card-1"}), \
             patch("main.db.add_purchase_item", side_effect=db.CardAlreadyLinkedError("card-1")):
            response = client.post("/api/purchases/p1/items", json={"card_id": "card-1"})
        self.assertEqual(response.status_code, 409)


class UpdatePurchaseItemEndpointTests(unittest.TestCase):
    def test_updates_item(self):
        updated = {"id": "item-1", "card_id": "card-1", "notes": "LP statt NM"}
        with patch("main.db.update_purchase_item", return_value=updated) as mock_update, \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "card-1", "title": "Karte 1", "front_image_path": None}]):
            response = client.patch("/api/purchases/p1/items/item-1", json={"notes": "LP statt NM"})
        self.assertEqual(response.status_code, 200)
        mock_update.assert_called_once_with("p1", "item-1", {"notes": "LP statt NM"})

    def test_returns_404_when_not_found(self):
        with patch("main.db.update_purchase_item", return_value=None):
            response = client.patch("/api/purchases/p1/items/does-not-exist", json={"notes": "x"})
        self.assertEqual(response.status_code, 404)


class DeletePurchaseItemEndpointTests(unittest.TestCase):
    def test_unlinks_card(self):
        with patch("main.db.delete_purchase_item", return_value={"id": "item-1"}) as mock_delete:
            response = client.delete("/api/purchases/p1/items/item-1")
        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with("p1", "item-1")

    def test_returns_404_when_not_found(self):
        with patch("main.db.delete_purchase_item", return_value=None):
            response = client.delete("/api/purchases/p1/items/does-not-exist")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

An `tests/test_webapp_poc_cards_endpoints.py` anhängen (vor `if __name__ == "__main__":`):

```python
class GetCardPurchaseFieldTests(unittest.TestCase):
    def test_includes_purchase_info_when_linked(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        purchase_info = {"purchase_id": "p1", "item_id": "item-1", "platform": "eBay"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=purchase_info):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["purchase"], purchase_info)

    def test_purchase_is_null_when_not_linked(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None):
            response = client.get("/api/cards/card-1")
        self.assertIsNone(response.json()["purchase"])


class ListCardsHasPurchaseFieldTests(unittest.TestCase):
    def test_flags_cards_with_a_linked_purchase(self):
        rows = [
            {"id": "card-1", "front_image_path": None, "back_image_path": None},
            {"id": "card-2", "front_image_path": None, "back_image_path": None},
        ]
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.db.cards_with_purchase", return_value={"card-1"}):
            response = client.get("/api/cards")
        cards = response.json()["cards"]
        self.assertTrue(cards[0]["has_purchase"])
        self.assertFalse(cards[1]["has_purchase"])
```

- [ ] **Step 2: Tests laufen lassen, Fehlschlag verifizieren**

Run: `python3 -m unittest discover -s tests -v`
Expected: FAIL - `/api/purchases`-Routen existieren noch nicht (404/405), `has_purchase`/`purchase`-Felder fehlen noch in den Cards-Responses.

- [ ] **Step 3: Endpoints in `main.py` ergänzen**

Neue Helper direkt vor der bestehenden `_attach_signed_urls`-Funktion einfügen:

```python
def _expand_purchase_items(items):
    # Reichert jedes purchase_items-Row um eine schlanke Karten-Kurzinfo an
    # (id/title/front_image_url), damit purchase.html/card.html nicht pro
    # Karte einen eigenen Request an /api/cards/{id} schicken muessen.
    if not items:
        return []
    card_ids = [item["card_id"] for item in items]
    cards_by_id = {c["id"]: c for c in db.get_cards_by_ids(card_ids)}
    expanded = []
    for item in items:
        item = dict(item)
        card = cards_by_id.get(item["card_id"], {})
        card_summary = {"id": item["card_id"], "title": card.get("title", "")}
        front_path = card.get("front_image_path")
        if front_path:
            try:
                card_summary["front_image_url"] = storage.signed_url(front_path)
            except Exception:
                pass
        item["card"] = card_summary
        expanded.append(item)
    return expanded


def _attach_purchase_items(purchase):
    purchase = dict(purchase)
    purchase["items"] = _expand_purchase_items(purchase.get("items", []))
    return purchase
```

Ans Ende von `main.py` (nach dem bestehenden `DELETE /api/cards/{card_id}`, vor dem `static_dir`-Mount) anfügen:

```python
@app.post("/api/purchases")
async def create_purchase(fields: dict = Body(...)):
    fields = dict(fields)
    items = fields.pop("items", None)
    try:
        purchase = db.create_purchase(fields, items)
    except db.CardAlreadyLinkedError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Karte {exc.args[0]} ist bereits einem Kauf zugeordnet.",
        ) from exc
    return JSONResponse(_attach_purchase_items(purchase))


@app.get("/api/purchases")
async def list_purchases(q: str | None = None):
    return JSONResponse({"purchases": db.list_purchases(q=q)})


@app.get("/api/purchases/{purchase_id}")
async def get_purchase(purchase_id: str):
    purchase = db.get_purchase(purchase_id)
    if purchase is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    return JSONResponse(_attach_purchase_items(purchase))


@app.patch("/api/purchases/{purchase_id}")
async def update_purchase(purchase_id: str, fields: dict = Body(...)):
    updated = db.update_purchase(purchase_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    return JSONResponse(_attach_purchase_items(updated))


@app.delete("/api/purchases/{purchase_id}", status_code=204)
async def delete_purchase(purchase_id: str):
    deleted = db.delete_purchase(purchase_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    return Response(status_code=204)


@app.post("/api/purchases/{purchase_id}/items")
async def add_purchase_item(purchase_id: str, fields: dict = Body(...)):
    if db.get_purchase(purchase_id) is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    card_id = fields.get("card_id")
    if not card_id or db.get_card(card_id) is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    try:
        item = db.add_purchase_item(purchase_id, fields)
    except db.CardAlreadyLinkedError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Karte {exc.args[0]} ist bereits einem Kauf zugeordnet.",
        ) from exc
    return JSONResponse(_expand_purchase_items([item])[0])


@app.patch("/api/purchases/{purchase_id}/items/{item_id}")
async def update_purchase_item(purchase_id: str, item_id: str, fields: dict = Body(...)):
    updated = db.update_purchase_item(purchase_id, item_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Kauf-Position {item_id} nicht gefunden.")
    return JSONResponse(_expand_purchase_items([updated])[0])


@app.delete("/api/purchases/{purchase_id}/items/{item_id}", status_code=204)
async def delete_purchase_item(purchase_id: str, item_id: str):
    deleted = db.delete_purchase_item(purchase_id, item_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Kauf-Position {item_id} nicht gefunden.")
    return Response(status_code=204)
```

Die bestehenden `GET /api/cards`- und `GET /api/cards/{card_id}`-Routen erweitern (ersetzen):

```python
@app.get("/api/cards")
async def list_cards(q: str | None = None, status: str | None = None):
    cards = [_attach_signed_urls(c) for c in db.list_cards(q=q, status=status)]
    linked_ids = db.cards_with_purchase([c["id"] for c in cards])
    for c in cards:
        c["has_purchase"] = c["id"] in linked_ids
    return JSONResponse({"cards": cards})


@app.get("/api/cards/{card_id}")
async def get_card(card_id: str):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    card = _attach_signed_urls(card)
    card["purchase"] = db.get_purchase_for_card(card_id)
    return JSONResponse(card)
```

- [ ] **Step 4: Tests laufen lassen, Erfolg verifizieren**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (alle bestehenden + alle neuen Tests, keine Regression)

- [ ] **Step 5: Commit**

```bash
git add webapp-poc/main.py tests/test_webapp_poc_purchases_endpoints.py tests/test_webapp_poc_cards_endpoints.py
git commit -m "Add purchases CRUD + item-linking endpoints, extend cards endpoints with purchase info"
```

---

### Task 4: `webapp-poc/static/purchases.html` – Käufe-Liste

**Files:**
- Create: `webapp-poc/static/purchases.html`

**Interfaces:**
- Consumes: `GET /api/purchases?q=` (Task 3) – erwartet `{"purchases": [{"id", "purchase_date", "platform", "seller", "total_price", "shipping", "item_count"}]}`.
- Produces: Links zu `purchase.html?id=<id>`; legt beim Rendern `sessionStorage["purchaseListIds"]` an (Task 5 liest das für die Vor-/Zurück-Navigation).

Kein Backend-Code, daher kein TDD-Zyklus - Verifikation ist manuelles Testen im Browser (Schritt 2).

- [ ] **Step 1: Seite erstellen**

`webapp-poc/static/purchases.html`:

```html
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>DCardLabs – Käufe</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 900px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.3rem; }
  .controls { display: flex; gap: 0.75rem; margin-bottom: 1rem; flex-wrap: wrap; align-items: center; }
  input { padding: 0.4rem 0.6rem; font-size: 1rem; }
  table { width: 100%; border-collapse: collapse; margin-top: 1rem; }
  th, td { text-align: left; padding: 0.5rem; border-bottom: 1px solid #ddd; }
  tr.row { cursor: pointer; }
  tr.row:hover { background: #f7f7f7; }
  #status-msg { font-style: italic; color: #555; }
  #new-purchase-form { display: none; grid-template-columns: 1fr 1fr; gap: 0.75rem; border: 1px solid #ccc; border-radius: 6px; padding: 1rem; margin-bottom: 1rem; }
  #new-purchase-form label { display: flex; flex-direction: column; font-size: 0.85rem; font-weight: 600; }
  #new-purchase-form input { font-weight: normal; margin-top: 0.2rem; }
  #new-purchase-form .actions { grid-column: 1 / -1; }
  .error { color: #b00020; font-weight: 600; }
</style>
</head>
<body>
  <h1>DCardLabs – Käufe</h1>
  <p><a href="cards.html">&larr; Zu den Karten</a></p>

  <div class="controls">
    <input type="text" id="q" placeholder="Suche (Plattform, Verkäufer, Notizen)">
    <button type="button" id="toggle-new">+ Neuer Kauf</button>
  </div>

  <form id="new-purchase-form">
    <label>Kaufdatum <input type="date" name="purchase_date" required></label>
    <label>Plattform <input type="text" name="platform"></label>
    <label>Verkäufer <input type="text" name="seller"></label>
    <label>Versand <input type="number" step="0.01" name="shipping"></label>
    <label>Gesamtpreis <input type="number" step="0.01" name="total_price"></label>
    <label>Notizen <input type="text" name="notes"></label>
    <div class="actions">
      <button type="submit">Speichern</button>
      <span id="new-purchase-status"></span>
    </div>
  </form>

  <p id="status-msg"></p>
  <table id="purchases-table" style="display: none;">
    <thead>
      <tr><th>Datum</th><th>Plattform</th><th>Verkäufer</th><th>Karten</th><th>Gesamt</th><th>Versand</th></tr>
    </thead>
    <tbody id="purchases-body"></tbody>
  </table>

<script>
const qInput = document.getElementById("q");
const statusMsg = document.getElementById("status-msg");
const table = document.getElementById("purchases-table");
const body = document.getElementById("purchases-body");
const toggleNewBtn = document.getElementById("toggle-new");
const newForm = document.getElementById("new-purchase-form");

toggleNewBtn.addEventListener("click", () => {
  newForm.style.display = newForm.style.display === "grid" ? "none" : "grid";
});

async function loadPurchases() {
  const params = new URLSearchParams();
  if (qInput.value.trim()) params.set("q", qInput.value.trim());

  statusMsg.textContent = "Lädt …";
  table.style.display = "none";
  try {
    const res = await fetch("/api/purchases?" + params.toString());
    const data = await res.json();
    if (!res.ok) {
      statusMsg.textContent = "Fehler: " + (data.detail || res.statusText);
      return;
    }
    statusMsg.textContent = `${data.purchases.length} Kauf/Käufe`;
    render(data.purchases);
  } catch (err) {
    statusMsg.textContent = "Fehler: " + err;
  }
}

function render(purchases) {
  body.innerHTML = "";
  table.style.display = purchases.length ? "table" : "none";
  try {
    sessionStorage.setItem("purchaseListIds", JSON.stringify(purchases.map((p) => p.id)));
  } catch (err) {
    // sessionStorage kann in eingeschraenkten Browser-Kontexten werfen -
    // Vor-/Zurueck-Navigation ist dann einfach nicht verfuegbar.
  }
  for (const p of purchases) {
    const tr = document.createElement("tr");
    tr.className = "row";
    tr.addEventListener("click", () => {
      location.href = `purchase.html?id=${encodeURIComponent(p.id)}`;
    });
    const cells = [
      p.purchase_date || "", p.platform || "", p.seller || "",
      String(p.item_count ?? 0), p.total_price ?? "", p.shipping ?? "",
    ];
    for (const text of cells) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    body.appendChild(tr);
  }
}

newForm.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fields = Object.fromEntries(new FormData(newForm).entries());
  const statusEl = document.getElementById("new-purchase-status");
  statusEl.textContent = "Speichert …";
  statusEl.className = "";
  try {
    const res = await fetch("/api/purchases", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(fields),
    });
    const responseBody = await res.json();
    if (!res.ok) {
      statusEl.textContent = "Fehler: " + (responseBody.detail || res.statusText);
      statusEl.className = "error";
      return;
    }
    newForm.reset();
    newForm.style.display = "none";
    statusEl.textContent = "";
    loadPurchases();
  } catch (err) {
    statusEl.textContent = "Fehler: " + err;
    statusEl.className = "error";
  }
});

qInput.addEventListener("input", () => {
  clearTimeout(qInput._debounce);
  qInput._debounce = setTimeout(loadPurchases, 300);
});

loadPurchases();
</script>
</body>
</html>
```

- [ ] **Step 2: Manuell verifizieren**

Server starten (`uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir webapp-poc`, Env-Variablen gesetzt), `http://localhost:8000/purchases.html` öffnen. Erwartet: leere Liste initial (falls noch keine Käufe existieren). "+ Neuer Kauf" öffnet das Formular, Absenden legt einen Kauf an (in Supabase Table Editor prüfen) und die Liste aktualisiert sich. Suchfeld filtert (Netzwerk-Tab zeigt `GET /api/purchases?q=...`). Klick auf eine Zeile führt zu `purchase.html?id=<uuid>` (Seite existiert erst nach Task 5, 404 an dieser Stelle ist bis dahin erwartet).

- [ ] **Step 3: Commit**

```bash
git add webapp-poc/static/purchases.html
git commit -m "Add purchases.html: purchase list with search and quick-create"
```

---

### Task 5: `webapp-poc/static/purchase.html` – Detail/Bearbeiten inkl. Vor-/Zurück-Navigation

**Files:**
- Create: `webapp-poc/static/purchase.html`

**Interfaces:**
- Consumes: `GET`/`PATCH`/`DELETE /api/purchases/{id}`, `POST`/`DELETE /api/purchases/{id}/items[/{item_id}]`, `GET /api/cards?q=` (Task 3); liest `id` aus dem URL-Query-Parameter (von `purchases.html` verlinkt) sowie `sessionStorage["purchaseListIds"]` (von `purchases.html` befüllt) für die Vor-/Zurück-Navigation.

Kein Backend-Code, daher kein TDD-Zyklus - Verifikation ist manuelles Testen im Browser (Schritt 2).

- [ ] **Step 1: Seite erstellen**

`webapp-poc/static/purchase.html`:

```html
<!doctype html>
<html lang="de">
<head>
<meta charset="utf-8">
<title>DCardLabs – Kauf bearbeiten</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 700px; margin: 2rem auto; padding: 0 1rem; }
  h1 { font-size: 1.3rem; }
  #prev-next-nav { display: flex; gap: 0.75rem; margin-bottom: 1rem; }
  form { display: grid; grid-template-columns: 1fr 1fr; gap: 0.75rem; }
  label { display: flex; flex-direction: column; font-size: 0.85rem; font-weight: 600; }
  input { font-weight: normal; padding: 0.4rem; margin-top: 0.2rem; }
  .actions { grid-column: 1 / -1; display: flex; gap: 0.75rem; margin-top: 1rem; }
  button { padding: 0.6rem 1.2rem; font-size: 1rem; cursor: pointer; }
  #delete-btn { background: #b00020; color: white; border: none; border-radius: 4px; }
  .error { color: #b00020; font-weight: 600; }
  .items { margin-top: 2rem; }
  .item-row { display: flex; align-items: center; gap: 0.75rem; padding: 0.5rem 0; border-bottom: 1px solid #ddd; }
  .item-row img { width: 48px; height: 64px; object-fit: cover; border-radius: 4px; background: #f0f0f0; }
  .item-row .title { flex: 1; }
  #add-item { display: flex; gap: 0.5rem; margin-top: 1rem; flex-wrap: wrap; }
</style>
</head>
<body>
  <h1>Kauf bearbeiten</h1>
  <p><a href="purchases.html">&larr; Zur Liste</a></p>
  <p id="prev-next-nav"></p>

  <form id="edit-form">
    <p id="load-status">Lädt …</p>
  </form>

  <div class="items">
    <h2>Verknüpfte Karten</h2>
    <div id="items-list"></div>
    <div id="add-item">
      <input type="text" id="card-search" placeholder="Karte suchen (Titel, Team, Set, Kartennr.)">
      <select id="card-results"></select>
      <button type="button" id="link-card-btn">Verknüpfen</button>
      <span id="link-status"></span>
    </div>
  </div>

<script>
const FIELDS = [
  ["purchase_date", "Kaufdatum", "date"], ["platform", "Plattform", "text"],
  ["seller", "Verkäufer", "text"], ["shipping", "Versand", "number"],
  ["total_price", "Gesamtpreis", "number"], ["notes", "Notizen", "text"],
];

function showError(container, message) {
  container.innerHTML = "";
  const p = document.createElement("p");
  p.className = "error";
  p.textContent = message;
  container.appendChild(p);
}

const params = new URLSearchParams(location.search);
const purchaseId = params.get("id");
const form = document.getElementById("edit-form");

if (!purchaseId) {
  showError(form, "Keine Kauf-ID angegeben.");
} else {
  loadPurchase();
  renderPrevNextNav();
}

// Liest die von purchases.html in sessionStorage abgelegte ID-Liste der
// aktuell gefilterten/durchsuchten Kaeufe, um "vorherige/naechste" ohne
// eigenen Backend-Endpoint anzuzeigen - exakt das gleiche Muster wie
// card.html's renderPrevNextNav() mit cardListIds.
function renderPrevNextNav() {
  const nav = document.getElementById("prev-next-nav");
  nav.innerHTML = "";
  let ids = [];
  try {
    ids = JSON.parse(sessionStorage.getItem("purchaseListIds") || "[]");
  } catch (err) {
    ids = [];
  }
  const index = ids.indexOf(purchaseId);
  if (index === -1) return;

  if (index > 0) {
    const prevLink = document.createElement("a");
    prevLink.href = `purchase.html?id=${encodeURIComponent(ids[index - 1])}`;
    prevLink.textContent = "← Vorherige";
    nav.appendChild(prevLink);
  }
  if (index < ids.length - 1) {
    const nextLink = document.createElement("a");
    nextLink.href = `purchase.html?id=${encodeURIComponent(ids[index + 1])}`;
    nextLink.textContent = "Nächste →";
    nav.appendChild(nextLink);
  }
}

async function loadPurchase() {
  try {
    const res = await fetch(`/api/purchases/${encodeURIComponent(purchaseId)}`);
    const purchase = await res.json();
    if (!res.ok) {
      showError(form, "Fehler: " + (purchase.detail || res.statusText));
      return;
    }
    render(purchase);
  } catch (err) {
    showError(form, "Fehler: " + err);
  }
}

function render(purchase) {
  form.innerHTML = "";
  for (const [key, label, type] of FIELDS) {
    const wrapper = document.createElement("label");
    wrapper.textContent = label;
    const input = document.createElement("input");
    input.type = type;
    input.name = key;
    input.value = purchase[key] ?? "";
    if (key === "purchase_date") input.required = true;
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
  deleteBtn.textContent = "Kauf löschen";
  deleteBtn.addEventListener("click", onDeletePurchase);
  actions.appendChild(deleteBtn);
  form.appendChild(actions);
  const statusP = document.createElement("p");
  statusP.id = "save-status";
  form.appendChild(statusP);

  renderItems(purchase.items || []);
}

function renderItems(items) {
  const list = document.getElementById("items-list");
  list.innerHTML = "";
  if (!items.length) {
    list.innerHTML = "<p>Noch keine Karte verknüpft.</p>";
    return;
  }
  for (const item of items) {
    const row = document.createElement("div");
    row.className = "item-row";
    const img = document.createElement("img");
    if (item.card?.front_image_url) img.src = item.card.front_image_url;
    row.appendChild(img);
    const title = document.createElement("a");
    title.className = "title";
    title.href = `card.html?id=${encodeURIComponent(item.card_id)}`;
    title.textContent = item.card?.title || "(ohne Titel)";
    row.appendChild(title);
    const cost = document.createElement("span");
    cost.textContent = item.allocated_cost != null ? `${item.allocated_cost} €` : "";
    row.appendChild(cost);
    const removeBtn = document.createElement("button");
    removeBtn.type = "button";
    removeBtn.textContent = "Entfernen";
    removeBtn.addEventListener("click", () => onRemoveItem(item.id));
    row.appendChild(removeBtn);
    list.appendChild(row);
  }
}

form.addEventListener("submit", async (ev) => {
  ev.preventDefault();
  const fields = Object.fromEntries(new FormData(form).entries());
  const statusP = document.getElementById("save-status");
  statusP.textContent = "Speichert …";
  statusP.className = "";
  try {
    const res = await fetch(`/api/purchases/${encodeURIComponent(purchaseId)}`, {
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
    renderItems(body.items || []);
  } catch (err) {
    statusP.textContent = "Fehler: " + err;
    statusP.className = "error";
  }
});

async function onDeletePurchase() {
  if (!confirm("Diesen Kauf wirklich löschen? Verknüpfte Karten bleiben erhalten, nur die Zuordnung geht verloren.")) return;
  try {
    const res = await fetch(`/api/purchases/${encodeURIComponent(purchaseId)}`, { method: "DELETE" });
    if (!res.ok && res.status !== 204) {
      const body = await res.json();
      alert("Fehler beim Löschen: " + (body.detail || res.statusText));
      return;
    }
    location.href = "purchases.html";
  } catch (err) {
    alert("Fehler beim Löschen: " + err);
  }
}

async function onRemoveItem(itemId) {
  if (!confirm("Diese Karte aus dem Kauf entfernen?")) return;
  try {
    const res = await fetch(
      `/api/purchases/${encodeURIComponent(purchaseId)}/items/${encodeURIComponent(itemId)}`,
      { method: "DELETE" }
    );
    if (!res.ok && res.status !== 204) {
      const body = await res.json();
      alert("Fehler: " + (body.detail || res.statusText));
      return;
    }
    loadPurchase();
  } catch (err) {
    alert("Fehler: " + err);
  }
}

const cardSearch = document.getElementById("card-search");
const cardResults = document.getElementById("card-results");
let searchedCards = [];

async function searchCards() {
  const q = cardSearch.value.trim();
  cardResults.innerHTML = "";
  if (!q) return;
  try {
    const res = await fetch(`/api/cards?q=${encodeURIComponent(q)}`);
    const body = await res.json();
    searchedCards = (body.cards || []).filter((c) => !c.has_purchase);
    for (const c of searchedCards) {
      const option = document.createElement("option");
      option.value = c.id;
      option.textContent = c.title || "(ohne Titel)";
      cardResults.appendChild(option);
    }
  } catch (err) {
    // Suche schlaegt fehl -> Dropdown bleibt leer, kein Blocker fuer den Rest der Seite.
  }
}

cardSearch.addEventListener("input", () => {
  clearTimeout(cardSearch._debounce);
  cardSearch._debounce = setTimeout(searchCards, 300);
});

document.getElementById("link-card-btn").addEventListener("click", async () => {
  const cardId = cardResults.value;
  const linkStatus = document.getElementById("link-status");
  if (!cardId) {
    linkStatus.textContent = "Bitte erst eine Karte suchen und auswählen.";
    linkStatus.className = "error";
    return;
  }
  linkStatus.textContent = "Verknüpft …";
  linkStatus.className = "";
  try {
    const res = await fetch(`/api/purchases/${encodeURIComponent(purchaseId)}/items`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ card_id: cardId }),
    });
    const body = await res.json();
    if (!res.ok) {
      linkStatus.textContent = "Fehler: " + (body.detail || res.statusText);
      linkStatus.className = "error";
      return;
    }
    linkStatus.textContent = "";
    cardSearch.value = "";
    cardResults.innerHTML = "";
    loadPurchase();
  } catch (err) {
    linkStatus.textContent = "Fehler: " + err;
    linkStatus.className = "error";
  }
});
</script>
</body>
</html>
```

- [ ] **Step 2: Manuell verifizieren**

Server läuft weiter (aus Task 4). Über `purchases.html` auf einen Kauf klicken → `purchase.html?id=<uuid>` zeigt vorausgefüllte Felder + verknüpfte Karten (falls vorhanden). Ein Feld ändern, "Speichern" → Bestätigung, in Supabase prüfen. Über das Suchfeld eine noch nicht verknüpfte Karte suchen, auswählen, "Verknüpfen" → erscheint in der Liste; erneutes Verknüpfen derselben Karte über einen anderen Kauf → 409-Fehlermeldung. "Entfernen" bei einer verknüpften Karte → verschwindet aus der Liste, Karte selbst bleibt (in `cards.html` weiterhin sichtbar). Aus `purchases.html` kommend erscheinen "← Vorherige"/"Nächste →"-Links, die durch die zuvor geladene Liste blättern; direkter Aufruf von `purchase.html?id=...` ohne vorher `purchases.html` geladen zu haben zeigt keine Navigation. "Kauf löschen" → zurück zur Liste, Kauf weg, verknüpfte Karten bleiben in `cards.html` erhalten (nur `has_purchase` wird wieder `false`).

- [ ] **Step 3: Commit**

```bash
git add webapp-poc/static/purchase.html
git commit -m "Add purchase.html: purchase detail view with prev/next nav and item linking"
```

---

### Task 6: `webapp-poc/static/card.html` – neuer "Kauf"-Bereich

**Files:**
- Modify: `webapp-poc/static/card.html`

**Interfaces:**
- Consumes: das erweiterte `GET /api/cards/{id}` (`purchase`-Feld, Task 3), `POST /api/purchases` (Einzelkauf-Schnellpfad), `GET /api/purchases?q=` + `POST /api/purchases/{id}/items` (Zuordnung zu bestehendem Kauf), `DELETE /api/purchases/{purchase_id}/items/{item_id}` (Verknüpfung lösen).

Kein Backend-Code, daher kein TDD-Zyklus - Verifikation ist manuelles Testen im Browser (Schritt 2).

- [ ] **Step 1: "Kauf"-Bereich ergänzen**

In `webapp-poc/static/card.html`, im `<style>`-Block ergänzen:

```css
  .purchase-section { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid #ddd; }
  .purchase-section h2 { font-size: 1.1rem; }
  .purchase-info { display: grid; grid-template-columns: auto 1fr; gap: 0.3rem 0.75rem; margin-bottom: 0.75rem; }
  .purchase-info dt { font-weight: 600; }
  .purchase-info dd { margin: 0; }
  .purchase-forms { display: flex; flex-direction: column; gap: 1rem; }
  .purchase-forms > div { border: 1px solid #ccc; border-radius: 6px; padding: 0.75rem; }
  .purchase-forms input, .purchase-forms select { padding: 0.3rem; margin-right: 0.4rem; }
```

Nach dem schließenden `</form>` des bestehenden `edit-form` (vor dem `<script>`-Tag) einfügen:

```html
  <div class="purchase-section" id="purchase-section"></div>
```

Im `<script>`-Block: nach `render(card)` in `loadCard()` (also direkt nachdem `render(card)` aufgerufen wurde) `renderPurchaseSection(card.purchase)` ergänzen -- `loadCard()` sieht danach so aus:

```javascript
async function loadCard() {
  try {
    const res = await fetch(`/api/cards/${encodeURIComponent(cardId)}`);
    const card = await res.json();
    if (!res.ok) {
      showError(form, "Fehler: " + (card.detail || res.statusText));
      return;
    }
    render(card);
    renderPurchaseSection(card.purchase);
  } catch (err) {
    showError(form, "Fehler: " + err);
  }
}
```

Ans Ende des `<script>`-Blocks (nach der bestehenden `onDelete`-Funktion) anfügen:

```javascript
function renderPurchaseSection(purchase) {
  const section = document.getElementById("purchase-section");
  section.innerHTML = "<h2>Kauf</h2>";

  if (purchase) {
    const dl = document.createElement("dl");
    dl.className = "purchase-info";
    const rows = [
      ["Kaufdatum", purchase.purchase_date], ["Plattform", purchase.platform],
      ["Verkäufer", purchase.seller], ["Anteil-Preis", purchase.allocated_cost],
      ["Notizen", purchase.notes],
    ];
    for (const [label, value] of rows) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      dl.appendChild(dt);
      const dd = document.createElement("dd");
      dd.textContent = value || "";
      dl.appendChild(dd);
    }
    section.appendChild(dl);

    const link = document.createElement("a");
    link.href = `purchase.html?id=${encodeURIComponent(purchase.purchase_id)}`;
    link.textContent = "Zum Kauf";
    section.appendChild(link);
    section.appendChild(document.createTextNode(" · "));

    const unlinkBtn = document.createElement("button");
    unlinkBtn.type = "button";
    unlinkBtn.textContent = "Verknüpfung lösen";
    unlinkBtn.addEventListener("click", async () => {
      if (!confirm("Verknüpfung zu diesem Kauf lösen? Der Kauf selbst bleibt bestehen.")) return;
      try {
        const res = await fetch(
          `/api/purchases/${encodeURIComponent(purchase.purchase_id)}/items/${encodeURIComponent(purchase.item_id)}`,
          { method: "DELETE" }
        );
        if (!res.ok && res.status !== 204) {
          const body = await res.json();
          alert("Fehler: " + (body.detail || res.statusText));
          return;
        }
        loadCard();
      } catch (err) {
        alert("Fehler: " + err);
      }
    });
    section.appendChild(unlinkBtn);
    return;
  }

  const forms = document.createElement("div");
  forms.className = "purchase-forms";

  const quickBuy = document.createElement("div");
  quickBuy.innerHTML = `
    <strong>Neuer Kauf für diese Karte</strong><br>
    <input type="date" id="quick-purchase-date" required>
    <input type="text" id="quick-platform" placeholder="Plattform">
    <input type="text" id="quick-seller" placeholder="Verkäufer">
    <input type="number" step="0.01" id="quick-total-price" placeholder="Preis">
    <input type="number" step="0.01" id="quick-shipping" placeholder="Versand">
    <button type="button" id="quick-buy-btn">Speichern</button>
    <span id="quick-buy-status"></span>
  `;
  forms.appendChild(quickBuy);

  const existingBuy = document.createElement("div");
  existingBuy.innerHTML = `
    <strong>Zu bestehendem Kauf hinzufügen</strong><br>
    <input type="text" id="purchase-search" placeholder="Kauf suchen (Plattform, Verkäufer, Notizen)">
    <select id="purchase-results"></select>
    <button type="button" id="link-purchase-btn">Verknüpfen</button>
    <span id="link-purchase-status"></span>
  `;
  forms.appendChild(existingBuy);

  section.appendChild(forms);

  document.getElementById("quick-buy-btn").addEventListener("click", async () => {
    const statusEl = document.getElementById("quick-buy-status");
    const purchaseDate = document.getElementById("quick-purchase-date").value;
    if (!purchaseDate) {
      statusEl.textContent = "Kaufdatum fehlt.";
      statusEl.className = "error";
      return;
    }
    statusEl.textContent = "Speichert …";
    statusEl.className = "";
    try {
      const res = await fetch("/api/purchases", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          purchase_date: purchaseDate,
          platform: document.getElementById("quick-platform").value,
          seller: document.getElementById("quick-seller").value,
          total_price: document.getElementById("quick-total-price").value,
          shipping: document.getElementById("quick-shipping").value,
          items: [{ card_id: cardId }],
        }),
      });
      const body = await res.json();
      if (!res.ok) {
        statusEl.textContent = "Fehler: " + (body.detail || res.statusText);
        statusEl.className = "error";
        return;
      }
      loadCard();
    } catch (err) {
      statusEl.textContent = "Fehler: " + err;
      statusEl.className = "error";
    }
  });

  const purchaseSearch = document.getElementById("purchase-search");
  const purchaseResults = document.getElementById("purchase-results");
  purchaseSearch.addEventListener("input", () => {
    clearTimeout(purchaseSearch._debounce);
    purchaseSearch._debounce = setTimeout(async () => {
      const q = purchaseSearch.value.trim();
      purchaseResults.innerHTML = "";
      if (!q) return;
      try {
        const res = await fetch(`/api/purchases?q=${encodeURIComponent(q)}`);
        const body = await res.json();
        for (const p of body.purchases || []) {
          const option = document.createElement("option");
          option.value = p.id;
          option.textContent = `${p.purchase_date || ""} · ${p.platform || ""} · ${p.seller || ""}`;
          purchaseResults.appendChild(option);
        }
      } catch (err) {
        // Suche schlaegt fehl -> Dropdown bleibt leer.
      }
    }, 300);
  });

  document.getElementById("link-purchase-btn").addEventListener("click", async () => {
    const statusEl = document.getElementById("link-purchase-status");
    const selectedPurchaseId = purchaseResults.value;
    if (!selectedPurchaseId) {
      statusEl.textContent = "Bitte erst einen Kauf suchen und auswählen.";
      statusEl.className = "error";
      return;
    }
    statusEl.textContent = "Verknüpft …";
    statusEl.className = "";
    try {
      const res = await fetch(`/api/purchases/${encodeURIComponent(selectedPurchaseId)}/items`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ card_id: cardId }),
      });
      const body = await res.json();
      if (!res.ok) {
        statusEl.textContent = "Fehler: " + (body.detail || res.statusText);
        statusEl.className = "error";
        return;
      }
      loadCard();
    } catch (err) {
      statusEl.textContent = "Fehler: " + err;
      statusEl.className = "error";
    }
  });
}
```

- [ ] **Step 2: Manuell verifizieren**

Server läuft weiter. Eine unverknüpfte Karte in `card.html` öffnen → Bereich "Kauf" zeigt beide Schnell-Formulare. "Neuer Kauf für diese Karte" ausfüllen und speichern → Bereich zeigt danach die schreibgeschützte Kauf-Info + "Zum Kauf"-Link (führt zu `purchase.html?id=...`, dort erscheint die Karte in der Item-Liste) + "Verknüpfung lösen". Bei einer anderen unverknüpften Karte über "Zu bestehendem Kauf hinzufügen" nach dem gerade erstellten Kauf suchen und verknüpfen → erscheint ebenfalls im Kauf. "Verknüpfung lösen" bei einer der beiden Karten → Bereich zeigt wieder die Schnell-Formulare, Kauf selbst bleibt in `purchases.html` mit einer Karte weniger bestehen.

- [ ] **Step 3: Commit**

```bash
git add webapp-poc/static/card.html
git commit -m "Add purchase section to card.html: view/unlink or quick-create/link a purchase"
```

---

### Task 7: Navigation verlinken + README aktualisieren + finaler Regressionstest

**Files:**
- Modify: `webapp-poc/static/cards.html`
- Modify: `webapp-poc/static/index.html`
- Modify: `webapp-poc/README.md`

**Interfaces:** Keine neuen Code-Interfaces - reine Verlinkung/Doku.

- [ ] **Step 1: Link zu `purchases.html` ergänzen**

In `webapp-poc/static/cards.html`, direkt nach dem bestehenden `<p><a href="index.html">...</a></p>`-Absatz einfügen:

```html
  <p><a href="purchases.html">Zu den Käufen &rarr;</a></p>
```

In `webapp-poc/static/index.html`, direkt nach dem bestehenden Link zur Karten-Liste (`<p><a href="cards.html">Zur Karten-Liste &rarr;</a></p>`) einfügen:

```html
  <p><a href="purchases.html">Zu den Käufen &rarr;</a></p>
```

- [ ] **Step 2: `webapp-poc/README.md` aktualisieren**

Im Abschnitt "Was hier passiert" ergänzen (nach der bestehenden Beschreibung von `PATCH`/`DELETE /api/cards/{id}`):

```markdown
- Käufe (Einzelkauf oder Sammelkauf/Lot) lassen sich erfassen, durchsuchen,
  bearbeiten und löschen (`static/purchases.html`/`purchase.html`,
  `POST`/`GET`/`PATCH`/`DELETE /api/purchases[/{id}]`). Einzelne Karten
  lassen sich einem Kauf zuordnen bzw. die Zuordnung wieder lösen - sowohl
  über `purchase.html` als auch direkt im neuen "Kauf"-Bereich von
  `card.html` (`POST`/`DELETE /api/purchases/{id}/items[/{item_id}]`).
  Eine Karte gehört höchstens einem Kauf gleichzeitig.
```

Falls vorhanden, den Hinweis auf "Käufe/Purchases (Sub-Projekt 3)" unter "Was absichtlich fehlt" entfernen (nicht mehr zutreffend).

- [ ] **Step 3: Vollständigen Testlauf verifizieren**

Run: `python3 -m unittest discover -s tests -v`
Expected: PASS (alle Tests im Repo, keine Regression - dieser Task ändert nur HTML/Markdown, keinen Python-Code)

- [ ] **Step 4: Commit**

```bash
git add webapp-poc/static/cards.html webapp-poc/static/index.html webapp-poc/README.md
git commit -m "Link purchases pages from index/cards, update webapp-poc README for Sub-Projekt 3"
```

---

## Nach Abschluss

Sub-Projekt 3 ist fertig, wenn: Käufe (Einzel- und Sammelkauf) sich in
`purchases.html`/`purchase.html` anlegen, durchsuchen, bearbeiten und
löschen lassen (inkl. Vor-/Zurück-Navigation), Karten sich sowohl dort als
auch direkt in `card.html` einem Kauf zuordnen bzw. wieder lösen lassen,
und alle Tests grün sind. Vor dem finalen PR: manuell gegen die echte
Supabase-Instanz auf dem NAS verifizieren (analog zu Sub-Projekt 1/2/den
Rotate-/Navigations-Zusatz-PRs). Nächster Schritt danach: eigene Spec/Plan
für Sub-Projekt 4 (eBay-Integration).
