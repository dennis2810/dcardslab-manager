"""Tests for GET /api/cards and GET /api/cards/{id} (webapp-poc/main.py)."""
import json
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
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}), \
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
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}), \
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
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]), \
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
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]), \
             patch("main.storage.signed_url", side_effect=RuntimeError("Supabase Storage hiccup")):
            response = client.get("/api/cards/card-1")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "card-1")
        self.assertNotIn("front_image_url", body)
        self.assertNotIn("back_image_url", body)


class ListCardsFilterEndpointTests(unittest.TestCase):
    def test_passes_query_params_to_db(self):
        with patch("main.db.list_cards", return_value=[]) as mock_list, \
             patch("main.db.list_cards_by_sku", return_value=[]), \
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}):
            response = client.get("/api/cards?q=Bayern&status=pr%C3%BCfen")
        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(q="Bayern", status="prüfen")

    def test_merges_sku_matched_cards_not_already_found_by_q(self):
        title_match = {"id": "card-1", "title": "Bayern-Karte", "front_image_path": None, "back_image_path": None}
        sku_match = {"id": "card-2", "title": "Andere Karte", "front_image_path": None, "back_image_path": None}
        with patch("main.db.list_cards", return_value=[title_match]), \
             patch("main.db.list_cards_by_sku", return_value=[sku_match]) as mock_sku, \
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}):
            response = client.get("/api/cards?q=webapp-000002")
        self.assertEqual(response.status_code, 200)
        ids = [c["id"] for c in response.json()["cards"]]
        self.assertEqual(ids, ["card-1", "card-2"])
        mock_sku.assert_called_once_with("webapp-000002")

    def test_does_not_duplicate_a_card_found_by_both_q_and_sku(self):
        card = {"id": "card-1", "title": "Karte 1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.list_cards", return_value=[card]), \
             patch("main.db.list_cards_by_sku", return_value=[card]), \
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}):
            response = client.get("/api/cards?q=Karte")
        ids = [c["id"] for c in response.json()["cards"]]
        self.assertEqual(ids, ["card-1"])


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


class SetCardPrivateCollectionEndpointTests(unittest.TestCase):
    def test_post_marks_card_private_and_returns_it_with_signed_urls(self):
        updated = {
            "id": "card-1", "title": "Karte 1", "private_collection": True,
            "front_image_path": "b1/1_front.jpg", "back_image_path": None,
        }
        with patch("main.db.set_card_private_collection", return_value=updated) as mock_set, \
             patch("main.storage.signed_url", return_value="https://signed/b1/1_front.jpg"):
            response = client.post("/api/cards/card-1/private-collection")
        self.assertEqual(response.status_code, 200)
        mock_set.assert_called_once_with("card-1", True)
        body = response.json()
        self.assertTrue(body["private_collection"])
        self.assertEqual(body["front_image_url"], "https://signed/b1/1_front.jpg")

    def test_post_returns_404_when_not_found(self):
        with patch("main.db.set_card_private_collection", return_value=None):
            response = client.post("/api/cards/does-not-exist/private-collection")
        self.assertEqual(response.status_code, 404)

    def test_delete_unmarks_card_private(self):
        updated = {"id": "card-1", "title": "Karte 1", "private_collection": False}
        with patch("main.db.set_card_private_collection", return_value=updated) as mock_set:
            response = client.delete("/api/cards/card-1/private-collection")
        self.assertEqual(response.status_code, 200)
        mock_set.assert_called_once_with("card-1", False)
        self.assertFalse(response.json()["private_collection"])

    def test_delete_returns_404_when_not_found(self):
        with patch("main.db.set_card_private_collection", return_value=None):
            response = client.delete("/api/cards/does-not-exist/private-collection")
        self.assertEqual(response.status_code, 404)


class ListPrivateCollectionEndpointTests(unittest.TestCase):
    def test_returns_private_cards_with_signed_urls(self):
        rows = [
            {"id": "card-1", "title": "Karte 1", "private_collection": True, "front_image_path": "b1/1_front.jpg"},
        ]
        with patch("main.db.list_private_collection_cards", return_value=rows) as mock_list, \
             patch("main.storage.signed_url", return_value="https://signed/b1/1_front.jpg"):
            response = client.get("/api/private-collection")
        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(q=None)
        body = response.json()
        self.assertEqual(body["cards"][0]["front_image_url"], "https://signed/b1/1_front.jpg")

    def test_passes_search_query_through(self):
        with patch("main.db.list_private_collection_cards", return_value=[]) as mock_list:
            response = client.get("/api/private-collection?q=Bayern")
        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(q="Bayern")


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


