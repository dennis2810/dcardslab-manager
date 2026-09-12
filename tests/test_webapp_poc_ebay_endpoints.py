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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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

    def test_inserts_extra_note_into_generated_description(self):
        with patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.create_ebay_listing", return_value=_listing()) as mock_create:
            response = client.post(
                "/api/cards/card-1/ebay-listing", json={"extra_note": "Aus meiner Sammlung."}
            )
        self.assertEqual(response.status_code, 200)
        _, _, row = mock_create.call_args[0]
        self.assertIn("Aus meiner Sammlung.", row["description"])

    def test_explicit_description_overrides_extra_note(self):
        with patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.create_ebay_listing", return_value=_listing()) as mock_create:
            response = client.post(
                "/api/cards/card-1/ebay-listing",
                json={"description": "Eigener Text", "extra_note": "sollte ignoriert werden"},
            )
        self.assertEqual(response.status_code, 200)
        _, _, row = mock_create.call_args[0]
        self.assertEqual(row["description"], "Eigener Text")

    def test_carries_auto_relist_after_days_through(self):
        with patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.create_ebay_listing", return_value=_listing()) as mock_create:
            response = client.post(
                "/api/cards/card-1/ebay-listing", json={"auto_relist_after_days": 14}
            )
        self.assertEqual(response.status_code, 200)
        _, _, row = mock_create.call_args[0]
        self.assertEqual(row["auto_relist_after_days"], 14)

    def test_auto_relist_after_days_defaults_to_none(self):
        with patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.db.create_ebay_listing", return_value=_listing()) as mock_create:
            response = client.post("/api/cards/card-1/ebay-listing")
        self.assertEqual(response.status_code, 200)
        _, _, row = mock_create.call_args[0]
        self.assertIsNone(row["auto_relist_after_days"])


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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.db.sales_by_listing_id") as mock_sales:
            response = client.get("/api/ebay/listings")
        self.assertIsNone(response.json()["listings"][0]["sale_price"])
        mock_sales.assert_not_called()

    def test_attaches_delivered_and_refunded_flags_for_sold_listings(self):
        sold_listing = _listing(status="Verkauft")
        with patch("main.db.list_ebay_listings", return_value=[sold_listing]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.db.sales_by_listing_id", return_value={
                 "listing-1": {
                     "sale_date": "2026-08-01T00:00:00+00:00", "gross_price": 12.5,
                     "delivered": True, "refunded": False,
                 }
             }):
            response = client.get("/api/ebay/listings")
        listing = response.json()["listings"][0]
        self.assertTrue(listing["delivered"])
        self.assertFalse(listing["refunded"])

    def test_delivered_and_refunded_default_to_false_when_nothing_sold(self):
        with patch("main.db.list_ebay_listings", return_value=[_listing()]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.db.sales_by_listing_id") as mock_sales:
            response = client.get("/api/ebay/listings")
        listing = response.json()["listings"][0]
        self.assertFalse(listing["delivered"])
        self.assertFalse(listing["refunded"])
        mock_sales.assert_not_called()

    def test_attaches_manual_sale_channel_when_card_sold_elsewhere(self):
        listing = _listing(status="Veroeffentlicht")
        with patch("main.db.list_ebay_listings", return_value=[listing]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={"card-1": {"channel": "Kleinanzeigen"}}), \
             patch("main.db.price_research_by_card_ids", return_value={}):
            response = client.get("/api/ebay/listings")
        self.assertEqual(response.json()["listings"][0]["manual_sale_channel"], "Kleinanzeigen")

    def test_attaches_private_collection_flag(self):
        listing = _listing(status="Veroeffentlicht")
        with patch("main.db.list_ebay_listings", return_value=[listing]), \
             patch("main.db.get_cards_by_ids", return_value=[_card(private_collection=True)]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}):
            response = client.get("/api/ebay/listings")
        self.assertTrue(response.json()["listings"][0]["private_collection"])

    def test_private_collection_flag_defaults_to_false(self):
        with patch("main.db.list_ebay_listings", return_value=[_listing()]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}):
            response = client.get("/api/ebay/listings")
        self.assertFalse(response.json()["listings"][0]["private_collection"])

    def test_attaches_price_research_average_and_count(self):
        with patch("main.db.list_ebay_listings", return_value=[_listing()]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids",
                   return_value={"card-1": {"avg_price": 15.0, "count": 2}}):
            response = client.get("/api/ebay/listings")
        listing = response.json()["listings"][0]
        self.assertEqual(listing["price_research_avg"], 15.0)
        self.assertEqual(listing["price_research_count"], 2)

    def test_price_research_fields_default_when_no_data(self):
        with patch("main.db.list_ebay_listings", return_value=[_listing()]), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}):
            response = client.get("/api/ebay/listings")
        listing = response.json()["listings"][0]
        self.assertIsNone(listing["price_research_avg"])
        self.assertEqual(listing["price_research_count"], 0)


class ListEbayListingViewsEndpointTests(unittest.TestCase):
    def test_returns_views_per_listing_id(self):
        listings = [_listing(id="listing-1", ebay_listing_id="111"), _listing(id="listing-2", ebay_listing_id="222")]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_listing_views", return_value={"111": 42, "222": 7}) as mock_views, \
             patch("main.db.list_ebay_listings", return_value=listings), \
             patch("main.db.update_ebay_listing") as mock_update:
            response = client.get("/api/ebay/listings/views?listing_ids=111,222")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["views"], {"111": 42, "222": 7})
        mock_views.assert_called_once_with("tok", ["111", "222"])
        # Bleibt in der Karten-/eBay-Uebersicht sichtbar, bis das naechste
        # Mal auf "Aufrufe laden" geklickt wird (Klaerung mit dem Nutzer),
        # statt bei jedem Seiten-Neuladen wieder bei "-" zu starten.
        mock_update.assert_any_call("listing-1", {"last_known_views": 42})
        mock_update.assert_any_call("listing-2", {"last_known_views": 7})

    def test_ignores_empty_ids_entries(self):
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_listing_views", return_value={}) as mock_views, \
             patch("main.db.list_ebay_listings") as mock_list, \
             patch("main.db.update_ebay_listing") as mock_update:
            client.get("/api/ebay/listings/views?listing_ids=111,,222,")
        mock_views.assert_called_once_with("tok", ["111", "222"])
        mock_list.assert_not_called()
        mock_update.assert_not_called()

    def test_skips_persisting_for_a_listing_id_with_no_local_match(self):
        # z.B. ein Angebot, das inzwischen lokal geloescht wurde, aber bei
        # eBay noch existiert - darf den Aufrufe-laden-Aufruf nicht crashen.
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_listing_views", return_value={"999": 3}), \
             patch("main.db.list_ebay_listings", return_value=[]), \
             patch("main.db.update_ebay_listing") as mock_update:
            response = client.get("/api/ebay/listings/views?listing_ids=999")
        self.assertEqual(response.status_code, 200)
        mock_update.assert_not_called()

    def test_returns_401_when_not_authorized(self):
        with patch("main.ebay_client.get_access_token",
                    side_effect=ebay_client.EbayNotAuthorizedError("nicht verbunden")):
            response = client.get("/api/ebay/listings/views?listing_ids=111")
        self.assertEqual(response.status_code, 401)

    def test_returns_502_on_api_error(self):
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_listing_views",
                   side_effect=ebay_client.EbayApiError("insufficient_scope")):
            response = client.get("/api/ebay/listings/views?listing_ids=111")
        self.assertEqual(response.status_code, 502)


class GetEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.get("/api/ebay/listings/does-not-exist")
        self.assertEqual(response.status_code, 404)

    def test_returns_listing_with_card_summary(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}):
            response = client.get("/api/ebay/listings/listing-1")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["card"]["title"], "Musterkarte")


class RefreshEbayListingSinceEndpointTests(unittest.TestCase):
    def _imported(self, **overrides):
        defaults = {"status": "Veroeffentlicht", "ebay_offer_id": "", "ebay_listing_id": "110412345678"}
        defaults.update(overrides)
        return _listing(**defaults)

    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.post("/api/ebay/listings/does-not-exist/refresh-listing-since")
        self.assertEqual(response.status_code, 404)

    def test_works_for_non_imported_listing_with_ebay_listing_id(self):
        # Nicht nur importierte Angebote koennen ein falsches listing_since
        # haben - die Backfill-Migration fuellte es fuer alle vor der
        # listing_since-Einfuehrung veroeffentlichten Angebote nur mit
        # published_at (siehe supabase/schema.sql), egal ob importiert oder
        # ueber die API eingestellt.
        with patch("main.db.get_ebay_listing", return_value=_listing(status="Veroeffentlicht", ebay_offer_id="offer-1", ebay_listing_id="110412345678")), \
             patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.get_item_by_legacy_id", return_value={"listing_since": "2026-01-15T10:00:00.000Z"}), \
             patch("main.db.update_ebay_listing", return_value=_listing(listing_since="2026-01-15T10:00:00.000Z")), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}):
            response = client.post("/api/ebay/listings/listing-1/refresh-listing-since")
        self.assertEqual(response.status_code, 200)

    def test_returns_400_when_no_ebay_listing_id(self):
        with patch("main.db.get_ebay_listing", return_value=self._imported(ebay_listing_id="")):
            response = client.post("/api/ebay/listings/listing-1/refresh-listing-since")
        self.assertEqual(response.status_code, 400)

    def test_returns_502_when_ebay_lookup_fails(self):
        with patch("main.db.get_ebay_listing", return_value=self._imported()), \
             patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.get_item_by_legacy_id", side_effect=ebay_client.EbayApiError("nope")):
            response = client.post("/api/ebay/listings/listing-1/refresh-listing-since")
        self.assertEqual(response.status_code, 502)

    def test_returns_422_when_ebay_has_no_creation_date(self):
        with patch("main.db.get_ebay_listing", return_value=self._imported()), \
             patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.get_item_by_legacy_id", return_value={"listing_since": None}):
            response = client.post("/api/ebay/listings/listing-1/refresh-listing-since")
        self.assertEqual(response.status_code, 422)

    def test_updates_listing_since_on_success(self):
        with patch("main.db.get_ebay_listing", return_value=self._imported()), \
             patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.get_item_by_legacy_id", return_value={"listing_since": "2026-01-15T10:00:00.000Z"}), \
             patch("main.db.update_ebay_listing", return_value=self._imported(listing_since="2026-01-15T10:00:00.000Z")) as mock_update, \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}):
            response = client.post("/api/ebay/listings/listing-1/refresh-listing-since")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["listing_since"], "2026-01-15T10:00:00.000Z")
        mock_update.assert_called_once_with("listing-1", {"listing_since": "2026-01-15T10:00:00.000Z"})


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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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

    def test_republish_does_not_reset_listing_since(self):
        # Eine reine Bearbeitung (z.B. Preisaenderung) eines bereits
        # laufenden Angebots ist kein Neu-Einstellen - listing_since (seit
        # wann laeuft GENAU DIESES eBay-Listing) darf dabei nicht angefasst
        # werden, nur published_at ("zuletzt aktualisiert").
        published = _listing(status="Veroeffentlicht", ebay_offer_id="offer-1", ebay_listing_id="L0")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.db.update_ebay_listing", return_value=published) as mock_update, \
             patch("main.db.get_card", return_value=_card(front_image_path="b1/1_front.jpg")), \
             patch("main.db.get_cards_by_ids", return_value=[_card(front_image_path="b1/1_front.jpg")]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.storage.public_url", return_value="https://img/x.jpg"), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={}), \
             patch("main.ebay_client.put_inventory_item"), \
             patch("main.ebay_client.update_offer"), \
             patch("main.ebay_client.publish_offer", return_value="L0"):
            response = client.patch("/api/ebay/listings/listing-1", json={"price": 12.0})
        self.assertEqual(response.status_code, 200)
        final_updates = mock_update.call_args_list[-1].args[1]
        self.assertNotIn("listing_since", final_updates)
        self.assertNotIn("last_auto_relisted_at", final_updates)
        self.assertIn("published_at", final_updates)

    def test_returns_409_for_imported_listing(self):
        # Importiert (siehe import_ebay_listing()): status "Veroeffentlicht",
        # aber ohne ebay_offer_id - die Inventory API kann so ein Angebot
        # nicht verwalten, ein PATCH darf daher weder lokal noch bei eBay
        # etwas aendern (siehe _is_externally_managed()).
        imported = _listing(status="Veroeffentlicht", ebay_offer_id="")
        with patch("main.db.get_ebay_listing", return_value=imported), \
             patch("main.db.update_ebay_listing") as mock_update:
            response = client.patch("/api/ebay/listings/listing-1", json={"price": 12.0})
        self.assertEqual(response.status_code, 409)
        mock_update.assert_not_called()


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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
        # Erstveroeffentlichung (Fixture-Default ebay_listing_id="") setzt
        # "seit wann laeuft dieses Listing" (listing_since) neu.
        self.assertIn("listing_since", updates)

    def test_returns_409_for_imported_listing(self):
        imported = _listing(status="Veroeffentlicht", ebay_offer_id="")
        with patch("main.db.get_ebay_listing", return_value=imported), \
             patch("main.ebay_client.put_inventory_item") as mock_put:
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 409)
        mock_put.assert_not_called()

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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.ebay_client.get_access_token", side_effect=ebay_client.EbayNotAuthorizedError("nicht verbunden")):
            response = client.post("/api/ebay/listings/listing-1/publish")
        self.assertEqual(response.status_code, 401)

    def test_scheduled_in_future_uses_app_mode_without_calling_ebay(self):
        with patch("main.db.get_ebay_listing", return_value=_listing()), \
             patch("main.db.update_ebay_listing", return_value=_listing(status="Geplant")) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw:
            response = client.post("/api/ebay/listings/listing-1/end")
        self.assertEqual(response.status_code, 200)
        mock_withdraw.assert_called_once_with("tok", "offer-1")
        mock_update.assert_called_once_with("listing-1", {"status": "Beendet"})

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

    def test_returns_409_for_imported_listing_without_calling_ebay(self):
        # Ein importiertes Angebot (kein ebay_offer_id, siehe
        # import_ebay_listing()) darf "Beenden" nicht stillschweigend nur
        # lokal auf "Beendet" setzen, waehrend es bei eBay weiterhin live
        # bleibt - siehe _is_externally_managed().
        imported = _listing(status="Veroeffentlicht", ebay_offer_id="")
        with patch("main.db.get_ebay_listing", return_value=imported), \
             patch("main.db.update_ebay_listing") as mock_update, \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw:
            response = client.post("/api/ebay/listings/listing-1/end")
        self.assertEqual(response.status_code, 409)
        mock_withdraw.assert_not_called()
        mock_update.assert_not_called()


