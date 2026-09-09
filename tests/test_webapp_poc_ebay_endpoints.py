"""Tests for /api/ebay/* and /api/cards/{id}/ebay-listing (webapp-poc/main.py)."""
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
import ebay_client  # noqa: E402

client = TestClient(main.app)


def _card(**overrides):
    card = {
        "id": "card-1", "card_no": 1, "title": "Musterkarte", "category": "Fußball",
        "team": "FC Beispiel", "manufacturer": "Topps", "set_name": "Bundesliga 2024",
        "season_year": "2024", "card_number": "12", "front_image_path": None,
    }
    card.update(overrides)
    return card


def _listing(**overrides):
    listing = {
        "id": "listing-1", "card_id": "card-1", "sku": "webapp-card-1",
        "title": "Musterkarte", "description": "desc", "listing_type": "sport",
        "category_id": "261328", "aspects": {"Sportart": ["Fußball"]},
        "price": 9.99, "quantity": 1, "status": "Entwurf",
        "ebay_offer_id": "", "ebay_listing_id": "", "scheduled_at": None,
        "scheduling_mode": "",
    }
    listing.update(overrides)
    return listing


class CreateEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_card_not_found(self):
        with patch("main.db.get_card", return_value=None):
            response = client.post("/api/cards/does-not-exist/ebay-listing")
        self.assertEqual(response.status_code, 404)

    def test_returns_409_when_listing_already_exists(self):
        with patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_ebay_listing_for_card", return_value=_listing()):
            response = client.post("/api/cards/card-1/ebay-listing")
        self.assertEqual(response.status_code, 409)

    def test_generates_fields_from_card_when_body_omitted(self):
        with patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.create_ebay_listing", return_value=_listing()) as mock_create:
            response = client.post("/api/cards/card-1/ebay-listing")
        self.assertEqual(response.status_code, 200)
        args, _ = mock_create.call_args
        card_id, sku, row = args
        self.assertEqual(card_id, "card-1")
        self.assertEqual(sku, "webapp-000001")
        self.assertIn("Musterkarte", row["title"])
        self.assertEqual(row["listing_type"], "sport")
        self.assertEqual(row["category_id"], "261328")
        self.assertEqual(row["aspects"]["Sportart"], ["Fußball"])
        body = response.json()
        self.assertIn("required_aspects", body)