class MergeDuplicateCardEndpointTests(unittest.TestCase):
    def test_adds_duplicate_quantity_to_existing_inventory_item_and_deletes_duplicate(self):
        card = {"id": "card-2", "front_image_path": None, "back_image_path": None}
        target = {"id": "card-1", "title": "Original"}
        with patch("main.db.get_card", side_effect=[card, target, target]) as mock_get, \
             patch("main.db.get_inventory_for_card", side_effect=[
                 [{"id": "inv-2", "quantity": 1}], [{"id": "inv-1", "quantity": 2}],
             ]), \
             patch("main.db.update_inventory_item") as mock_update, \
             patch("main.db.create_inventory_item") as mock_create, \
             patch("main.db.delete_card", return_value=card) as mock_delete:
            response = client.post("/api/cards/card-2/merge-into/card-1")
        self.assertEqual(response.status_code, 200)
        mock_update.assert_called_once_with("inv-1", {"quantity": 3})
        mock_create.assert_not_called()
        mock_delete.assert_called_once_with("card-2")
        self.assertEqual(response.json()["id"], "card-1")

    def test_creates_inventory_item_when_target_has_none(self):
        card = {"id": "card-2", "front_image_path": None, "back_image_path": None}
        target = {"id": "card-1"}
        with patch("main.db.get_card", side_effect=[card, target, target]), \
             patch("main.db.get_inventory_for_card", side_effect=[[{"id": "inv-2", "quantity": 1}], []]), \
             patch("main.db.update_inventory_item") as mock_update, \
             patch("main.db.create_inventory_item") as mock_create, \
             patch("main.db.delete_card", return_value=card):
            response = client.post("/api/cards/card-2/merge-into/card-1")
        self.assertEqual(response.status_code, 200)
        mock_update.assert_not_called()
        mock_create.assert_called_once_with("card-1", {"quantity": 1})

    def test_rejects_merging_a_card_with_itself(self):
        response = client.post("/api/cards/card-1/merge-into/card-1")
        self.assertEqual(response.status_code, 400)

    def test_returns_404_when_duplicate_not_found(self):
        with patch("main.db.get_card", return_value=None):
            response = client.post("/api/cards/does-not-exist/merge-into/card-1")
        self.assertEqual(response.status_code, 404)

    def test_returns_404_when_target_not_found(self):
        card = {"id": "card-2"}
        with patch("main.db.get_card", side_effect=[card, None]):
            response = client.post("/api/cards/card-2/merge-into/does-not-exist")
        self.assertEqual(response.status_code, 404)


class RotateCardImageEndpointTests(unittest.TestCase):
    def test_rotates_front_image_and_returns_card_with_fresh_signed_urls(self):
        card = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.rotate_image", return_value=b"\xff\xd8\xff") as mock_rotate, \
             patch("main.storage.signed_url", side_effect=lambda p, **_: f"https://signed/{p}"):
            response = client.post("/api/cards/card-1/rotate", json={"side": "front", "degrees": 90})

        self.assertEqual(response.status_code, 200)
        mock_rotate.assert_called_once_with("b1/1_front.jpg", 90)
        body = response.json()
        self.assertEqual(body["front_image_url"], "https://signed/b1/1_front.jpg")
        self.assertEqual(body["back_image_url"], "https://signed/b1/1_back.jpg")
        # The rotated bytes come back as a data URI - no dependency on the
        # (possibly still Storage-CDN-stale) signed URL for the just-rotated
        # side, see main.py's rotate endpoint comment.
        self.assertTrue(body["rotated_image_data_uri"].startswith("data:image/jpeg;base64,"))

    def test_rotates_back_image(self):
        card = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.rotate_image", return_value=b"\xff\xd8\xff") as mock_rotate, \
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

    def test_returns_rotated_data_uri_even_when_signed_url_fails(self):
        # storage.rotate_image() already persisted the rotation and its
        # return value is already in memory - the response's data URI must
        # not depend on signed_url() (a separate, potentially-CDN-stale or
        # even failing network call) succeeding.
        card = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.rotate_image", return_value=b"\xff\xd8\xff"), \
             patch("main.storage.signed_url", side_effect=Exception("boom")):
            response = client.post("/api/cards/card-1/rotate", json={"side": "front", "degrees": 90})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["rotated_image_data_uri"].startswith("data:image/jpeg;base64,"))
        self.assertNotIn("front_image_url", body)