class RelistEbayListingEndpointTests(unittest.TestCase):
    def test_returns_404_when_not_found(self):
        with patch("main.db.get_ebay_listing", return_value=None):
            response = client.post("/api/ebay/listings/does-not-exist/relist")
        self.assertEqual(response.status_code, 404)

    def test_returns_409_when_not_published(self):
        with patch("main.db.get_ebay_listing", return_value=_listing(status="Entwurf")):
            response = client.post("/api/ebay/listings/listing-1/relist")
        self.assertEqual(response.status_code, 409)

    def test_returns_409_for_imported_listing_without_calling_ebay(self):
        imported = _listing(status="Veroeffentlicht", ebay_offer_id="")
        with patch("main.db.get_ebay_listing", return_value=imported), \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw:
            response = client.post("/api/ebay/listings/listing-1/relist")
        self.assertEqual(response.status_code, 409)
        mock_withdraw.assert_not_called()

    def test_withdraws_and_republishes(self):
        published = _listing(status="Veroeffentlicht", ebay_offer_id="offer-1", ebay_listing_id="L1")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.db.update_ebay_listing", side_effect=lambda lid, updates: {**published, **updates}) as mock_update, \
             patch("main.db.get_card", return_value=_card()), \
             patch("main.db.get_cards_by_ids", return_value=[_card()]), \
             patch("main.db.manual_sale_info_by_card_id", return_value={}), \
             patch("main.db.price_research_by_card_ids", return_value={}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.withdraw_offer") as mock_withdraw, \
             patch("main.ebay_client.ensure_merchant_location", return_value="DCARDSLAB-DE"), \
             patch("main.ebay_client.get_listing_policies", return_value={}), \
             patch("main.ebay_client.put_inventory_item"), \
             patch("main.ebay_client.update_offer") as mock_update_offer, \
             patch("main.ebay_client.publish_offer", return_value="L2"):
            response = client.post("/api/ebay/listings/listing-1/relist")
        self.assertEqual(response.status_code, 200)
        mock_withdraw.assert_called_once_with("tok", "offer-1")
        mock_update_offer.assert_called_once()
        self.assertEqual(response.json()["status"], "Veroeffentlicht")
        # Neu-Einstellen setzt sowohl die "laeuft seit"-Anzeige (listing_since)
        # als auch das "Neu eingestellt"-Badge (last_auto_relisted_at, trotz
        # Namens auch fuer den manuellen Button) neu.
        updates = mock_update.call_args[0][1]
        self.assertIn("listing_since", updates)
        self.assertIn("last_auto_relisted_at", updates)

    def test_returns_502_when_withdraw_fails(self):
        published = _listing(status="Veroeffentlicht", ebay_offer_id="offer-1")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.withdraw_offer", side_effect=ebay_client.EbayApiError("eBay lehnt ab")):
            response = client.post("/api/ebay/listings/listing-1/relist")
        self.assertEqual(response.status_code, 502)

    def test_returns_401_when_not_authorized(self):
        published = _listing(status="Veroeffentlicht", ebay_offer_id="offer-1")
        with patch("main.db.get_ebay_listing", return_value=published), \
             patch("main.ebay_client.get_access_token", side_effect=ebay_client.EbayNotAuthorizedError("kein Token")):
            response = client.post("/api/ebay/listings/listing-1/relist")
        self.assertEqual(response.status_code, 401)


