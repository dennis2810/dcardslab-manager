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
