"""Tests for /api/wishlist[...] (webapp-poc/main.py)."""
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


class ListWishlistEndpointTests(unittest.TestCase):
    def test_returns_items(self):
        items = [{"id": "w1", "title": "Karte A"}]
        with patch("main.db.list_wishlist_items", return_value=items) as mock_list:
            response = client.get("/api/wishlist")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["items"], items)
        mock_list.assert_called_once_with(q=None)

    def test_passes_search_query_through(self):
        with patch("main.db.list_wishlist_items", return_value=[]) as mock_list:
            response = client.get("/api/wishlist?q=Bayern")
        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(q="Bayern")


class CreateWishlistEndpointTests(unittest.TestCase):
    def test_creates_item(self):
        created = {"id": "w1", "title": "Karte A"}
        with patch("main.db.create_wishlist_item", return_value=created) as mock_create:
            response = client.post("/api/wishlist", json={"title": "Karte A", "target_price": 9.99})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created)
        mock_create.assert_called_once_with({"title": "Karte A", "target_price": 9.99})


class UpdateWishlistEndpointTests(unittest.TestCase):
    def test_updates_item(self):
        updated = {"id": "w1", "title": "Karte B"}
        with patch("main.db.update_wishlist_item", return_value=updated) as mock_update:
            response = client.patch("/api/wishlist/w1", json={"title": "Karte B"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), updated)
        mock_update.assert_called_once_with("w1", {"title": "Karte B"})

    def test_returns_404_when_not_found(self):
        with patch("main.db.update_wishlist_item", return_value=None):
            response = client.patch("/api/wishlist/does-not-exist", json={"title": "x"})
        self.assertEqual(response.status_code, 404)


class DeleteWishlistEndpointTests(unittest.TestCase):
    def test_deletes_item(self):
        with patch("main.db.delete_wishlist_item", return_value={"id": "w1"}) as mock_delete:
            response = client.delete("/api/wishlist/w1")
        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with("w1")

    def test_returns_404_when_not_found(self):
        with patch("main.db.delete_wishlist_item", return_value=None):
            response = client.delete("/api/wishlist/does-not-exist")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