class RunAutoRelistOnceTests(unittest.TestCase):
    def test_does_nothing_when_globally_disabled(self):
        with patch("main.db.get_app_status", return_value={"auto_relist_enabled": False}), \
             patch("main.db.list_listings_due_for_auto_relist") as mock_due:
            result = main._run_auto_relist_once()
        self.assertEqual(result, 0)
        mock_due.assert_not_called()

    def test_relists_due_listings_and_marks_timestamp(self):
        due = [_listing(id="a", ebay_offer_id="offer-a"), _listing(id="b", ebay_offer_id="offer-b")]
        with patch("main.db.get_app_status", return_value={"auto_relist_enabled": True}), \
             patch("main.db.list_listings_due_for_auto_relist", return_value=due), \
             patch("main._relist_listing", return_value={}) as mock_relist, \
             patch("main.db.update_ebay_listing") as mock_update:
            result = main._run_auto_relist_once()
        self.assertEqual(result, 2)
        self.assertEqual(mock_relist.call_count, 2)
        self.assertEqual(mock_update.call_count, 2)
        for call in mock_update.call_args_list:
            self.assertIn("last_auto_relisted_at", call.args[1])

    def test_marks_timestamp_even_when_relist_fails(self):
        due = [_listing(id="a", ebay_offer_id="offer-a")]
        with patch("main.db.get_app_status", return_value={"auto_relist_enabled": True}), \
             patch("main.db.list_listings_due_for_auto_relist", return_value=due), \
             patch("main._relist_listing", side_effect=RuntimeError("eBay down")), \
             patch("main.db.update_ebay_listing") as mock_update:
            result = main._run_auto_relist_once()
        self.assertEqual(result, 0)
        mock_update.assert_called_once()
        self.assertIn("last_auto_relisted_at", mock_update.call_args.args[1])

    def test_survives_failure_loading_due_listings(self):
        with patch("main.db.get_app_status", return_value={"auto_relist_enabled": True}), \
             patch("main.db.list_listings_due_for_auto_relist", side_effect=RuntimeError("db down")):
            result = main._run_auto_relist_once()  # must not raise
        self.assertEqual(result, 0)


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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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

    def test_imported_listing_reports_error_without_calling_ebay(self):
        imported = _listing(status="Veroeffentlicht", ebay_offer_id="")
        with patch("main.db.get_ebay_listing", return_value=imported), \
             patch("main.ebay_client.put_inventory_item") as mock_put:
            response = client.post("/api/ebay/listings/publish-bulk", json={"listing_ids": ["listing-1"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["results"][0]["status"], "Fehler")
        mock_put.assert_not_called()


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
             patch("main.db.price_research_by_card_ids", return_value={}), \
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

    def test_reports_error_for_imported_listing_without_updating(self):
        imported = _listing(price=10.0, status="Veroeffentlicht", ebay_offer_id="")
        with patch("main.db.get_ebay_listing", return_value=imported), \
             patch("main.db.update_ebay_listing") as mock_update:
            response = client.post(
                "/api/ebay/listings/price-bulk",
                json={"listing_ids": ["listing-1"], "percent": -10},
            )
        self.assertEqual(response.status_code, 200)
        self.assertIn("error", response.json()["results"][0])
        mock_update.assert_not_called()


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
        self.assertEqual(response.json()["results"][0]["title"], "Musterkarte")
        self.assertFalse(response.json()["results"][0]["is_own_listing"])
        mock_search.assert_called_once_with("app-tok", "Musterkarte")

    def test_marks_own_listing_when_item_id_matches(self):
        results = [
            {"title": "Musterkarte", "price": 12.5, "item_id": "v1|110412345678|0"},
            {"title": "Musterkarte", "price": 15.0, "item_id": "v1|999999999999|0"},
        ]
        with patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.search_active_listings", return_value=results):
            response = client.get("/api/ebay/price-research?q=Musterkarte&own_listing_id=110412345678")
        body = response.json()["results"]
        self.assertTrue(body[0]["is_own_listing"])
        self.assertFalse(body[1]["is_own_listing"])

    def test_marks_nothing_as_own_when_no_own_listing_id_given(self):
        results = [{"title": "Musterkarte", "price": 12.5, "item_id": "v1|110412345678|0"}]
        with patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.search_active_listings", return_value=results):
            response = client.get("/api/ebay/price-research?q=Musterkarte")
        self.assertFalse(response.json()["results"][0]["is_own_listing"])

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

    def test_does_not_mistake_season_year_for_a_print_run(self):
        # season_year kann laut ai_card_recognition.py Formate wie "2024/25"
        # oder "24/25" annehmen - das sieht syntaktisch wie eine Auflage
        # ("/50") aus, ist aber keine. Ohne Sonderbehandlung wuerden nahezu
        # alle echten Angebote (die die Saison im Titel haben) ausgeschlossen,
        # nur weil die Suchanfrage die Saison anders/gar nicht formatiert -
        # genau das vom Nutzer gemeldete "nur 100%-Titeltreffer"-Problem.
        results = [
            {"title": "Lionel Messi 2024/25 Panini", "price": 5.0},
            {"title": "L. Messi 24/25 Panini", "price": 6.0},
            {"title": "Messi Panini Base", "price": 4.5},
            {"title": "Messi Panini /50", "price": 200.0},
            {"title": "Messi Panini Auto", "price": 300.0},
        ]
        with patch("main.ebay_client.get_application_access_token", return_value="app-tok"), \
             patch("main.ebay_client.search_active_listings", return_value=results):
            response = client.get("/api/ebay/price-research?q=Lionel+Messi+2024+Panini")
        titles = [r["title"] for r in response.json()["results"]]
        self.assertEqual(titles, ["Lionel Messi 2024/25 Panini", "L. Messi 24/25 Panini", "Messi Panini Base"])


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

    def test_matches_imported_listing_by_legacy_item_id_when_sku_unknown(self):
        # Importierte Angebote (import_ebay_listing()) haben nie eine echte
        # SKU auf eBay selbst - der Sync muss sie trotzdem per eBays eigener
        # Artikelnummer (legacyItemId == unser ebay_listing_id) finden.
        imported_listing = _listing(id="listing-imported", sku="webapp-card-99", ebay_listing_id="123456789012")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "lineItems": [{
                "sku": "SOME-OTHER-SKU-NEVER-SET-BY-US", "legacyItemId": "123456789012",
                "lineItemId": "LI1", "quantity": 1, "total": {"value": "9.99"},
            }],
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.db.list_ebay_listings", return_value=[imported_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1"}) as mock_upsert, \
             patch("main.db.update_ebay_listing", return_value=imported_listing) as mock_update, \
             patch("main.db.zero_inventory_for_card"):
            response = client.post("/api/ebay/sync-sales")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"synced": 1, "skipped": 0})
        mock_update.assert_called_once_with("listing-imported", {"status": "Verkauft"})
        sale_fields = mock_upsert.call_args[0][0]
        self.assertEqual(sale_fields["listing_id"], "listing-imported")

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

    def test_captures_buyer_username(self):
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "buyer": {"username": "kartenfan99"},
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
        self.assertEqual(sale_fields["buyer_username"], "kartenfan99")

    def test_missing_buyer_defaults_username_to_empty_string(self):
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
        self.assertEqual(sale_fields["buyer_username"], "")

    def test_pulls_tracking_from_ebay_when_order_already_fulfilled(self):
        # Deckt den Fall ab, dass direkt im eBay Seller Hub versendet wurde
        # (statt ueber das Tool) - die Trackingdaten sollen trotzdem lokal
        # ankommen und die Karte automatisch als versendet markiert werden.
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "orderFulfillmentStatus": "FULFILLED",
            "lineItems": [{"sku": "webapp-card-1", "lineItemId": "LI1", "quantity": 1, "total": {"value": "9.99"}}],
        }]
        fulfillments = [{
            "lineItems": [{"lineItemId": "LI1"}],
            "trackingNumber": "1Z999AA10123456784", "shippingCarrierCode": "UPS",
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.ebay_client.list_shipping_fulfillments", return_value=fulfillments) as mock_fulfillments, \
             patch("main.db.list_ebay_listings", return_value=[matched_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1", "card_id": "card-1"}) as mock_upsert, \
             patch("main.db.update_ebay_listing", return_value=matched_listing), \
             patch("main.db.zero_inventory_for_card"), \
             patch("main.db.update_card") as mock_update_card:
            client.post("/api/ebay/sync-sales")
        mock_fulfillments.assert_called_once_with("tok", "O1")
        sale_fields = mock_upsert.call_args[0][0]
        self.assertEqual(sale_fields["tracking_number"], "1Z999AA10123456784")
        self.assertEqual(sale_fields["shipping_carrier"], "UPS")
        mock_update_card.assert_called_once_with("card-1", {"shipped": True})

    def test_does_not_look_up_fulfillments_when_order_not_yet_fulfilled(self):
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "orderFulfillmentStatus": "NOT_STARTED",
            "lineItems": [{"sku": "webapp-card-1", "lineItemId": "LI1", "quantity": 1, "total": {"value": "9.99"}}],
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.ebay_client.list_shipping_fulfillments") as mock_fulfillments, \
             patch("main.db.list_ebay_listings", return_value=[matched_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1"}) as mock_upsert, \
             patch("main.db.update_ebay_listing", return_value=matched_listing), \
             patch("main.db.zero_inventory_for_card"):
            client.post("/api/ebay/sync-sales")
        mock_fulfillments.assert_not_called()
        sale_fields = mock_upsert.call_args[0][0]
        self.assertNotIn("tracking_number", sale_fields)

    def test_fulfilled_order_without_matching_line_item_fulfillment_still_marks_shipped(self):
        # Deckt z. B. eine Sendung ohne Sendungsverfolgung ab (Warenpost/
        # Brief) - eBay fuehrt die Bestellung trotzdem als FULFILLED, auch
        # ohne passenden Fulfillment-Datensatz, also gilt die Karte als
        # versendet, nur ohne Trackingnummer.
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "orderFulfillmentStatus": "FULFILLED",
            "lineItems": [{"sku": "webapp-card-1", "lineItemId": "LI1", "quantity": 1, "total": {"value": "9.99"}}],
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.ebay_client.list_shipping_fulfillments", return_value=[]), \
             patch("main.db.list_ebay_listings", return_value=[matched_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1"}) as mock_upsert, \
             patch("main.db.update_ebay_listing", return_value=matched_listing), \
             patch("main.db.zero_inventory_for_card"), \
             patch("main.db.update_card") as mock_update_card:
            client.post("/api/ebay/sync-sales")
        sale_fields = mock_upsert.call_args[0][0]
        self.assertNotIn("tracking_number", sale_fields)
        mock_update_card.assert_called_once_with("card-1", {"shipped": True})

    def test_fulfillment_lookup_failure_does_not_fail_the_sync(self):
        # Isolation principle, same as the existing inventory-zeroing test
        # above - a Fehler beim Nachladen der Trackingdaten darf den
        # restlichen Sync (inkl. des eigentlichen Verkaufs-Upserts) nicht
        # verhindern.
        matched_listing = _listing(id="listing-1", sku="webapp-card-1")
        orders = [{
            "orderId": "O1", "creationDate": "2026-08-27T10:00:00Z",
            "orderFulfillmentStatus": "FULFILLED",
            "lineItems": [{"sku": "webapp-card-1", "lineItemId": "LI1", "quantity": 1, "total": {"value": "9.99"}}],
        }]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.latest_sale_sync_cursor", return_value=None), \
             patch("main.ebay_client.get_orders", return_value=orders), \
             patch("main.ebay_client.list_shipping_fulfillments", side_effect=ebay_client.EbayApiError("boom")), \
             patch("main.db.list_ebay_listings", return_value=[matched_listing]), \
             patch("main.db.upsert_ebay_sale", return_value={"id": "sale-1"}) as mock_upsert, \
             patch("main.db.update_ebay_listing", return_value=matched_listing), \
             patch("main.db.zero_inventory_for_card"), \
             patch("main.db.update_card"):
            response = client.post("/api/ebay/sync-sales")
        self.assertEqual(response.status_code, 200)
        sale_fields = mock_upsert.call_args[0][0]
        self.assertNotIn("tracking_number", sale_fields)