class ListEbayListingsEndpointTests(unittest.TestCase):
    def test_passes_filters_through(self):
        with patch("main.db.list_ebay_listings", return_value=[]) as mock_list, \
             patch("main.db.get_card", return_value=_card()):
            response = client.get("/api/ebay/listings?status=Entwurf&q=Muster")
        self.assertEqual(response.status_code, 200)
        mock_list.assert_called_once_with(status="Entwurf", q="Muster")

    def test_attaches_sale_info_for_sold_listings(self):
        sold_listing = _listing(status="Verkauft")
        with patch("main.db.list_ebay_listings", return_value=[sold_listing]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sales_by_listing_id", return_value={
                 "listing-1": {"sale_date": "2026-08-01T00:00:00+00:00", "gross_price": 12.5}
             }) as mock_sales:
            response = client.get("/api/ebay/listings")
        listing = response.json()["listings"][0]
        self.assertEqual(listing["sale_price"], 12.5)
        self.assertEqual(listing["sale_date"], "2026-08-01T00:00:00+00:00")
        mock_sales.assert_called_once_with(["listing-1"])

    def test_skips_sale_lookup_when_nothing_sold(self):
        with patch("main.db.list_ebay_listings", return_value=[_listing()]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.sales_by_listing_id") as mock_sales:
            response = client.get("/api/ebay/listings")
        self.assertIsNone(response.json()["listings"][0]["sale_price"])
        mock_sales.assert_not_called()

    def test_attaches_manual_sale_channel_when_card_sold_elsewhere(self):
        listing = _listing(status="Veroeffentlicht")
        with patch("main.db.list_ebay_listings", return_value=[listing]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={"card-1": {"channel": "Kleinanzeigen"}}):
            response = client.get("/api/ebay/listings")
        self.assertEqual(response.json()["listings"][0]["manual_sale_channel"], "Kleinanzeigen")


class GetEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.get("/api/ebay/listings/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_returns_listing_with_card_summary(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}):
            response = client.get("/api/ebay/listings/listing-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["card"]["title"], "Musterkarte")


class UpdateEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.patch("/api/ebay/listings/does-not-exist", json={"price": 5})
        self.assertEqual(response.status_code, 404)

    def test_updates_draft_without_republishing(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(price=12.0)) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token") as mock_token:
            response = client.patch("/api/ebay/listings/listing-1", json={"price": 12.0})
        self.assertEqual(response.status_code, 200)
        mock_update.assert_called_once_with("listing-1", {"price": 12.0})
        mock_token.assert_not_called()

    def test_republishes_when_already_published(self):
        published = _listing(status="Veroeffentlicht", ebay_offer_id="offer-1")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.db.update_ebay_listing", return_value=published) as mock_update, \
             patch("main.db.get_card", return_value=_card(front_image_path="b1/1_front.jpg")), \
             patch("main.db.get_cards_by_ids", return_value=[_card(front_image_path="b1/1_front.jpg")]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.storage.public_url", return_value="https://img/x.jpg"), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={}), \
             patch("main.ebay_client.put_inventory_item") as mock_put, \
             patch("main.ebay_client.update_offer") as mock_update_offer, \
             patch("main.ebay_client.publish_offer", return_value="L1"):
            response = client.patch("/api/ebay/listings/listing-1", json={"price": 12.0})
        self.assertEqual(response.status_code, 200)
        mock_put.assert_called_once()
        mock_update_offer.assert_called_once()


class DeleteEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.delete("/api/ebay/listings/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_returns_409_when_not_draft_or_failed(self):
        with patch("main.db.get_ebay_listing", return_value=_listing(status="Veroeffentlicht")):
            response = client.delete("/api/ebay/listings/listing-1")
        self.assertEqual(response.status_code, 409)

    def test_deletes_draft(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.delete_ebay_listing", return_value=_listing()) as mock_delete:
            response = client.delete("/api/ebay/listings/listing-1")
        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with("listing-1")

    def test_deletes_failed_listing(self):
        # Regression test: card.html's edit form shows a delete button for
        # status "Fehler" too (renderEbaySection treats "Entwurf" and
        # "Fehler" alike), but the backend only ever allowed "Entwurf" -
        # clicking delete on a failed listing silently 409'd.
        with patch("main.db.get_ebay_listing", return_value=_listing(status="Fehler")), \
             patch("main.db.delete_ebay_listing", return_value=_listing(status="Fehler")) as mock_delete:
            response = client.delete("/api/ebay/listings/listing-1")
        self.assertEqual(response.status_code, 204)
        mock_delete.assert_called_once_with("listing-1")


class PublishEbayListingEndpointTests(unittest.TestCase):
    def test_returns_422_when_required_aspects_missing(self):
        listing = _listing(aspects={})
        with patch("main.db.get_ebay_listing", return_value=listing), \
             patch("main.ebay_client.put_inventory_item") as mock_put:
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 422)
        mock_put.assert_not_called()

    def test_publishes_successfully(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Veroeffentlicht")) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={"fulfillmentPolicyId": "F1"}), \
             patch("main.ebay_client.put_inventory_item"), \
             patch("main.ebay_client.create_offer", return_value="offer-1"), \
             patch("main.ebay_client.publish_offer", return_value="L1"):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 200)
        updates = mock_update.call_args[0][1]
        self.assertEqual(updates["status"], "Veroeffentlicht")
        self.assertEqual(updates["ebay_offer_id"], "offer-1")
        self.assertEqual(updates["ebay_listing_id"], "L1")

    def test_sends_both_front_and_back_image_urls(self):
        # Regression test: the first real production publish only sent the
        # front photo to eBay - _publish_listing() built image_url from
        # front_image_path alone, never looking at back_image_path.
        card = _card(front_image_path="b1/1_front.jpg", back_image_path="b1/1_back.jpg")
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Veroeffentlicht")), \
             patch("main.db.get_card", return_value=card), \
             patch("main.db.get_cards_by_ids", return_value=[card]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.storage.public_url", side_effect=lambda path: f"https://img/{path}"), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={"fulfillmentPolicyId": "F1"}), \
             patch("main.ebay_client.put_inventory_item") as mock_put, \
             patch("main.ebay_client.create_offer", return_value="offer-1"), \
             patch("main.ebay_client.publish_offer", return_value="L1"):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 200)
        image_urls = mock_put.call_args[0][3]
        self.assertEqual(image_urls, ["https://img/b1/1_front.jpg", "https://img/b1/1_back.jpg"])

    def test_includes_extra_photos_after_front_and_back_capped_at_twelve(self):
        extra = ",".join(f"b1/1_extra_{i}.jpg" for i in range(11))
        card = _card(front_image_path="b1/1_front.jpg", back_image_path="b1/1_back.jpg", extra_image_paths=extra)
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Veroeffentlicht")), \
             patch("main.db.get_card", return_value=card), \
             patch("main.db.get_cards_by_ids", return_value=[card]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.storage.public_url", side_effect=lambda path: f"https://img/{path}"), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={"fulfillmentPolicyId": "F1"}), \
             patch("main.ebay_client.put_inventory_item") as mock_put, \
             patch("main.ebay_client.create_offer", return_value="offer-1"), \
             patch("main.ebay_client.publish_offer", return_value="L1"):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 200)
        image_urls = mock_put.call_args[0][3]
        # 2 (front+back) + 11 extras = 13, capped at eBay's 12-image limit.
        self.assertEqual(len(image_urls), 12)
        self.assertEqual(image_urls[0], "https://img/b1/1_front.jpg")
        self.assertEqual(image_urls[1], "https://img/b1/1_back.jpg")
        self.assertEqual(image_urls[2], "https://img/b1/1_extra_0.jpg")

    def test_returns_502_on_ebay_api_error(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Fehler")), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", side_effect=ebay_client.EbayApiError("Policy fehlt")):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 502)

    def test_logs_which_step_failed(self):
        # eBay's sandbox reuses one generic errorId (25002) for many
        # distinct causes, and the raw HTTP response alone doesn't say which
        # of the ~5 sequential eBay calls in _publish_listing() actually
        # failed - the log line naming the step is what makes a live
        # failure diagnosable from container logs alone.
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Fehler")), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", side_effect=ebay_client.EbayApiError("Policy fehlt")), \
             self.assertLogs("ebay_publish", level="INFO") as log_ctx:
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 502)
        joined = "\n".join(log_ctx.output)
        self.assertIn("get_listing_policies", joined)

    def test_persists_offer_id_even_when_a_later_step_fails(self):
        # Regression test: create_offer() succeeding but publish_offer()
        # failing afterward must not lose the offer_id - otherwise every
        # retry calls create_offer() again against an offer eBay already
        # has (errorId 25002 "Offer entity already exists"), an infinite
        # loop discovered live against the real eBay sandbox.
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing()) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={}), \
             patch("main.ebay_client.put_inventory_item"), \
             patch("main.ebay_client.create_offer", return_value="offer-new"), \
             patch("main.ebay_client.publish_offer", side_effect=ebay_client.EbayApiError("Policy fehlt")):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 502)
        offer_id_updates = [
            call.args[1]["ebay_offer_id"] for call in mock_update.call_args_list
            if "ebay_offer_id" in call.args[1]
        ]
        self.assertIn("offer-new", offer_id_updates)

    def test_does_not_recreate_offer_when_offer_id_already_saved(self):
        listing_with_offer = _listing(ebay_offer_id="offer-existing")
        with patch("main.db.get_ebay_listing", return_value=listing_with_offer), \
             patch("main.db.update_ebay_listing", return_value=listing_with_offer), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={}), \
             patch("main.ebay_client.put_inventory_item"), \
             patch("main.ebay_client.create_offer") as mock_create, \
             patch("main.ebay_client.update_offer") as mock_update_offer, \
             patch("main.ebay_client.publish_offer", return_value="L1"):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 200)
        mock_create.assert_not_called()
        mock_update_offer.assert_called_once()

    def test_returns_401_when_not_authorized(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Fehler")), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", side_effect=ebay_client.EbayNotAuthorizedError("nicht verbunden")):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 401)

    def test_scheduled_in_future_uses_app_mode_without_calling_ebay(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Geplant")) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.put_inventory_item") as mock_put, \
             patch("main.ebay_client.create_offer") as mock_create:
            response = client.post(
                "/api/ebay/listings/listing-1/publish",
                json={"scheduled_at": "2999-01-01T10:00:00+00:00"},
            )
        self.assertEqual(response.status_code, 200)
        mock_put.assert_not_called()
        mock_create.assert_not_called()
        updates = mock_update.call_args[0][1]
        self.assertEqual(updates["status"], "Geplant")
        self.assertEqual(updates["scheduling_mode"], "app")


class UnscheduleEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.post("/api/ebay/listings/does-not-exist/unschedule")
        self.assertEqual(response.status_code, 404)

    def test_returns_409_when_not_scheduled(self):
        with patch("main.db.get_ebay_listing", return_value=_listing(status="Entwurf")):
            response = client.post("/api/ebay/listings/listing-1/unschedule")
        self.assertEqual(response.status_code, 409)

    def test_unschedules_app_mode_listing(self):
        scheduled = _listing(status="Geplant", scheduling_mode="app", scheduled_at="2999-01-01T00:00:00Z")
        with patch("main.db.get_ebay_listing", return_value=scheduled), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Entwurf")) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw:
            response = client.post("/api/ebay/listings/listing-1/unschedule")
        self.assertEqual(response.status_code, 200)
        mock_withdraw.assert_not_called()
        mock_update.assert_called_once_with(
            "listing-1", {"status": "Entwurf", "scheduled_at": None, "scheduling_mode": ""}
        )

    def test_unschedules_native_mode_listing_and_withdraws_offer(self):
        scheduled = _listing(
            status="Geplant", scheduling_mode="native",
            scheduled_at="2999-01-01T00:00:00Z", ebay_offer_id="offer-1",
        )
        with patch("main.db.get_ebay_listing", return_value=scheduled), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Entwurf")), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw:
            response = client.post("/api/ebay/listings/listing-1/unschedule")
        self.assertEqual(response.status_code, 200)
        mock_withdraw.assert_called_once_with("tok", "offer-1")


class EndEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.post("/api/ebay/listings/does-not-exist/end")
        self.assertEqual(response.status_code, 404)

    def test_returns_409_when_not_published(self):
        with patch("main.db.get_ebay_listing", return_value=_listing(status="Entwurf")):
            response = client.post("/api/ebay/listings/listing-1/end")
        self.assertEqual(response.status_code, 409)

    def test_withdraws_offer_and_sets_status_to_beendet(self):
        published = _listing(status="Veroeffentlicht", ebay_offer_id="offer-1")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Beendet")) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw:
            response = client.post("/api/ebay/listings/listing-1/end")
        self.assertEqual(response.status_code, 200)
        mock_withdraw.assert_called_once_with("tok", "offer-1")
        mock_update.assert_called_once_with("listing-1", {"status": "Beendet"})

    def test_skips_withdraw_when_no_offer_id(self):
        published = _listing(status="Veroeffentlicht", ebay_offer_id="")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Beendet")), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw:
            response = client.post("/api/ebay/listings/listing-1/end")
        self.assertEqual(response.status_code, 200)
        mock_withdraw.assert_not_called()

    def test_returns_502_when_withdraw_fails(self):
        published = _listing(status="Veroeffentlicht", ebay_offer_id="offer-1")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.withdraw_offer", side_effect=ebay_client.EbayApiError("eBay lehnt ab")):
            response = client.post("/api/ebay/listings/listing-1/end")
        self.assertEqual(response.status_code, 502)

    def test_returns_401_when_not_authorized(self):
        published = _listing(status="Veroeffentlicht", ebay_offer_id="offer-1")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.ebay_client.get_access_token", side_effect=ebay_client.EbayNotAuthorizedError("kein Token")):
            response = client.post("/api/ebay/listings/listing-1/end")
        self.assertEqual(response.status_code, 401)


