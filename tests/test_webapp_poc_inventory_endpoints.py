"""Tests for /api/inventory[...] and POST /api/cards/{id}/inventory
(webapp-poc/main.py)."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

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


class ListInventoryEndpointTests(unittest.TestCase):
    def test_returns_inventory_with_card_summaries(self):
        rows = [{"id": "inv-1", "card_id": "card-1", "quantity": 2}]
        with patch("main.db.list_inventory", return_value=rows), \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "card-1", "title": "Karte 1", "front_image_path": None}]), \
             patch("main.db.ebay_info_by_card_id", return_value={}):
            response = client.get("/api/inventory")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["inventory"][0]["card"]["title"], "Karte 1")
        self.assertEqual(body["inventory"][0]["quantity"], 2)

    def test_attaches_the_linked_ebay_listings_sku(self):
        rows = [{"id": "inv-1", "card_id": "card-1", "quantity": 2}]
        with patch("main.db.list_inventory", return_value=rows), \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "card-1", "title": "Karte 1", "front_image_path": None}]), \
             patch("main.db.ebay_info_by_card_id", return_value={"card-1": {"status": "Entwurf", "sku": "webapp-000001"}}):
            response = client.get("/api/inventory")
        self.assertEqual(response.json()["inventory"][0]["sku"], "webapp-000001")

    def test_returns_empty_list_when_no_rows(self):
        with patch("main.db.list_inventory", return_value=[]):
            response = client.get("/api/inventory")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["inventory"], [])


class CreateInventoryItemEndpointTests(unittest.TestCase):
    def test_creates_item_for_existing_card(self):
        created = {"id": "inv-1", "card_id": "card-1", "quantity": 1}
        with patch("main.db.get_card", return_value={"id": "card-1"}), \
             patch("main.db.create_inventory_item", return_value=created) as mock_create, \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "card-1", "title": "Karte 1", "front_image_path": None}]), \
             patch("main.db.ebay_info_by_card_id", return_value={}):
            response = client.post("/api/cards/card-1/inventory", json={"quantity": 1})
        self.assertEqual(response.status_code, 200)
        mock_create.assert_called_once_with("card-1", {"quantity": 1})
        self.assertEqual(response.json()["card"]["title"], "Karte 1")

    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_card", return_value=None), \
             patch("main.db.create_inventory_item") as mock_create:
            response = client.post("/api/cards/does-not-exist/inventory", json={"quantity": 1})
        self.assertEqual(response.status_code, 404)
        mock_create.assert_not_called()


class UpdateInventoryItemEndpointTests(unittest.TestCase):
    def test_updates_and_returns_item(self):
        updated = {"id": "inv-1", "card_id": "card-1", "location": "Regal 2"}
        with patch("main.db.update_inventory_item", return_value=updated) as mock_update, \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "card-1", "title": "Karte 1", "front_image_path": None}]), \
             patch("main.db.ebay_info_by_card_id", return_value={}):
            response = client.patch("/api/inventory/inv-1", json={"location": "Regal 2"})
        self.assertEqual(response.status_code, 200)
        mock_update.assert_called_once_with("inv-1", {"location": "Regal 2"})

    def test_returns_404_when_not_found(self):
        with patch("main.db.update_inventory_item", return_value=None):
            response = client.patch("/api/inventory/does-not-exist", json={"location": "x"})
        self.assertEqual(response.status_code, 404)


class DeleteInventoryItemEndpointTests(unittest.TestCase):
    def test_deletes_item(self):
        with patch("main.db.delete_inventory_item", return_value={"id": "inv-1"}) as mock_delete:
            response = client.delete("/api/inventory/inv-1")
        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with("inv-1")

    def test_returns_404_when_not_found(self):
        with patch("main.db.delete_inventory_item", return_value=None):
            response = client.delete("/api/inventory/does-not-exist")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