class NotifyNewSalesTests(unittest.TestCase):
    def test_sends_email_when_enabled_and_configured(self):
        settings = {
            "notify_on_sale": True, "smtp_host": "smtp.example.com", "smtp_port": 587,
            "smtp_from": "a@b.de", "smtp_to": "me@b.de",
        }
        newly_synced = [{"card_id": "c1", "listing": {"title": "Messi"}, "sale_fields": {"gross_price": 9.99}}]
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email") as mock_send:
            main._notify_new_sales(newly_synced)
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], settings)
        self.assertIn("1 neuer eBay-Verkauf", args[1])

    def test_does_nothing_when_notifications_disabled(self):
        settings = {"notify_on_sale": False, "smtp_host": "smtp.example.com"}
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email") as mock_send:
            main._notify_new_sales([{"card_id": "c1", "listing": {}, "sale_fields": {}}])
        mock_send.assert_not_called()

    def test_does_nothing_when_no_settings_row(self):
        with patch("main.db.get_app_status", return_value=None), \
             patch("main.email_notify.send_email") as mock_send:
            main._notify_new_sales([{"card_id": "c1", "listing": {}, "sale_fields": {}}])
        mock_send.assert_not_called()

    def test_smtp_failure_does_not_raise(self):
        settings = {"notify_on_sale": True, "smtp_host": "smtp.example.com"}
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email", side_effect=OSError("boom")):
            main._notify_new_sales([{"card_id": "c1", "listing": {}, "sale_fields": {}}])  # must not raise

    def test_app_status_fetch_failure_does_not_raise(self):
        with patch("main.db.get_app_status", side_effect=RuntimeError("db down")):
            main._notify_new_sales([{"card_id": "c1", "listing": {}, "sale_fields": {}}])  # must not raise

    def test_sends_push_when_enabled_and_configured(self):
        settings = {"notify_on_sale": True}
        newly_synced = [{"card_id": "c1", "listing": {"title": "Messi"}, "sale_fields": {"gross_price": 9.99}}]
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email"), \
             patch("main.push_notify.is_configured", return_value=True), \
             patch("main.push_notify.send_push_to_all") as mock_push:
            main._notify_new_sales(newly_synced)
        mock_push.assert_called_once()
        args = mock_push.call_args[0]
        self.assertIn("1 neuer eBay-Verkauf", args[0])
        self.assertEqual(mock_push.call_args[1]["url"], "/ebay.html")

    def test_skips_push_when_not_configured(self):
        settings = {"notify_on_sale": True}
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email"), \
             patch("main.push_notify.is_configured", return_value=False), \
             patch("main.push_notify.send_push_to_all") as mock_push:
            main._notify_new_sales([{"card_id": "c1", "listing": {}, "sale_fields": {}}])
        mock_push.assert_not_called()

    def test_push_failure_does_not_raise(self):
        settings = {"notify_on_sale": True}
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email"), \
             patch("main.push_notify.is_configured", return_value=True), \
             patch("main.push_notify.send_push_to_all", side_effect=RuntimeError("push down")):
            main._notify_new_sales([{"card_id": "c1", "listing": {}, "sale_fields": {}}])  # must not raise

    def test_push_failure_does_not_prevent_email(self):
        settings = {"notify_on_sale": True, "smtp_host": "smtp.example.com"}
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email") as mock_send, \
             patch("main.push_notify.is_configured", side_effect=RuntimeError("boom")):
            main._notify_new_sales([{"card_id": "c1", "listing": {}, "sale_fields": {}}])
        mock_send.assert_called_once()


class IsReminderDigestDueTests(unittest.TestCase):
    def test_due_when_never_sent(self):
        with patch("main.db.get_app_status", return_value={}):
            self.assertTrue(main._is_reminder_digest_due())

    def test_due_when_no_status_row(self):
        with patch("main.db.get_app_status", return_value=None):
            self.assertTrue(main._is_reminder_digest_due())

    def test_not_due_when_sent_recently(self):
        recent = main.datetime.now(main.timezone.utc).isoformat()
        with patch("main.db.get_app_status", return_value={"last_reminder_email_sent_at": recent}):
            self.assertFalse(main._is_reminder_digest_due())

    def test_due_when_sent_over_24h_ago(self):
        old = (main.datetime.now(main.timezone.utc) - main.timedelta(hours=25)).isoformat()
        with patch("main.db.get_app_status", return_value={"last_reminder_email_sent_at": old}):
            self.assertTrue(main._is_reminder_digest_due())


class SendReminderDigestIfDueTests(unittest.TestCase):
    def test_does_nothing_when_not_due(self):
        with patch("main._is_reminder_digest_due", return_value=False), \
             patch("main.db.record_reminder_email_sent") as mock_record, \
             patch("main.email_notify.send_email") as mock_send:
            main._send_reminder_digest_if_due()
        mock_record.assert_not_called()
        mock_send.assert_not_called()

    def test_stamps_timestamp_even_when_notifications_disabled(self):
        with patch("main._is_reminder_digest_due", return_value=True), \
             patch("main.db.record_reminder_email_sent") as mock_record, \
             patch("main.db.get_app_status", return_value={"notify_on_reminders": False}), \
             patch("main.email_notify.send_email") as mock_send:
            main._send_reminder_digest_if_due()
        mock_record.assert_called_once()
        mock_send.assert_not_called()

    def test_does_nothing_when_nothing_due(self):
        settings = {"notify_on_reminders": True}
        with patch("main._is_reminder_digest_due", return_value=True), \
             patch("main.db.record_reminder_email_sent"), \
             patch("main.db.get_app_status", return_value=settings), \
             patch("main.db.list_due_reminders", return_value=[]), \
             patch("main.db.list_stale_unsold_listings", return_value=[]), \
             patch("main.db.list_stale_wishlist_items", return_value=[]), \
             patch("main.email_notify.send_email") as mock_send:
            main._send_reminder_digest_if_due()
        mock_send.assert_not_called()

    def test_sends_digest_when_due_reminders_exist(self):
        settings = {"notify_on_reminders": True}
        due = [{"id": "r1", "card_id": "c1", "note": "x", "due_date": "2026-09-01"}]
        with patch("main._is_reminder_digest_due", return_value=True), \
             patch("main.db.record_reminder_email_sent"), \
             patch("main.db.get_app_status", return_value=settings), \
             patch("main.db.list_due_reminders", return_value=due), \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "c1", "title": "Karte 1"}]), \
             patch("main.db.list_stale_unsold_listings", return_value=[]), \
             patch("main.db.list_stale_wishlist_items", return_value=[]), \
             patch("main.email_notify.send_email") as mock_send:
            main._send_reminder_digest_if_due()
        mock_send.assert_called_once()
        args = mock_send.call_args[0]
        self.assertEqual(args[0], settings)
        self.assertIn("Karte 1", args[2])

    def test_smtp_failure_does_not_raise(self):
        settings = {"notify_on_reminders": True}
        due = [{"id": "r1", "card_id": "c1", "note": "x", "due_date": "2026-09-01"}]
        with patch("main._is_reminder_digest_due", return_value=True), \
             patch("main.db.record_reminder_email_sent"), \
             patch("main.db.get_app_status", return_value=settings), \
             patch("main.db.list_due_reminders", return_value=due), \
             patch("main.db.get_cards_by_ids", return_value=[{"id": "c1", "title": "Karte 1"}]), \
             patch("main.db.list_stale_unsold_listings", return_value=[]), \
             patch("main.db.list_stale_wishlist_items", return_value=[]), \
             patch("main.email_notify.send_email", side_effect=OSError("boom")):
            main._send_reminder_digest_if_due()  # must not raise

    def test_uses_configured_thresholds(self):
        settings = {
            "notify_on_reminders": True, "stale_listing_min_days": 30,
            "stale_listing_check_days": 10, "stale_wishlist_min_days": 14,
        }
        with patch("main._is_reminder_digest_due", return_value=True), \
             patch("main.db.record_reminder_email_sent"), \
             patch("main.db.get_app_status", return_value=settings), \
             patch("main.db.list_due_reminders", return_value=[]), \
             patch("main.db.list_stale_unsold_listings", return_value=[]) as mock_listings, \
             patch("main.db.list_stale_wishlist_items", return_value=[]) as mock_wishlist, \
             patch("main.email_notify.send_email"):
            main._send_reminder_digest_if_due()
        mock_listings.assert_called_once_with(30, 10)
        mock_wishlist.assert_called_once_with(14)