class ReplaceCardImageEndpointTests(unittest.TestCase):
    def _post_replace(self, card_id="card-1", side="front", filename="new-front.jpg"):
        return client.post(
            f"/api/cards/{card_id}/image",
            data={"side": side},
            files={"file": (filename, b"fake-image-bytes", "image/jpeg")},
        )

    def test_uploads_to_the_cards_existing_batch_and_position(self):
        card = {"id": "card-1", "batch_id": "batch-1", "position_in_batch": 3, "front_image_path": "batch-1/3_front.jpg"}
        updated = {"id": "card-1", "front_image_path": "batch-1/3_front.jpg", "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.compress_image", return_value=b"\xff\xd8\xff"), \
             patch("main.storage.upload_image", return_value="batch-1/3_front.jpg") as mock_upload, \
             patch("main.db.set_card_image_path", return_value=updated) as mock_set, \
             patch("main.storage.signed_url", return_value="https://signed/batch-1/3_front.jpg"):
            response = self._post_replace()
        self.assertEqual(response.status_code, 200)
        mock_upload.assert_called_once()
        args = mock_upload.call_args.args
        self.assertEqual(args[0], "batch-1")
        self.assertEqual(args[1], 3)
        self.assertEqual(args[2], "front")
        mock_set.assert_called_once_with("card-1", "front", "batch-1/3_front.jpg")
        body = response.json()
        self.assertEqual(body["front_image_url"], "https://signed/batch-1/3_front.jpg")
        self.assertTrue(body["replaced_image_data_uri"].startswith("data:image/jpeg;base64,"))

    def test_replaces_back_side(self):
        card = {"id": "card-1", "batch_id": "batch-1", "position_in_batch": 1, "back_image_path": "batch-1/1_back.jpg"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.compress_image", return_value=b"\xff\xd8\xff"), \
             patch("main.storage.upload_image", return_value="batch-1/1_back.jpg") as mock_upload, \
             patch("main.db.set_card_image_path", return_value=card), \
             patch("main.storage.signed_url", return_value="https://signed/x"):
            response = self._post_replace(side="back", filename="new-back.jpg")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mock_upload.call_args.args[2], "back")

    def test_rejects_invalid_side(self):
        response = self._post_replace(side="sideways")
        self.assertEqual(response.status_code, 400)

    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_card", return_value=None):
            response = self._post_replace(card_id="does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_upload_failure_returns_502(self):
        card = {"id": "card-1", "batch_id": "batch-1", "position_in_batch": 1}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.compress_image", return_value=b"\xff\xd8\xff"), \
             patch("main.storage.upload_image", side_effect=RuntimeError("bucket down")), \
             patch("main.db.set_card_image_path") as mock_set:
            response = self._post_replace()
        self.assertEqual(response.status_code, 502)
        mock_set.assert_not_called()


class AddCardExtraImageEndpointTests(unittest.TestCase):
    def _post_extra(self, card_id="card-1", filename="extra.jpg"):
        return client.post(
            f"/api/cards/{card_id}/images",
            files={"file": (filename, b"fake-image-bytes", "image/jpeg")},
        )

    def test_uploads_and_appends_to_extra_image_paths(self):
        card = {"id": "card-1", "batch_id": "batch-1", "position_in_batch": 3, "extra_image_paths": ""}
        updated = {"id": "card-1", "extra_image_paths": "batch-1/3_extra_abc123.jpg"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.upload_extra_image", return_value="batch-1/3_extra_abc123.jpg") as mock_upload, \
             patch("main.db.add_card_extra_image", return_value=updated) as mock_add, \
             patch("main.storage.signed_url", return_value="https://signed/extra"):
            response = self._post_extra()
        self.assertEqual(response.status_code, 200)
        mock_upload.assert_called_once()
        args = mock_upload.call_args.args
        self.assertEqual(args[0], "batch-1")
        self.assertEqual(args[1], 3)
        mock_add.assert_called_once_with("card-1", "batch-1/3_extra_abc123.jpg")
        body = response.json()
        self.assertEqual(body["extra_image_urls"], ["https://signed/extra"])

    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_card", return_value=None):
            response = self._post_extra(card_id="does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_upload_failure_returns_502(self):
        card = {"id": "card-1", "batch_id": "batch-1", "position_in_batch": 1, "extra_image_paths": ""}
        with patch("main.db.get_card", return_value=card), \
             patch("main.storage.upload_extra_image", side_effect=RuntimeError("bucket down")), \
             patch("main.db.add_card_extra_image") as mock_add:
            response = self._post_extra()
        self.assertEqual(response.status_code, 502)
        mock_add.assert_not_called()


class DeleteCardExtraImageEndpointTests(unittest.TestCase):
    def test_deletes_from_db_and_storage(self):
        updated = {"id": "card-1", "extra_image_paths": ""}
        with patch("main.db.remove_card_extra_image", return_value=(updated, "batch-1/3_extra_abc.jpg")) as mock_remove, \
             patch("main.storage.delete_images") as mock_delete:
            response = client.delete("/api/cards/card-1/images/0")
        self.assertEqual(response.status_code, 204)
        mock_remove.assert_called_once_with("card-1", 0)
        mock_delete.assert_called_once_with(["batch-1/3_extra_abc.jpg"])

    def test_returns_404_when_not_found(self):
        with patch("main.db.remove_card_extra_image", return_value=(None, None)), \
             patch("main.storage.delete_images") as mock_delete:
            response = client.delete("/api/cards/card-1/images/5")
        self.assertEqual(response.status_code, 404)
        mock_delete.assert_not_called()

    def test_storage_deletion_failure_does_not_break_the_delete(self):
        updated = {"id": "card-1", "extra_image_paths": ""}
        with patch("main.db.remove_card_extra_image", return_value=(updated, "batch-1/3_extra_abc.jpg")), \
             patch("main.storage.delete_images", side_effect=RuntimeError("bucket down")):
            response = client.delete("/api/cards/card-1/images/0")
        self.assertEqual(response.status_code, 204)


class GetCardPurchaseFieldTests(unittest.TestCase):
    def test_includes_purchase_info_when_linked(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        purchase_info = {"purchase_id": "p1", "item_id": "item-1", "platform": "eBay"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=purchase_info), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["purchase"], purchase_info)

    def test_purchase_is_null_when_not_linked(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertIsNone(response.json()["purchase"])


class GetCardEbayListingFieldTests(unittest.TestCase):
    def test_includes_ebay_listing_when_linked(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        listing = {"id": "listing-1", "card_id": "card-1", "status": "Entwurf"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=listing), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["ebay_listing"], listing)

    def test_ebay_listing_is_null_when_none_exists(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertIsNone(response.json()["ebay_listing"])


class GetCardInventoryFieldTests(unittest.TestCase):
    def test_includes_inventory_items_for_the_card(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        items = [{"id": "inv-1", "card_id": "card-1", "quantity": 2}]
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=items), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["inventory"], items)

    def test_inventory_is_empty_list_when_none_exists(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["inventory"], [])


class GetCardEbaySaleFieldTests(unittest.TestCase):
    def test_includes_sale_info_when_sold(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        sale = {"sale_date": "2026-08-01T00:00:00+00:00", "gross_price": 12.5}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=sale), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["ebay_sale"], sale)

    def test_ebay_sale_is_null_when_none_exists(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertIsNone(response.json()["ebay_sale"])


class GetCardManualSaleFieldTests(unittest.TestCase):
    def test_includes_manual_sale_when_present(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        manual_sale = {"id": "ms-1", "card_id": "card-1", "channel": "Kleinanzeigen", "gross_price": 8.0}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=manual_sale), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["manual_sale"], manual_sale)

    def test_manual_sale_is_null_when_none_exists(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertIsNone(response.json()["manual_sale"])


class CreateManualSaleEndpointTests(unittest.TestCase):
    def test_creates_manual_sale_and_zeroes_inventory(self):
        created = {"id": "ms-1", "card_id": "card-1", "channel": "Kleinanzeigen", "gross_price": 8.0}
        with patch("main.db.get_card", return_value={"id": "card-1"}), \
             patch("main.db.create_manual_sale", return_value=created) as mock_create, \
             patch("main.db.zero_inventory_for_card") as mock_zero:
            response = client.post(
                "/api/cards/card-1/manual-sale",
                json={"channel": "Kleinanzeigen", "gross_price": 8.0},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), created)
        mock_create.assert_called_once_with("card-1", {"channel": "Kleinanzeigen", "gross_price": 8.0})
        mock_zero.assert_called_once_with("card-1")

    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_card", return_value=None):
            response = client.post("/api/cards/does-not-exist/manual-sale", json={"channel": "Vinted"})
        self.assertEqual(response.status_code, 404)

    def test_inventory_zeroing_failure_does_not_break_the_create(self):
        # Gleiches Isolationsprinzip wie bei sync_ebay_sales()/
        # _create_default_inventory_item() - der Verkauf selbst ist schon
        # angelegt, ein Supabase-Hiccup bei der Inventar-Nullung darf das
        # nicht mehr zu einem 500 machen.
        created = {"id": "ms-1", "card_id": "card-1"}
        with patch("main.db.get_card", return_value={"id": "card-1"}), \
             patch("main.db.create_manual_sale", return_value=created), \
             patch("main.db.zero_inventory_for_card", side_effect=RuntimeError("inventory table down")):
            response = client.post("/api/cards/card-1/manual-sale", json={"channel": "Vinted"})
        self.assertEqual(response.status_code, 200)


class ListManualSalesEndpointTests(unittest.TestCase):
    def test_returns_all_manual_sales(self):
        rows = [{"id": "ms-1", "card_id": "card-1"}, {"id": "ms-2", "card_id": "card-2"}]
        with patch("main.db.all_manual_sales", return_value=rows):
            response = client.get("/api/manual-sales")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["manual_sales"], rows)


class UpdateManualSaleEndpointTests(unittest.TestCase):
    def test_updates_manual_sale(self):
        with patch("main.db.update_manual_sale", return_value={"id": "ms-1", "gross_price": 9.0}) as mock_update:
            response = client.patch("/api/manual-sales/ms-1", json={"gross_price": 9.0})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["gross_price"], 9.0)
        mock_update.assert_called_once_with("ms-1", {"gross_price": 9.0})

    def test_returns_404_when_not_found(self):
        with patch("main.db.update_manual_sale", return_value=None):
            response = client.patch("/api/manual-sales/does-not-exist", json={"gross_price": 1})
        self.assertEqual(response.status_code, 404)


class DeleteManualSaleEndpointTests(unittest.TestCase):
    def test_deletes_manual_sale_and_restores_inventory(self):
        with patch("main.db.delete_manual_sale", return_value={"id": "ms-1", "card_id": "card-1"}), \
             patch("main.db.restore_inventory_for_card") as mock_restore:
            response = client.delete("/api/manual-sales/ms-1")
        self.assertEqual(response.status_code, 204)
        mock_restore.assert_called_once_with("card-1")

    def test_returns_404_when_not_found(self):
        with patch("main.db.delete_manual_sale", return_value=None):
            response = client.delete("/api/manual-sales/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_inventory_restoration_failure_does_not_break_the_delete(self):
        # Gleiches Isolationsprinzip wie beim Anlegen (create_manual_sale) -
        # der Verkauf ist schon geloescht, ein Supabase-Hiccup bei der
        # Inventar-Wiederherstellung darf das nicht zu einem 500 machen.
        with patch("main.db.delete_manual_sale", return_value={"id": "ms-1", "card_id": "card-1"}), \
             patch("main.db.restore_inventory_for_card", side_effect=RuntimeError("inventory table down")):
            response = client.delete("/api/manual-sales/ms-1")
        self.assertEqual(response.status_code, 204)


class GetCardPriceResearchFieldTests(unittest.TestCase):
    def test_includes_price_research_entries_for_the_card(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        entries = [{"id": "pr-1", "card_id": "card-1", "price": 12.5, "checked_at": "2026-09-01"}]
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=entries):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["price_research"], entries)

    def test_price_research_is_empty_list_when_none_exists(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["price_research"], [])

    def test_falls_back_to_empty_list_when_the_table_is_missing(self):
        # z.B. wenn die price_research-Migration in dieser Supabase-Instanz
        # noch nicht eingespielt wurde - die Kartenseite darf dadurch nicht
        # mit einem 500 komplett blockiert werden.
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]), \
             patch("main.db.get_sale_for_card", return_value=None), \
             patch("main.db.get_manual_sale_for_card", return_value=None), \
             patch("main.db.list_price_research_for_card", side_effect=RuntimeError('relation "price_research" does not exist')):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["price_research"], [])


