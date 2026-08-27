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
        "id": "card-1", "title": "Musterkarte", "category": "Fußball",
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
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.create_ebay_listing", return_value=_listing()) as mock_create:
            response = client.post("/api/cards/card-1/ebay-listing")
        self.assertEqual(response.status_code, 200)
        args, _ = mock_create.call_args
        card_id, sku, row = args
        self.assertEqual(card_id, "card-1")
        self.assertEqual(sku, "webapp-card-1")
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


class GetEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.get("/api/ebay/listings/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_returns_listing_with_card_summary(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.get_card", return_value=_card()):
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
             patch("main.storage.public_url", return_value="https://img/x.jpg"), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
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

    def test_returns_409_when_not_draft(self):
        with patch("main.db.get_ebay_listing", return_value=_listing(status="Veroeffentlicht")):
            response = client.delete("/api/ebay/listings/listing-1")
        self.assertEqual(response.status_code, 409)

    def test_deletes_draft(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.delete_ebay_listing", return_value=_listing()) as mock_delete:
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
             patch("main.ebay_client.get_access_token", return_value="tok"), \
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

    def test_returns_502_on_ebay_api_error(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Fehler")), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_listing_policies", side_effect=ebay_client.EbayApiError("Policy fehlt")):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 502)

    def test_returns_401_when_not_authorized(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Fehler")), \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.ebay_client.get_access_token", side_effect=ebay_client.EbayNotAuthorizedError("nicht verbunden")):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 401)

    def test_scheduled_in_future_uses_app_mode_without_calling_ebay(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Geplant")) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
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
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw:
            response = client.post("/api/ebay/listings/listing-1/unschedule")
        self.assertEqual(response.status_code, 200)
        mock_withdraw.assert_called_once_with("tok", "offer-1")


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
             patch("main.ebay_client.get_access_token", return_value="tok"), \
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


class OauthStatusEndpointTests(unittest.TestCase):
    def test_proxies_oauth_server_response(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"authorized": True, "environment": "sandbox"}
        with patch("main.httpx.get", return_value=mock_response):
            response = client.get("/api/ebay/oauth/status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authorized"])


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
             patch("main.db.update_ebay_listing", return_value=matched_listing) as mock_update:
            response = client.post("/api/ebay/sync-sales")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body, {"synced": 1, "skipped": 1})
        mock_upsert.assert_called_once()
        mock_update.assert_called_once_with("listing-1", {"status": "Verkauft"})


if __name__ == "__main__":
    unittest.main()