class SyncEbayReturnsOnceTests(unittest.TestCase):
    def test_marks_sale_refunded_when_return_has_refund(self):
        returns = [{"returnId": "R1", "orderId": "O1", "refundInfo": {"refunds": [{"amount": {"value": "9.99"}}]}}]
        sale = {"id": "sale-1", "ebay_order_id": "O1", "refunded": False}
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.get_app_status", return_value={}), \
             patch("main.ebay_client.get_return_requests", return_value=returns), \
             patch("main.db.get_ebay_sale_by_order_id", return_value=sale), \
             patch("main.db.update_ebay_sale") as mock_update:
            checked, matched = main._sync_ebay_returns_once()
        self.assertEqual((checked, matched), (1, 1))
        mock_update.assert_called_once_with("sale-1", {"refunded": True})

    def test_skips_return_without_refund_info(self):
        returns = [{"returnId": "R1", "orderId": "O1", "refundInfo": {"refunds": []}}]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.get_app_status", return_value={}), \
             patch("main.ebay_client.get_return_requests", return_value=returns), \
             patch("main.db.get_ebay_sale_by_order_id") as mock_get_sale, \
             patch("main.db.update_ebay_sale") as mock_update:
            checked, matched = main._sync_ebay_returns_once()
        self.assertEqual((checked, matched), (1, 0))
        mock_get_sale.assert_not_called()
        mock_update.assert_not_called()

    def test_skips_return_with_no_matching_sale(self):
        returns = [{"returnId": "R1", "orderId": "O1", "refundInfo": {"refunds": [{"amount": {"value": "9.99"}}]}}]
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.get_app_status", return_value={}), \
             patch("main.ebay_client.get_return_requests", return_value=returns), \
             patch("main.db.get_ebay_sale_by_order_id", return_value=None), \
             patch("main.db.update_ebay_sale") as mock_update:
            checked, matched = main._sync_ebay_returns_once()
        self.assertEqual((checked, matched), (1, 0))
        mock_update.assert_not_called()

    def test_skips_sale_already_marked_refunded(self):
        returns = [{"returnId": "R1", "orderId": "O1", "refundInfo": {"refunds": [{"amount": {"value": "9.99"}}]}}]
        sale = {"id": "sale-1", "ebay_order_id": "O1", "refunded": True}
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.get_app_status", return_value={}), \
             patch("main.ebay_client.get_return_requests", return_value=returns), \
             patch("main.db.get_ebay_sale_by_order_id", return_value=sale), \
             patch("main.db.update_ebay_sale") as mock_update:
            checked, matched = main._sync_ebay_returns_once()
        self.assertEqual((checked, matched), (1, 0))
        mock_update.assert_not_called()

    def test_a_failing_return_does_not_abort_the_batch(self):
        returns = [
            {"returnId": "R1", "orderId": "O1", "refundInfo": {"refunds": [{"amount": {"value": "9.99"}}]}},
            {"returnId": "R2", "orderId": "O2", "refundInfo": {"refunds": [{"amount": {"value": "5.00"}}]}},
        ]
        sale2 = {"id": "sale-2", "ebay_order_id": "O2", "refunded": False}
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.get_app_status", return_value={}), \
             patch("main.ebay_client.get_return_requests", return_value=returns), \
             patch("main.db.get_ebay_sale_by_order_id", side_effect=[RuntimeError("db down"), sale2]), \
             patch("main.db.update_ebay_sale") as mock_update:
            checked, matched = main._sync_ebay_returns_once()  # must not raise
        self.assertEqual((checked, matched), (2, 1))
        mock_update.assert_called_once_with("sale-2", {"refunded": True})

    def test_uses_last_returns_sync_at_as_since(self):
        with patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.db.get_app_status", return_value={"last_returns_sync_at": "2026-09-01T00:00:00+00:00"}), \
             patch("main.ebay_client.get_return_requests", return_value=[]) as mock_get_returns:
            main._sync_ebay_returns_once()
        mock_get_returns.assert_called_once_with("tok", "2026-09-01T00:00:00+00:00")


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


class SubmitEbaySaleTrackingEndpointTests(unittest.TestCase):
    def _sale(self, **overrides):
        sale = {
            "id": "sale-1", "card_id": "card-1", "ebay_order_id": "O1",
            "ebay_line_item_id": "LI1", "quantity": 1,
        }
        sale.update(overrides)
        return sale

    def test_submits_to_ebay_saves_tracking_and_marks_card_shipped(self):
        with patch("main.db.get_ebay_sale", return_value=self._sale()), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.submit_shipping_fulfillment", return_value={}) as mock_submit, \
             patch("main.db.update_ebay_sale", return_value={"id": "sale-1", "tracking_number": "1Z999", "shipping_carrier": "UPS"}), \
             patch("main.db.update_card") as mock_update_card:
            response = client.post(
                "/api/ebay/sales/sale-1/submit-tracking",
                json={"tracking_number": "1Z999", "shipping_carrier": "UPS"},
            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["tracking_number"], "1Z999")
        mock_submit.assert_called_once()
        args = mock_submit.call_args[0]
        self.assertEqual(args[:5], ("tok", "O1", "LI1", 1, "1Z999"))
        mock_update_card.assert_called_once_with("card-1", {"shipped": True})

    def test_returns_404_when_sale_not_found(self):
        with patch("main.db.get_ebay_sale", return_value=None):
            response = client.post(
                "/api/ebay/sales/does-not-exist/submit-tracking",
                json={"tracking_number": "1Z999", "shipping_carrier": "UPS"},
            )
        self.assertEqual(response.status_code, 404)

    def test_returns_400_when_tracking_number_missing(self):
        with patch("main.db.get_ebay_sale", return_value=self._sale()):
            response = client.post(
                "/api/ebay/sales/sale-1/submit-tracking",
                json={"tracking_number": "", "shipping_carrier": "UPS"},
            )
        self.assertEqual(response.status_code, 400)

    def test_returns_502_on_ebay_api_error(self):
        with patch("main.db.get_ebay_sale", return_value=self._sale()), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.submit_shipping_fulfillment", side_effect=ebay_client.EbayApiError("boom")):
            response = client.post(
                "/api/ebay/sales/sale-1/submit-tracking",
                json={"tracking_number": "1Z999", "shipping_carrier": "UPS"},
            )
        self.assertEqual(response.status_code, 502)

    def test_returns_401_when_not_authorized(self):
        with patch("main.db.get_ebay_sale", return_value=self._sale()), \
             patch("main.ebay_client.get_access_token", side_effect=ebay_client.EbayNotAuthorizedError("nope")):
            response = client.post(
                "/api/ebay/sales/sale-1/submit-tracking",
                json={"tracking_number": "1Z999", "shipping_carrier": "UPS"},
            )
        self.assertEqual(response.status_code, 401)