class CreatePriceResearchEntryEndpointTests(unittest.TestCase):
    def test_creates_entry_for_an_existing_card(self):
        entry = {"id": "pr-1", "card_id": "card-1", "price": 12.5, "checked_at": "2026-09-01"}
        with patch("main.db.get_card", return_value={"id": "card-1"}), \
             patch("main.db.create_price_research_entry", return_value=entry) as mock_create:
            response = client.post("/api/cards/card-1/price-research", json={"price": 12.5, "checked_at": "2026-09-01"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), entry)
        mock_create.assert_called_once_with("card-1", {"price": 12.5, "checked_at": "2026-09-01"})

    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_card", return_value=None):
            response = client.post("/api/cards/does-not-exist/price-research", json={"price": 1})
        self.assertEqual(response.status_code, 404)


class DeletePriceResearchEntryEndpointTests(unittest.TestCase):
    def test_deletes_entry(self):
        with patch("main.db.delete_price_research_entry", return_value={"id": "pr-1"}):
            response = client.delete("/api/price-research/pr-1")
        self.assertEqual(response.status_code, 204)

    def test_returns_404_when_not_found(self):
        with patch("main.db.delete_price_research_entry", return_value=None):
            response = client.delete("/api/price-research/does-not-exist")
        self.assertEqual(response.status_code, 404)


