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


class GetCardPurchaseFieldTests(unittest.TestCase):
    def test_includes_purchase_info_when_linked(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        purchase_info = {"purchase_id": "p1", "item_id": "item-1", "platform": "eBay"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=purchase_info), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["purchase"], purchase_info)

    def test_purchase_is_null_when_not_linked(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertIsNone(response.json()["purchase"])


class GetCardEbayListingFieldTests(unittest.TestCase):
    def test_includes_ebay_listing_when_linked(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        listing = {"id": "listing-1", "card_id": "card-1", "status": "Entwurf"}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=listing), \
             patch("main.db.get_inventory_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["ebay_listing"], listing)

    def test_ebay_listing_is_null_when_none_exists(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertIsNone(response.json()["ebay_listing"])


class GetCardInventoryFieldTests(unittest.TestCase):
    def test_includes_inventory_items_for_the_card(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        items = [{"id": "inv-1", "card_id": "card-1", "quantity": 2}]
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=items):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["inventory"], items)

    def test_inventory_is_empty_list_when_none_exists(self):
        card = {"id": "card-1", "front_image_path": None, "back_image_path": None}
        with patch("main.db.get_card", return_value=card), \
             patch("main.db.get_purchase_for_card", return_value=None), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.get_inventory_for_card", return_value=[]):
            response = client.get("/api/cards/card-1")
        self.assertEqual(response.json()["inventory"], [])


class ListCardsHasPurchaseFieldTests(unittest.TestCase):
    def test_flags_cards_with_a_linked_purchase(self):
        rows = [
            {"id": "card-1", "front_image_path": None, "back_image_path": None},
            {"id": "card-2", "front_image_path": None, "back_image_path": None},
        ]
        with patch("main.db.list_cards", return_value=rows), \
             patch("main.db.cards_with_purchase", return_value={"card-1"}), \
             patch("main.db.ebay_info_by_card_id", return_value={}):
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
             patch("main.db.ebay_info_by_card_id", return_value=info):
            response = client.get("/api/cards")
        cards = response.json()["cards"]
        self.assertEqual(cards[0]["ebay_status"], "Veroeffentlicht")
        self.assertEqual(cards[0]["ebay_sku"], "webapp-000001")
        self.assertIsNone(cards[1]["ebay_status"])
        self.assertIsNone(cards[1]["ebay_sku"])


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
    def _post_create(self, fields=None):
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        data = {"fields": json.dumps(fields if fields is not None else {"title": "Max Mustermann"})}
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
        }
        patches.update(overrides)
        patchers = [patch(target, new) for target, new in patches.items()]
        for p in patchers:
            self.addCleanup(p.stop)
        return {target: p.start() for target, p in zip(patches, patchers)}

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


if __name__ == "__main__":
    unittest.main()