class EbaySaleShippingAddressEndpointTests(unittest.TestCase):
    def test_returns_ship_to_address(self):
        order = {
            "fulfillmentStartInstructions": [
                {"shippingStep": {"shipTo": {"fullName": "Max Mustermann", "contactAddress": {"city": "Berlin"}}}}
            ]
        }
        with patch("main.db.get_ebay_sale", return_value={"id": "sale-1", "ebay_order_id": "O1"}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value=order):
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["fullName"], "Max Mustermann")

    def test_returns_404_when_sale_not_found(self):
        with patch("main.db.get_ebay_sale", return_value=None):
            response = client.get("/api/ebay/sales/does-not-exist/shipping-address")
        self.assertEqual(response.status_code, 404)

    def test_returns_404_when_no_ship_to_in_order(self):
        with patch("main.db.get_ebay_sale", return_value={"id": "sale-1", "ebay_order_id": "O1"}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value={"fulfillmentStartInstructions": []}):
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        self.assertEqual(response.status_code, 404)

    def test_returns_502_on_ebay_api_error(self):
        with patch("main.db.get_ebay_sale", return_value={"id": "sale-1", "ebay_order_id": "O1"}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", side_effect=ebay_client.EbayApiError("boom")):
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        self.assertEqual(response.status_code, 502)

    def test_includes_buyer_username_and_backfills_it(self):
        order = {
            "buyer": {"username": "kartenfan99"},
            "fulfillmentStartInstructions": [
                {"shippingStep": {"shipTo": {"fullName": "Max Mustermann", "contactAddress": {"city": "Berlin"}}}}
            ],
        }
        with patch("main.db.get_ebay_sale", return_value={"id": "sale-1", "ebay_order_id": "O1"}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value=order), \
             patch("main.db.set_ebay_sale_buyer_username") as mock_backfill:
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["buyer_username"], "kartenfan99")
        mock_backfill.assert_called_once_with("sale-1", "kartenfan99")

    def test_missing_buyer_defaults_username_to_empty_string_and_skips_backfill(self):
        order = {
            "fulfillmentStartInstructions": [
                {"shippingStep": {"shipTo": {"fullName": "Max Mustermann"}}}
            ],
        }
        with patch("main.db.get_ebay_sale", return_value={"id": "sale-1", "ebay_order_id": "O1"}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value=order), \
             patch("main.db.set_ebay_sale_buyer_username") as mock_backfill:
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["buyer_username"], "")
        mock_backfill.assert_not_called()

    def test_backfill_failure_does_not_fail_the_request(self):
        order = {
            "buyer": {"username": "kartenfan99"},
            "fulfillmentStartInstructions": [
                {"shippingStep": {"shipTo": {"fullName": "Max Mustermann"}}}
            ],
        }
        with patch("main.db.get_ebay_sale", return_value={"id": "sale-1", "ebay_order_id": "O1"}), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value=order), \
             patch("main.db.set_ebay_sale_buyer_username", side_effect=RuntimeError("db down")):
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["buyer_username"], "kartenfan99")

    def _order_with_ship_to(self, **overrides):
        order = {
            "orderFulfillmentStatus": "FULFILLED",
            "fulfillmentStartInstructions": [
                {"shippingStep": {"shipTo": {"fullName": "Max Mustermann"}}}
            ],
        }
        order.update(overrides)
        return order

    def test_pulls_and_persists_tracking_when_order_is_fulfilled(self):
        # Deckt den Fall ab, dass der Verkauf schon vor dieser Funktion
        # synchronisiert wurde oder direkt im eBay Seller Hub versendet
        # wurde - die Sendungsverfolgung soll nicht erst auf den naechsten
        # Hintergrund-Sync warten muessen, wenn der Nutzer aktiv nachlaedt.
        sale = {"id": "sale-1", "ebay_order_id": "O1", "ebay_line_item_id": "LI1", "card_id": "card-1"}
        fulfillments = [{
            "lineItems": [{"lineItemId": "LI1"}],
            "trackingNumber": "1Z999AA10123456784", "shippingCarrierCode": "UPS",
        }]
        with patch("main.db.get_ebay_sale", return_value=sale), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value=self._order_with_ship_to()), \
             patch("main.ebay_client.list_shipping_fulfillments", return_value=fulfillments), \
             patch("main.db.update_ebay_sale") as mock_update_sale, \
             patch("main.db.update_card") as mock_update_card:
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["tracking_number"], "1Z999AA10123456784")
        self.assertEqual(body["shipping_carrier"], "UPS")
        self.assertTrue(body["shipped"])
        mock_update_sale.assert_called_once_with(
            "sale-1", {"tracking_number": "1Z999AA10123456784", "shipping_carrier": "UPS"},
        )
        mock_update_card.assert_called_once_with("card-1", {"shipped": True})

    def test_marks_shipped_even_without_a_matching_fulfillment(self):
        # Warenpost/Brief ohne Sendungsverfolgung - eBay fuehrt die
        # Bestellung trotzdem als FULFILLED.
        sale = {"id": "sale-1", "ebay_order_id": "O1", "ebay_line_item_id": "LI1", "card_id": "card-1"}
        with patch("main.db.get_ebay_sale", return_value=sale), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value=self._order_with_ship_to()), \
             patch("main.ebay_client.list_shipping_fulfillments", return_value=[]), \
             patch("main.db.update_ebay_sale") as mock_update_sale, \
             patch("main.db.update_card") as mock_update_card:
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        body = response.json()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(body["tracking_number"], "")
        self.assertTrue(body["shipped"])
        mock_update_sale.assert_not_called()
        mock_update_card.assert_called_once_with("card-1", {"shipped": True})

    def test_does_not_pull_tracking_when_order_not_yet_fulfilled(self):
        sale = {"id": "sale-1", "ebay_order_id": "O1", "ebay_line_item_id": "LI1", "card_id": "card-1"}
        order = self._order_with_ship_to(orderFulfillmentStatus="NOT_STARTED")
        with patch("main.db.get_ebay_sale", return_value=sale), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value=order), \
             patch("main.ebay_client.list_shipping_fulfillments") as mock_fulfillments, \
             patch("main.db.update_card") as mock_update_card:
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        body = response.json()
        self.assertFalse(body["shipped"])
        self.assertEqual(body["tracking_number"], "")
        mock_fulfillments.assert_not_called()
        mock_update_card.assert_not_called()

    def test_fulfillment_lookup_failure_still_marks_shipped(self):
        sale = {"id": "sale-1", "ebay_order_id": "O1", "ebay_line_item_id": "LI1", "card_id": "card-1"}
        with patch("main.db.get_ebay_sale", return_value=sale), \
             patch("main.ebay_client.get_access_token", return_value="tok"), \
             patch("main.ebay_client.get_order", return_value=self._order_with_ship_to()), \
             patch("main.ebay_client.list_shipping_fulfillments", side_effect=ebay_client.EbayApiError("boom")), \
             patch("main.db.update_card") as mock_update_card:
            response = client.get("/api/ebay/sales/sale-1/shipping-address")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["shipped"])
        mock_update_card.assert_called_once_with("card-1", {"shipped": True})