class ListCardsHasPurchaseFieldTests(unittest.TestCase):
    def test_flags_cards_with_a_linked_purchase(self):
        rows = [
            {"id": "card-1", "front_image_path": None, "back_image_path": None},
            {"id": "card-2", "front_image_path": None, "back_image_path": None},
        ]
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.db.cards_with_purchase", return_value={"card-1"}), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}):
            response = client.get("/api/cards")
        cards = response.json()["cards"]
        self.assertTrue(cards[0]["has_purchase"])
        self.assertFalse(cards[1]["has_purchase"])


class ListCardsEbayStatusFieldTests(unittest.TestCase):
    def test_attaches_ebay_status_and_sku_when_a_listing_exists(self):
        rows = [
            {"id": "card-1", "front_image_path": None, "back_image_path": None},
            {"id": "card-2", "front_image_path": None, "back_image_path": None},
        ]
        info = {"card-1": {"status": "Veroeffentlicht", "sku": "webapp-000001"}}
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value=info), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}):
            response = client.get("/api/cards")
        cards = response.json()["cards"]
        self.assertEqual(cards[0]["ebay_status"], "Veroeffentlicht")
        self.assertEqual(cards[0]["ebay_sku"], "webapp-000001")
        self.assertIsNone(cards[1]["ebay_status"])
        self.assertIsNone(cards[1]["ebay_sku"])

    def test_flags_imported_listing_without_offer_id(self):
        rows = [
            {"id": "card-1", "front_image_path": None, "back_image_path": None},
            {"id": "card-2", "front_image_path": None, "back_image_path": None},
        ]
        info = {
            "card-1": {"status": "Veroeffentlicht", "sku": "", "ebay_offer_id": ""},
            "card-2": {"status": "Veroeffentlicht", "sku": "webapp-000002", "ebay_offer_id": "offer-1"},
        }
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value=info), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}):
            response = client.get("/api/cards")
        cards = response.json()["cards"]
        self.assertTrue(cards[0]["ebay_imported"])
        self.assertFalse(cards[1]["ebay_imported"])

    def test_attaches_card_type_sport_vs_non_sport(self):
        rows = [
            {"id": "card-1", "front_image_path": None, "back_image_path": None, "category": "Fußball"},
            {"id": "card-2", "front_image_path": None, "back_image_path": None, "category": "Marvel"},
        ]
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value={}):
            response = client.get("/api/cards")
        cards = response.json()["cards"]
        self.assertEqual(cards[0]["card_type"], "sport")
        self.assertEqual(cards[1]["card_type"], "non_sport")

    def test_attaches_manual_sale_channel_when_present(self):
        rows = [
            {"id": "card-1", "front_image_path": None, "back_image_path": None},
            {"id": "card-2", "front_image_path": None, "back_image_path": None},
        ]
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={"card-1": {"channel": "Vinted"}}), \
             patch("main.db.sale_flags_by_card_id", return_value={}):
            response = client.get("/api/cards")
        cards = response.json()["cards"]
        self.assertEqual(cards[0]["manual_sale_channel"], "Vinted")
        self.assertIsNone(cards[1]["manual_sale_channel"])

    def test_attaches_delivered_and_refunded_flags(self):
        rows = [
            {"id": "card-1", "front_image_path": None, "back_image_path": None},
            {"id": "card-2", "front_image_path": None, "back_image_path": None},
        ]
        flags = {"card-1": {"delivered": True, "refunded": False}}
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.db.cards_with_purchase", return_value=set()), \
             patch("main.db.ebay_info_by_card_id", return_value={}), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sale_flags_by_card_id", return_value=flags):
            response = client.get("/api/cards")
        cards = response.json()["cards"]
        self.assertTrue(cards[0]["delivered"])
        self.assertFalse(cards[0]["refunded"])
        self.assertFalse(cards[1]["delivered"])
        self.assertFalse(cards[1]["refunded"])


