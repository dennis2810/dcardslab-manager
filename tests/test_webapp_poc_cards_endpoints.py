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

    def test_signed_url_failure_for_one_card_does_not_crash_whole_response(self):
        rows = [
            {"id": "card-1", "title": "Karte 1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"},
            {"id": "card-2", "title": "Karte 2", "front_image_path": "b1/2_front.jpg", "back_image_path": "b1/2_back.jpg"},
        ]

        def fake_signed_url(path, **_):
            if path == "b1/1_front.jpg":
                raise RuntimeError("Supabase Storage hiccup")
            return f"https://signed/{path}"

        with patch("main.db.list_cards", return_value=rows), \
             patch("main.storage.signed_url", side_effect=fake_signed_url):
            response = client.get("/api/cards")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["cards"]), 2)

        card_1 = next(c for c in body["cards"] if c["id"] == "card-1")
        card_2 = next(c for c in body["cards"] if c["id"] == "card-2")
        # The failing URL key is simply omitted - the card itself, and its
        # other (successful) URL, still come through.
        self.assertNotIn("front_image_url", card_1)
        self.assertEqual(card_1["back_image_url"], "https://signed/b1/1_back.jpg")
        # The other card is unaffected by card-1's signed_url failure.
        self.assertEqual(card_2["front_image_url"], "https://signed/b1/2_front.jpg")
        self.assertEqual(card_2["back_image_url"], "https://signed/b1/2_back.jpg")


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

    def test_signed_url_failure_degrades_gracefully_instead_of_500(self):
        row = {"id": "card-1", "title": "Karte 1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"}
        with patch("main.db.get_card", return_value=row), \
             patch("main.storage.signed_url", side_effect=RuntimeError("Supabase Storage hiccup")):
            response = client.get("/api/cards/card-1")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "card-1")
        self.assertNotIn("front_image_url", body)
        self.assertNotIn("back_image_url", body)


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


class RotateCardImageEndpointTests(unittest.TestCase):
    def test_rotates_front_image_and_returns_card_with_fresh_signed_urls(self):
        card = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.rotate_image") as mock_rotate, \
             patch("main.storage.signed_url", side_effect=lambda p, **_: f"https://signed/{p}"):
            response = client.post("/api/cards/card-1/rotate", json={"side": "front", "degrees": 90})

        self.assertEqual(response.status_code, 200)
        mock_rotate.assert_called_once_with("b1/1_front.jpg", 90)
        body = response.json()
        self.assertEqual(body["front_image_url"], "https://signed/b1/1_front.jpg")
        self.assertEqual(body["back_image_url"], "https://signed/b1/1_back.jpg")

    def test_rotates_back_image(self):
        card = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.rotate_image") as mock_rotate, \
             patch("main.storage.signed_url", return_value="https://signed/x"):
            response = client.post("/api/cards/card-1/rotate", json={"side": "back", "degrees": 180})

        self.assertEqual(response.status_code, 200)
        mock_rotate.assert_called_once_with("b1/1_back.jpg", 180)

    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_card", return_value=None):
            response = client.post("/api/cards/does-not-exist/rotate", json={"side": "front", "degrees": 90})
        self.assertEqual(response.status_code, 404)

    def test_returns_404_when_requested_side_has_no_image(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": "b1/1_back.jpg"}
        with patch("main.db.get_card", return_value=card):
            response = client.post("/api/cards/card-1/rotate", json={"side": "front", "degrees": 90})
        self.assertEqual(response.status_code, 404)

    def test_rejects_invalid_side(self):
        card = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": None}
        with patch("main.db.get_card", return_value=card):
            response = client.post("/api/cards/card-1/rotate", json={"side": "sideways", "degrees": 90})
        self.assertEqual(response.status_code, 400)

    def test_rejects_invalid_degrees(self):
        card = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": None}
        with patch("main.db.get_card", return_value=card):
            response = client.post("/api/cards/card-1/rotate", json={"side": "front", "degrees": 45})
        self.assertEqual(response.status_code, 400)


if __name__ == "__main__":
    unittest.main()