class PublishBulkEndpointTests(unittest.TestCase):
    def test_mixed_success_and_failure_does_not_abort(self):
        listing_a = _listing(id="a")
        listing_b = _listing(id="b")

        def get_listing(listing_id):
            return {"a": listing_a, "b": listing_b}[listing_id]

        def publish_side_effect(*args, **kwargs):
            # main._publish_listing() internally calls ebay_client.create_offer;
            # let "a" succeed and "b" raise, verifying "a" isn't rolled back.
            raise ebay_client.EbayApiError("eBay lehnt Karte b ab")

        with patch("main.db.get_ebay_listing", side_effect=get_listing), \
             patch("main.db.update_ebay_listing", side_effect=lambda lid, updates: {**get_listing(lid), **updates}), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={}), \
             patch("main.ebay_client.put_inventory_item"), \
             patch("main.ebay_client.create_offer", side_effect=["offer-a", ebay_client.EbayApiError("eBay lehnt Karte b ab")]), \
             patch("main.ebay_client.publish_offer", return_value="L1"):
            response = client.post("/api/ebay/listings/publish-bulk", json={"listing_ids": ["a", "b"]})
        self.assertEqual(response.status_code, 200)
        results = response.json()["results"]
        self.assertEqual(results[0]["listing_id"], "a")
        self.assertEqual(results[0]["status"], "Veroeffentlicht")
        self.assertEqual(results[1]["listing_id"], "b")
        self.assertEqual(results[1]["status"], "Fehler")

    def test_unknown_listing_id_reports_error_without_raising(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.post("/api/ebay/listings/publish-bulk", json={"listing_ids": ["does-not-exist"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["status"], "Fehler")


class PriceBulkEndpointTests(unittest.TestCase):
    def test_applies_negative_percent_and_rounds(self):
        listing = _listing(price=10.0, status="Entwurf")
        with patch("main.db.get_ebay_listing", return_value=listing), \
             patch("main.db.update_ebay_listing", side_effect=lambda lid, updates: {**listing, **updates}) as mock_update, \
             patch("main.ebay_client.get_access_token") as mock_token:
            response = client.post(
                "/api/ebay/listings/price-bulk",
                json={"listing_ids": ["listing-1"], "percent": -10},
            )
        self.assertEqual(response.status_code, 200)
        result = response.json()["results"][0]
        self.assertEqual(result["price"], 9.0)
        mock_update.assert_called_once_with("listing-1", {"price": 9.0})
        mock_token.assert_not_called()

    def test_applies_positive_percent(self):
        listing = _listing(price=10.0, status="Entwurf")
        with patch("main.db.get_ebay_listing", return_value=listing), \
             patch("main.db.update_ebay_listing", side_effect=lambda lid, updates: {**listing, **updates}):
            response = client.post(
                "/api/ebay/listings/price-bulk",
                json={"listing_ids": ["listing-1"], "percent": 20},
            )
        self.assertEqual(response.json()["results"][0]["price"], 12.0)

    def test_never_goes_below_zero(self):
        listing = _listing(price=10.0, status="Entwurf")
        with patch("main.db.get_ebay_listing", return_value=listing), \
             patch("main.db.update_ebay_listing", side_effect=lambda lid, updates: {**listing, **updates}):
            response = client.post(
                "/api/ebay/listings/price-bulk",
                json={"listing_ids": ["listing-1"], "percent": -500},
            )
        self.assertEqual(response.json()["results"][0]["price"], 0.0)

    def test_republishes_when_already_published(self):
        published = _listing(price=10.0, status="Veroeffentlicht", ebay_offer_id="offer-1")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.db.update_ebay_listing", side_effect=lambda lid, updates: {**published, **updates}), \
             patch("main.db.get_card", return_value=_card(front_image_path="b1/1_front.jpg")), \
             patch("main.db.get_cards_by_ids", return_value=[_card(front_image_path="b1/1_front.jpg")]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.storage.public_url", return_value="https://img/x.jpg"), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={}), \
             patch("main.ebay_client.put_inventory_item") as mock_put, \
             patch("main.ebay_client.update_offer") as mock_update_offer, \
             patch("main.ebay_client.publish_offer", return_value="L1"):
            response = client.post(
                "/api/ebay/listings/price-bulk",
                json={"listing_ids": ["listing-1"], "percent": -10},
            )
        self.assertEqual(response.status_code, 200)
        mock_put.assert_called_once()
        mock_update_offer.assert_called_once()

    def test_unknown_listing_id_reports_error_without_raising(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.post(
                "/api/ebay/listings/price-bulk",
                json={"listing_ids": ["does-not-exist"], "percent": -10},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("error", response.json()["results"][0])

    def test_missing_percent_is_400(self):
        response = client.post("/api/ebay/listings/price-bulk", json={"listing_ids": ["listing-1"]})
        self.assertEqual(response.status_code, 400)


class OauthStatusEndpointTests(unittest.TestCase):
    def test_proxies_oauth_server_response(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"authorized": True, "environment": "sandbox"}
        with patch("main.httpx.get", return_value=mock_response):
            response = client.get("/api/ebay/oauth/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authorized"])

    def test_returns_json_502_when_oauth_server_unreachable(self):
        # Ohne die eigene Fehlerbehandlung wuerde main.py hier eine
        # unbehandelte Exception werfen, auf die FastAPI mit reinem Text
        # statt JSON antwortet - Aufrufer wie das Dashboard erwarten immer
        # gueltiges JSON zurueck.
        import httpx
        with patch("main.httpx.get", side_effect=httpx.ConnectError("nope")):
            response = client.get("/api/ebay/oauth/status")
        self.assertEqual(response.status_code, 502)
        self.assertFalse(response.json()["authorized"])


class EbayPriceResearchEndpointTests(unittest.TestCase):
    def test_returns_active_listing_results(self):
        results = [{"title": "Musterkarte", "price": 12.5, "currency": "EUR", "condition": "Used", "item_web_url": "https://x"}]
        with patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.search_active_listings", return_value=results) as mock_search:
            response = client.get("/api/ebay/price-research?q=Musterkarte")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"], results)
        mock_search.assert_called_once_with("app-tok", "Musterkarte")

    def test_returns_502_on_api_error(self):
        with patch("main.ebay_client.get_application_access_token", side_effect=ebay_client.EbayApiError("boom")):
            response = client.get("/api/ebay/price-research?q=Musterkarte")
        self.assertEqual(response.status_code, 502)

    def test_excludes_auto_listing_when_query_is_not_auto(self):
        # Eine Auto(gramm)-Karte ist eine andere, meist deutlich teurere
        # Variante als die gesuchte Basiskarte - taucht sie trotzdem in den
        # Ergebnissen auf, wuerde sie den Preisdurchschnitt verzerren.
        results = [
            {"title": "Musterkarte", "price": 10.0},
            {"title": "Musterkarte Auto", "price": 200.0},
            {"title": "Musterkarte Autograph", "price": 250.0},
        ]
        with patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.search_active_listings", return_value=results):
            response = client.get("/api/ebay/price-research?q=Musterkarte")
        titles = [r["title"] for r in response.json()["results"]]
        self.assertEqual(titles, ["Musterkarte"])

    def test_keeps_auto_listings_when_query_is_auto(self):
        results = [
            {"title": "Musterkarte", "price": 10.0},
            {"title": "Musterkarte Auto", "price": 200.0},
        ]
        with patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.search_active_listings", return_value=results):
            response = client.get("/api/ebay/price-research?q=Musterkarte+Auto")
        titles = [r["title"] for r in response.json()["results"]]
        self.assertEqual(titles, ["Musterkarte", "Musterkarte Auto"])

    def test_excludes_numbered_listing_when_query_is_not_numbered(self):
        results = [
            {"title": "Musterkarte", "price": 10.0},
            {"title": "Musterkarte /50", "price": 80.0},
        ]
        with patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.search_active_listings", return_value=results):
            response = client.get("/api/ebay/price-research?q=Musterkarte")
        titles = [r["title"] for r in response.json()["results"]]
        self.assertEqual(titles, ["Musterkarte"])

    def test_keeps_matching_print_run_and_excludes_different_one(self):
        # Verschiedene Auflagen (/50 vs. /99) sind unterschiedliche Karten mit
        # unterschiedlichem Marktpreis - nur die exakt gesuchte Auflage soll
        # in die Preisrecherche einfliessen, ein unnummeriertes Angebot aber
        # weiterhin (koennte schlicht ohne Auflage im Titel stehen).
        results = [
            {"title": "Musterkarte /50", "price": 80.0},
            {"title": "Musterkarte /99", "price": 40.0},
            {"title": "Musterkarte", "price": 10.0},
        ]
        with patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.search_active_listings", return_value=results):
            response = client.get("/api/ebay/price-research?q=Musterkarte+/50")
        titles = [r["title"] for r in response.json()["results"]]
        self.assertEqual(titles, ["Musterkarte /50", "Musterkarte"])


class SyncSalesEndpointTests(unittest.TestCase):
    def test_returns_401_when_not_authorized(self):
        with patch("main.ebay_client.get_access_token", side_effect=ebay_client.EbayNotAuthorizedError("x")):
            response = client.post("/api/ebay/sync-sales")
        self.assertEqual(response.status_code, 401)

    def test_matches_known_sku_and_skips_unknown(self):
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "lineItems": [
                {"sku": "webapp-card-1", "lineItemId": "LI1", "quantity": 1, "total": {"value": "9.99"}},
                {"sku": "webapp-unknown", "lineItemId": "LI2", "quantity": 1, "total": {"value": "5.00"}},
            ],
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.db.list_ebay_listings", return_value=[matched_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1"}) as mock_upsert, \
             patch("main.db.update_ebay_listing", return_value=matched_listing) as mock_update, \
             patch("main.db.zero_inventory_for_card") as mock_zero:
            response = client.post("/api/ebay/sync-sales")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body, {"synced": 1, "skipped": 1})
        mock_upsert.assert_called_once()
        mock_update.assert_called_once_with("listing-1", {"status": "Verkauft"})
        mock_zero.assert_called_once_with("card-1")

    def test_captures_shipping_charged_from_delivery_cost(self):
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "lineItems": [{
                "sku": "webapp-card-1", "lineItemId": "LI1", "quantity": 1,
                "total": {"value": "9.99"},
                "deliveryCost": {"shippingCost": {"value": "3.50", "currency": "EUR"}},
            }],
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.db.list_ebay_listings", return_value=[matched_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1"}) as mock_upsert, \
             patch("main.db.update_ebay_listing", return_value=matched_listing), \
             patch("main.db.zero_inventory_for_card"):
            client.post("/api/ebay/sync-sales")
        sale_fields = mock_upsert.call_args[0][0]
        self.assertEqual(sale_fields["shipping_charged"], 3.5)

    def test_missing_delivery_cost_defaults_shipping_charged_to_zero(self):
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "lineItems": [{"sku": "webapp-card-1", "lineItemId": "LI1", "quantity": 1, "total": {"value": "9.99"}}],
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.db.list_ebay_listings", return_value=[matched_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1"}) as mock_upsert, \
             patch("main.db.update_ebay_listing", return_value=matched_listing), \
             patch("main.db.zero_inventory_for_card"):
            client.post("/api/ebay/sync-sales")
        sale_fields = mock_upsert.call_args[0][0]
        self.assertEqual(sale_fields["shipping_charged"], 0.0)

    def test_inventory_zeroing_failure_does_not_fail_the_sync(self):
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "lineItems": [{"sku": "webapp-card-1", "lineItemId": "LI1", "quantity": 1, "total": {"value": "9.99"}}],
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.db.list_ebay_listings", return_value=[matched_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1"}), \
             patch("main.db.update_ebay_listing", return_value=matched_listing), \
             patch("main.db.zero_inventory_for_card", side_effect=RuntimeError("boom")):
            response = client.post("/api/ebay/sync-sales")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"synced": 1, "skipped": 0})

    def test_returns_502_instead_of_crashing_when_get_orders_fails(self):
        # A regression test: get_orders() raising EbayApiError (eBay
        # rejects the request, network hiccup, ...) must not escape as an
        # unhandled 500 - only get_access_token()'s EbayNotAuthorizedError
        # was caught before this fix.
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", side_effect=ebay_client.EbayApiError("eBay lehnt die Anfrage ab")):
            response = client.post("/api/ebay/sync-sales")
        self.assertEqual(response.status_code, 502)
        self.assertIn("eBay lehnt die Anfrage ab", response.json()["detail"])


class UpdateEbaySaleEndpointTests(unittest.TestCase):
    def test_updates_shipping_cost(self):
        with patch("main.db.update_ebay_sale", return_value={"id": "sale-1", "shipping_cost": 3.5}) as mock_update:
            response = client.patch("/api/ebay/sales/sale-1", json={"shipping_cost": 3.5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["shipping_cost"], 3.5)
        mock_update.assert_called_once_with("sale-1", {"shipping_cost": 3.5})

    def test_returns_404_when_not_found(self):
        with patch("main.db.update_ebay_sale", return_value=None):
            response = client.patch("/api/ebay/sales/does-not-exist", json={"shipping_cost": 1})
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