class RecognizeCardImagesEndpointTests(unittest.TestCase):
    def _post_recognize(self):
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        return client.post("/api/cards/recognize", files=files)

    def test_returns_recognized_fields(self):
        fake_result = {"title": "Max Mustermann", "category": "Fußball", "status": "ok"}
        with patch("main.recognize_card", return_value=fake_result) as mock_recognize:
            response = self._post_recognize()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), fake_result)
        mock_recognize.assert_called_once()

    def test_does_not_touch_the_database(self):
        with patch("main.recognize_card", return_value={"status": "ok"}), \
             patch("main.db.create_batch") as mock_create_batch:
            self._post_recognize()
        mock_create_batch.assert_not_called()

    def test_missing_file_is_422(self):
        response = client.post("/api/cards/recognize", files={"front": ("front.jpg", b"x", "image/jpeg")})
        self.assertEqual(response.status_code, 422)


class CreateCardManualEndpointTests(unittest.TestCase):
    def _post_create(self, fields=None, location=None, notes=None, archive_photos=None):
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        data = {"fields": json.dumps(fields if fields is not None else {"title": "Max Mustermann"})}
        if location is not None:
            data["location"] = location
        if notes is not None:
            data["notes"] = notes
        if archive_photos is not None:
            data["archive_photos"] = "true" if archive_photos else "false"
        return client.post("/api/cards", files=files, data=data)

    def _patch_all(self, **overrides):
        patches = {
            "main.db.create_batch": MagicMock(return_value="batch-1"),
            "main.db.update_batch_status": MagicMock(),
            "main.db.insert_card": MagicMock(return_value={
                "id": "card-1", "batch_id": "batch-1", "title": "Max Mustermann",
                "front_image_path": "batch-1/1_front.jpg", "back_image_path": "batch-1/1_back.jpg",
            }),
            "main.storage.upload_image": MagicMock(side_effect=lambda b, p, side, path: f"{b}/{p}_{side}.jpg"),
            "main.storage.signed_url": MagicMock(side_effect=lambda object_path, **_: f"https://signed/{object_path}"),
            "main.db.create_inventory_item": MagicMock(),
            "main.db.find_duplicate_card": MagicMock(return_value=None),
        }
        patches.update(overrides)
        patchers = [patch(target, new) for target, new in patches.items()]
        for p in patchers:
            self.addCleanup(p.stop)
        return {target: p.start() for target, p in zip(patches, patchers)}

    def test_creates_a_default_inventory_item(self):
        # Same rationale as /api/scan's - the seller physically has the
        # card in hand when adding it manually too, so a quantity-1 "NM"
        # inventory row is created automatically here as well.
        mocks = self._patch_all()
        self._post_create()
        mocks["main.db.create_inventory_item"].assert_called_once_with(
            "card-1", {"quantity": 1, "location": "", "notes": ""}
        )

    def test_passes_location_and_notes_from_the_form(self):
        mocks = self._patch_all()
        self._post_create(location="Regal B", notes="Einzelkarte")
        mocks["main.db.create_inventory_item"].assert_called_once_with(
            "card-1", {"quantity": 1, "location": "Regal B", "notes": "Einzelkarte"}
        )

    def test_private_collection_field_creates_zero_quantity_inventory(self):
        mocks = self._patch_all()
        self._post_create(fields={"title": "Max Mustermann", "private_collection": True})
        mocks["main.db.create_inventory_item"].assert_called_once_with(
            "card-1", {"quantity": 0, "location": "", "notes": ""}
        )

    def test_inventory_item_creation_failure_does_not_fail_the_request(self):
        mocks = self._patch_all()
        mocks["main.db.create_inventory_item"].side_effect = RuntimeError("inventory table down")
        response = self._post_create()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "card-1")

    def test_creates_batch_with_count_one(self):
        mocks = self._patch_all()
        self._post_create()
        mocks["main.db.create_batch"].assert_called_once_with(card_count=1)

    def test_uploads_both_images_at_position_one(self):
        mocks = self._patch_all()
        self._post_create()
        upload_calls = mocks["main.storage.upload_image"].call_args_list
        self.assertEqual(len(upload_calls), 2)
        for call in upload_calls:
            self.assertEqual(call.args[0], "batch-1")
            self.assertEqual(call.args[1], 1)
        sides = {call.args[2] for call in upload_calls}
        self.assertEqual(sides, {"front", "back"})

    def test_inserts_card_with_parsed_fields(self):
        mocks = self._patch_all()
        self._post_create(fields={"title": "Erika Musterfrau", "team": "FC Test"})
        insert_args = mocks["main.db.insert_card"].call_args.args
        self.assertEqual(insert_args[0], "batch-1")
        self.assertEqual(insert_args[1], 1)
        self.assertEqual(insert_args[2]["title"], "Erika Musterfrau")
        self.assertEqual(insert_args[2]["team"], "FC Test")

    def test_marks_batch_ok_on_success(self):
        mocks = self._patch_all()
        self._post_create()
        mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "ok")

    def test_returns_card_with_signed_urls(self):
        self._patch_all()
        response = self._post_create()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "card-1")
        self.assertTrue(body["front_image_url"].startswith("https://signed/"))

    def test_attaches_possible_duplicate_when_found(self):
        mocks = self._patch_all()
        mocks["main.db.find_duplicate_card"].return_value = {"id": "card-existing", "card_no": 7}
        response = self._post_create(fields={"title": "Max Mustermann", "set_name": "Set A", "card_number": "1"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["possible_duplicate"], {"id": "card-existing", "card_no": 7})
        mocks["main.db.find_duplicate_card"].assert_called_once_with("Max Mustermann", "Set A", "1")

    def test_omits_possible_duplicate_when_none_found(self):
        self._patch_all()
        response = self._post_create()
        self.assertNotIn("possible_duplicate", response.json())

    def test_duplicate_check_failure_does_not_fail_the_request(self):
        mocks = self._patch_all()
        mocks["main.db.find_duplicate_card"].side_effect = RuntimeError("cards table down")
        response = self._post_create()
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("possible_duplicate", response.json())

    def test_flags_photo_duplicate_when_no_text_match_but_image_hash_matches(self):
        mocks = self._patch_all()
        photo_duplicate = {"id": "card-photo-match", "title": "Andere Schreibweise", "card_no": 4}
        with patch("main.image_hash.compute_hash", return_value="abc123abc123abc1"), \
             patch("main.db.find_duplicate_card_by_image_hash", return_value=photo_duplicate) as mock_photo:
            response = self._post_create()
        body = response.json()
        self.assertEqual(body["possible_duplicate"], {**photo_duplicate, "matched_by": "photo"})
        mock_photo.assert_called_once_with("abc123abc123abc1", exclude_card_id=None)
        insert_kwargs = mocks["main.db.insert_card"].call_args.kwargs
        self.assertEqual(insert_kwargs["front_image_hash"], "abc123abc123abc1")

    def test_skips_image_hash_lookup_when_text_duplicate_already_found(self):
        mocks = self._patch_all()
        mocks["main.db.find_duplicate_card"].return_value = {"id": "card-existing", "card_no": 7}
        with patch("main.image_hash.compute_hash", return_value="abc123abc123abc1"), \
             patch("main.db.find_duplicate_card_by_image_hash") as mock_photo:
            self._post_create()
        mock_photo.assert_not_called()

    def test_invalid_fields_json_is_400(self):
        self._patch_all()
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        response = client.post("/api/cards", files=files, data={"fields": "not-json"})
        self.assertEqual(response.status_code, 400)

    def test_upload_failure_marks_batch_failed_and_returns_502(self):
        mocks = self._patch_all(
            **{"main.storage.upload_image": MagicMock(side_effect=RuntimeError("Storage down"))}
        )
        response = self._post_create()
        self.assertEqual(response.status_code, 502)
        mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "failed")
        mocks["main.db.insert_card"].assert_not_called()

    def test_insert_failure_marks_batch_failed_and_returns_502(self):
        # Regression test: db.insert_card() must be covered by the same
        # failure handling as storage.upload_image() - otherwise a Supabase
        # insert error leaves the scan_batches row stuck at "pending"
        # forever instead of "failed" (same trap /api/scan's process_one()
        # already guards against for its own insert_card() call).
        mocks = self._patch_all(
            **{"main.db.insert_card": MagicMock(side_effect=RuntimeError("Insert down"))}
        )
        response = self._post_create()
        self.assertEqual(response.status_code, 502)
        mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "failed")

    def test_missing_file_is_422(self):
        self._patch_all()
        response = client.post(
            "/api/cards", files={"front": ("front.jpg", b"x", "image/jpeg")},
            data={"fields": "{}"},
        )
        self.assertEqual(response.status_code, 422)

    def test_archive_photos_true_archives_front_and_back(self):
        self._patch_all()
        with patch("main._archive_handyscan_photos_safe") as mock_archive:
            response = self._post_create(archive_photos=True)
        self.assertEqual(response.status_code, 200)
        mock_archive.assert_called_once()
        front_path, back_path = mock_archive.call_args.args
        self.assertTrue(str(front_path).endswith(".jpg"))
        self.assertTrue(str(back_path).endswith(".jpg"))

    def test_archive_photos_omitted_does_not_archive(self):
        self._patch_all()
        with patch("main._archive_handyscan_photos_safe") as mock_archive:
            response = self._post_create()
        self.assertEqual(response.status_code, 200)
        mock_archive.assert_not_called()

    def test_archive_photos_false_does_not_archive(self):
        self._patch_all()
        with patch("main._archive_handyscan_photos_safe") as mock_archive:
            response = self._post_create(archive_photos=False)
        self.assertEqual(response.status_code, 200)
        mock_archive.assert_not_called()

    def test_archive_failure_does_not_fail_the_request(self):
        # _archive_handyscan_photos_safe() swallows its own errors (siehe
        # main.py) - dieser Test ruft die echte Funktion mit einem nicht
        # beschreibbaren Zielverzeichnis auf, um sicherzustellen, dass ein
        # Archivierungsfehler das Kartenanlegen nicht scheitern laesst.
        self._patch_all()
        with patch("main.HANDYSCAN_ARCHIVE_DIR", Path("/nonexistent-root-only/handyscan")):
            response = self._post_create(archive_photos=True)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["id"], "card-1")


if __name__ == "__main__":
    unittest.main()