class ImportEbayListingEndpointTests(unittest.TestCase):
    def _item(self, **overrides):
        item = {
            "title": "Max Mustermann Rookie Card",
            "price": 12.5,
            "currency": "EUR",
            "condition": "Used",
            "item_web_url": "https://www.ebay.de/itm/110412345678",
            "image_urls": ["https://i.ebayimg.com/front.jpg", "https://i.ebayimg.com/back.jpg"],
        }
        item.update(overrides)
        return item

    def _photo_response(self):
        resp = MagicMock()
        resp.content = b"fake-image-bytes"
        resp.raise_for_status.side_effect = None
        return resp

    def _patch_all(self, **overrides):
        patches = {
            "main.ebay_client.get_application_access_token": MagicMock(return_value="app-tok"),
            "main.ebay_client.get_item_by_legacy_id": MagicMock(return_value=self._item()),
            "main.httpx.get": MagicMock(return_value=self._photo_response()),
            "main.recognize_card": MagicMock(return_value={"title": "Erkannter Titel"}),
            "main.db.find_duplicate_card": MagicMock(return_value=None),
            "main.db.create_batch": MagicMock(return_value="batch-1"),
            "main.storage.upload_image": MagicMock(side_effect=lambda b, p, side, path: f"{b}/{p}_{side}.jpg"),
            "main.storage.signed_url": MagicMock(side_effect=lambda object_path, **_: f"https://signed/{object_path}"),
            "main.db.insert_card": MagicMock(return_value={
                "id": "card-1", "card_no": 1, "title": "Erkannter Titel",
                "front_image_path": "batch-1/1_front.jpg", "back_image_path": "batch-1/1_back.jpg",
            }),
            "main.db.update_batch_status": MagicMock(),
            "main.db.create_inventory_item": MagicMock(),
            "main.db.create_ebay_listing": MagicMock(return_value=_listing(id="listing-1", card_id="card-1")),
            "main.db.update_ebay_listing": MagicMock(
                return_value=_listing(
                    id="listing-1", card_id="card-1", status="Veroeffentlicht",
                    ebay_listing_id="110412345678",
                )
            ),
            "main.db.get_cards_by_ids": MagicMock(return_value=[{"id": "card-1", "title": "Erkannter Titel"}]),
            "main.db.manual_sale_info_by_card_id": MagicMock(return_value={}),
            "main.db.price_research_by_card_ids": MagicMock(return_value={}),
        }
        patches.update(overrides)
        patchers = [patch(target, new) for target, new in patches.items()]
        for p in patchers:
            self.addCleanup(p.stop)
        return {target: p.start() for target, p in zip(patches, patchers)}

    def _post_import(self, item_id="110412345678"):
        return client.post("/api/ebay/import", data={"item_id": item_id})

    def test_returns_400_for_empty_item_id(self):
        self._patch_all()
        response = client.post("/api/ebay/import", data={"item_id": "  "})
        self.assertEqual(response.status_code, 400)

    def test_returns_502_when_ebay_lookup_fails(self):
        self._patch_all(**{
            "main.ebay_client.get_item_by_legacy_id": MagicMock(
                side_effect=ebay_client.EbayApiError("not found")
            ),
        })
        response = self._post_import()
        self.assertEqual(response.status_code, 502)

    def test_returns_422_when_fewer_than_two_photos(self):
        self._patch_all(**{
            "main.ebay_client.get_item_by_legacy_id": MagicMock(
                return_value=self._item(image_urls=["https://i.ebayimg.com/only.jpg"])
            ),
        })
        response = self._post_import()
        self.assertEqual(response.status_code, 422)

    def test_creates_card_and_ebay_listing_on_success(self):
        mocks = self._patch_all()
        response = self._post_import()
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["id"], "card-1")
        self.assertEqual(body["ebay_listing"]["status"], "Veroeffentlicht")
        self.assertEqual(body["ebay_listing"]["ebay_listing_id"], "110412345678")
        mocks["main.db.create_batch"].assert_called_once_with(card_count=1)
        mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "ok")
        self.assertEqual(mocks["main.storage.upload_image"].call_count, 2)

    def test_marks_listing_published_without_offer_id(self):
        # Kein ebay_offer_id - das ist das Signal fuer "extern verwaltet",
        # siehe Kommentar in main.py's import_ebay_listing().
        mocks = self._patch_all()
        self._post_import()
        update_call = mocks["main.db.update_ebay_listing"].call_args
        listing_id, fields = update_call.args
        self.assertEqual(listing_id, "listing-1")
        self.assertEqual(fields["status"], "Veroeffentlicht")
        self.assertEqual(fields["ebay_listing_id"], "110412345678")
        self.assertNotIn("ebay_offer_id", fields)

    def test_uses_ebays_listing_creation_date_as_listing_since(self):
        # Ohne das wuerde "Eingestellt am" faelschlich den Import-Zeitpunkt
        # statt eBays echtem Startdatum zeigen (Klaerung mit dem Nutzer,
        # anhand eines konkreten importierten Angebots aufgefallen).
        mocks = self._patch_all(**{
            "main.ebay_client.get_item_by_legacy_id": MagicMock(
                return_value=self._item(listing_since="2026-01-15T10:00:00.000Z")
            ),
        })
        self._post_import()
        update_call = mocks["main.db.update_ebay_listing"].call_args
        _, fields = update_call.args
        self.assertEqual(fields["listing_since"], "2026-01-15T10:00:00.000Z")

    def test_falls_back_to_import_time_when_ebay_has_no_listing_since(self):
        mocks = self._patch_all(**{
            "main.ebay_client.get_item_by_legacy_id": MagicMock(
                return_value=self._item(listing_since=None)
            ),
        })
        self._post_import()
        update_call = mocks["main.db.update_ebay_listing"].call_args
        _, fields = update_call.args
        self.assertEqual(fields["listing_since"], fields["published_at"])
        self.assertTrue(fields["listing_since"])

    def test_uses_ebay_title_when_recognition_finds_none(self):
        mocks = self._patch_all(**{
            "main.recognize_card": MagicMock(return_value={"title": ""}),
        })
        self._post_import()
        insert_fields = mocks["main.db.insert_card"].call_args.args[2]
        self.assertEqual(insert_fields["title"], "Max Mustermann Rookie Card")

    def test_creates_default_inventory_item(self):
        mocks = self._patch_all()
        self._post_import()
        mocks["main.db.create_inventory_item"].assert_called_once_with(
            "card-1", {"quantity": 1, "location": "", "notes": ""}
        )

    def test_attaches_possible_duplicate_when_found(self):
        mocks = self._patch_all(**{
            "main.db.find_duplicate_card": MagicMock(
                return_value={"id": "card-existing", "card_no": 7}
            ),
        })
        response = self._post_import()
        body = response.json()
        self.assertEqual(body["possible_duplicate"]["id"], "card-existing")

    def test_photo_download_failure_returns_502(self):
        import httpx as httpx_module
        self._patch_all(**{"main.httpx.get": MagicMock(side_effect=httpx_module.ConnectError("nope"))})
        response = self._post_import()
        self.assertEqual(response.status_code, 502)

    def test_insert_failure_marks_batch_failed_and_returns_502(self):
        mocks = self._patch_all(**{
            "main.db.insert_card": MagicMock(side_effect=RuntimeError("insert down")),
        })
        response = self._post_import()
        self.assertEqual(response.status_code, 502)
        mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "failed")


if __name__ == "__main__":
    unittest.main()
