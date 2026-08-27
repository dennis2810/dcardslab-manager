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
