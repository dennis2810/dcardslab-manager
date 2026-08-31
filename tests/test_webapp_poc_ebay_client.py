"""Tests for webapp-poc/ebay_client.py - eBay Sell API client. All HTTP is
mocked (unittest.mock), same depth as the rest of the project's tests."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import ebay_client  # noqa: E402


def _response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text or str(json_data or "")
    resp.raise_for_status.side_effect = None
    return resp


class GetAccessTokenTests(unittest.TestCase):
    def test_returns_token_on_success(self):
        with patch("ebay_client.httpx.get", return_value=_response(200, {"access_token": "tok-1"})):
            token = ebay_client.get_access_token()
        self.assertEqual(token, "tok-1")

    def test_raises_not_authorized_on_401(self):
        with patch("ebay_client.httpx.get", return_value=_response(401, {"authorized": False})):
            with self.assertRaises(ebay_client.EbayNotAuthorizedError):
                ebay_client.get_access_token()

    def test_raises_api_error_when_oauth_server_unreachable(self):
        import httpx
        with patch("ebay_client.httpx.get", side_effect=httpx.ConnectError("nope")):
            with self.assertRaises(ebay_client.EbayApiError):
                ebay_client.get_access_token()


class ConditionIdToEnumTests(unittest.TestCase):
    def test_maps_known_ids(self):
        self.assertEqual(ebay_client.condition_id_to_enum("4000"), "USED_VERY_GOOD")
        self.assertEqual(ebay_client.condition_id_to_enum("2750"), "LIKE_NEW")

    def test_passes_through_unknown_value(self):
        self.assertEqual(ebay_client.condition_id_to_enum("USED_GOOD"), "USED_GOOD")


class CardConditionDescriptorValueTests(unittest.TestCase):
    def test_maps_known_abbreviations_and_full_names_case_insensitively(self):
        self.assertEqual(ebay_client.card_condition_descriptor_value("NM"), "400010")
        self.assertEqual(ebay_client.card_condition_descriptor_value("near mint"), "400010")
        self.assertEqual(ebay_client.card_condition_descriptor_value("EX"), "400011")
        self.assertEqual(ebay_client.card_condition_descriptor_value("Very Good"), "400012")
        self.assertEqual(ebay_client.card_condition_descriptor_value("Poor"), "400013")

    def test_falls_back_to_near_mint_or_better_for_unknown_value(self):
        self.assertEqual(ebay_client.card_condition_descriptor_value("Mint"), "400010")

    def test_returns_empty_string_when_blank(self):
        self.assertEqual(ebay_client.card_condition_descriptor_value(None), "")
        self.assertEqual(ebay_client.card_condition_descriptor_value(""), "")


class GetListingPoliciesTests(unittest.TestCase):
    def _policy_response(self, list_field, id_field, policy_id):
        return _response(200, {list_field: [{id_field: policy_id}]})

    def test_returns_all_three_policy_ids(self):
        responses = [
            self._policy_response("fulfillmentPolicies", "fulfillmentPolicyId", "F1"),
            self._policy_response("paymentPolicies", "paymentPolicyId", "P1"),
            self._policy_response("returnPolicies", "returnPolicyId", "R1"),
        ]
        with patch("ebay_client.httpx.request", side_effect=responses):
            policies = ebay_client.get_listing_policies("tok")
        self.assertEqual(policies, {
            "fulfillmentPolicyId": "F1", "paymentPolicyId": "P1", "returnPolicyId": "R1",
        })

    def test_raises_german_error_when_a_policy_is_missing(self):
        responses = [
            self._policy_response("fulfillmentPolicies", "fulfillmentPolicyId", "F1"),
            _response(200, {"paymentPolicies": []}),
            self._policy_response("returnPolicies", "returnPolicyId", "R1"),
        ]
        with patch("ebay_client.httpx.request", side_effect=responses):
            with self.assertRaises(ebay_client.EbayApiError) as ctx:
                ebay_client.get_listing_policies("tok")
        self.assertIn("Zahlungs-Richtlinie", str(ctx.exception))


class EnsureMerchantLocationTests(unittest.TestCase):
    """eBay's Sell Inventory API requires every Offer to reference a
    merchantLocationKey - without one, createOffer/publishOffer fail with
    a confusing errorId 25002 mentioning a missing "Item.Country" (found
    live against the real sandbox). Ported from the desktop app's proven
    create-or-update flow in ebay-oauth-server/app.py."""

    def test_creates_location_when_it_does_not_exist_yet(self):
        with patch("ebay_client.httpx.request", return_value=_response(201, {})) as mock_request:
            key = ebay_client.ensure_merchant_location("tok")
        self.assertEqual(key, ebay_client.DEFAULT_MERCHANT_LOCATION_KEY)
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "POST")
        self.assertIn(f"/location/{ebay_client.DEFAULT_MERCHANT_LOCATION_KEY}", args[1])
        self.assertNotIn("update_location_details", args[1])
        self.assertEqual(kwargs["json"]["location"]["address"]["country"], "DE")
        self.assertEqual(kwargs["json"]["location"]["address"]["postalCode"], "51061")

    def test_falls_back_to_update_when_location_already_exists(self):
        already_exists = _response(400, {"errors": [{"message": "Location already exists."}]}, text="Location already exists.")
        with patch("ebay_client.httpx.request", side_effect=[already_exists, _response(200, {})]) as mock_request:
            key = ebay_client.ensure_merchant_location("tok")
        self.assertEqual(key, ebay_client.DEFAULT_MERCHANT_LOCATION_KEY)
        self.assertEqual(mock_request.call_count, 2)
        update_args, _ = mock_request.call_args_list[1]
        self.assertIn("update_location_details", update_args[1])

    def test_raises_on_unrelated_error(self):
        with patch("ebay_client.httpx.request", return_value=_response(400, {"errors": [{"message": "Invalid postal code."}]}, text="Invalid postal code.")):
            with self.assertRaises(ebay_client.EbayApiError):
                ebay_client.ensure_merchant_location("tok")


class PutInventoryItemTests(unittest.TestCase):
    def test_sends_title_and_image_url(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.put_inventory_item("tok", "sku-1", {"title": "Musterkarte"}, ["https://img/x.jpg"])
        args, kwargs = mock_request.call_args
        self.assertEqual(args[0], "PUT")
        self.assertIn("/inventory_item/sku-1", args[1])
        self.assertEqual(kwargs["json"]["product"]["title"], "Musterkarte")
        self.assertEqual(kwargs["json"]["product"]["imageUrls"], ["https://img/x.jpg"])

    def test_sends_front_and_back_image_urls(self):
        # Regression test: the app only ever sent the front photo here -
        # found live after the first successful production publish showed
        # up on eBay with the back of the card missing entirely.
        listing = {"title": "x"}
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.put_inventory_item("tok", "sku-1", listing, ["https://img/front.jpg", "https://img/back.jpg"])
        _, kwargs = mock_request.call_args
        self.assertEqual(
            kwargs["json"]["product"]["imageUrls"],
            ["https://img/front.jpg", "https://img/back.jpg"],
        )

    def test_raises_on_error_status(self):
        with patch("ebay_client.httpx.request", return_value=_response(400, text="bad request")):
            with self.assertRaises(ebay_client.EbayApiError):
                ebay_client.put_inventory_item("tok", "sku-1", {"title": "x"}, None)

    def test_sends_translated_condition(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.put_inventory_item("tok", "sku-1", {"title": "x", "condition_id": "4000"}, None)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"]["condition"], "USED_VERY_GOOD")

    def test_defaults_quantity_to_one_when_blank(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.put_inventory_item("tok", "sku-1", {"title": "x", "quantity": None}, None)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"]["availability"]["shipToLocationAvailability"]["quantity"], 1)

    def test_sends_aspects_as_item_specifics(self):
        # Regression test: found live against real eBay production - the
        # Inventory Item's product.aspects field is where eBay actually
        # reads Item Specifics (e.g. "Sportart") from. Without it, eBay
        # rejects the offer/publish with errorId 25002 "Das Artikelmerkmal
        # Sportart fehlt" even though ebay_listing.build_aspects()/
        # missing_aspects() already computed and locally validated the
        # exact same aspects - they were just never sent to eBay at all.
        # The sandbox apparently doesn't enforce this as strictly, which is
        # why this stayed hidden through all the sandbox testing.
        listing = {"title": "x", "aspects": {"Sportart": ["Fußball"], "Team / Verein": ["FC Test"]}}
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.put_inventory_item("tok", "sku-1", listing, None)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"]["product"]["aspects"], {"Sportart": ["Fußball"], "Team / Verein": ["FC Test"]})

    def test_sends_empty_aspects_dict_when_none_set(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.put_inventory_item("tok", "sku-1", {"title": "x"}, None)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"]["product"]["aspects"], {})

    def test_sends_card_condition_as_condition_descriptor(self):
        # Regression test: found live against real eBay production - the
        # top-level "condition" ConditionEnum alone isn't enough for
        # trading card categories (261328/183050). eBay separately requires
        # a "conditionDescriptors" entry for the "Kartenzustand"/"Card
        # Condition" descriptor (ID 40001), rejecting the Inventory Item
        # otherwise with errorId 25064 "Kartenzustand (40001) ist ein
        # erforderliches Feld".
        listing = {"title": "x", "condition": "NM"}
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.put_inventory_item("tok", "sku-1", listing, None)
        _, kwargs = mock_request.call_args
        self.assertEqual(
            kwargs["json"]["conditionDescriptors"],
            [{"name": "40001", "values": ["400010"]}],
        )

    def test_omits_condition_descriptors_when_condition_not_set(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.put_inventory_item("tok", "sku-1", {"title": "x"}, None)
        _, kwargs = mock_request.call_args
        self.assertNotIn("conditionDescriptors", kwargs["json"])


class CreateOfferTests(unittest.TestCase):
    def test_returns_offer_id(self):
        listing = {"category_id": "261328", "price": 12.5, "quantity": 1, "description": "desc"}
        with patch("ebay_client.httpx.request", return_value=_response(201, {"offerId": "offer-1"})):
            offer_id = ebay_client.create_offer("tok", "sku-1", listing)
        self.assertEqual(offer_id, "offer-1")

    def test_blanked_price_and_quantity_fall_back_instead_of_sending_none(self):
        # db._blank_numeric_to_none() turns a cleared price/quantity input
        # into None (not a missing key), so dict.get(key, default) alone
        # wouldn't apply the fallback - this is a regression test for that.
        listing = {"category_id": "261328", "price": None, "quantity": None, "description": "desc"}
        with patch("ebay_client.httpx.request", return_value=_response(201, {"offerId": "offer-1"})) as mock_request:
            ebay_client.create_offer("tok", "sku-1", listing)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"]["pricingSummary"]["price"]["value"], "0")
        self.assertEqual(kwargs["json"]["availableQuantity"], 1)

    def test_includes_merchant_location_key(self):
        listing = {"category_id": "261328", "price": 12.5, "quantity": 1, "description": "desc"}
        with patch("ebay_client.httpx.request", return_value=_response(201, {"offerId": "offer-1"})) as mock_request:
            ebay_client.create_offer("tok", "sku-1", listing)
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["json"]["merchantLocationKey"], ebay_client.DEFAULT_MERCHANT_LOCATION_KEY)

    def test_disables_catalog_product_matching(self):
        # Found live against the real sandbox: eBay's default
        # includeCatalogProductDetails=true tries to auto-match the offer to
        # an EPID catalog product, which is unreliable for niche categories
        # like single trading cards and was the actual cause of a generic
        # errorId 25002 "Systemfehler" failing publishOffer (GET on the
        # published offer showed includeCatalogProductDetails: true even
        # though this client never set it - eBay's own default). Explicitly
        # disabling it turns the offer into a plain non-catalog listing.
        listing = {"category_id": "261328", "price": 12.5, "quantity": 1, "description": "desc"}
        with patch("ebay_client.httpx.request", return_value=_response(201, {"offerId": "offer-1"})) as mock_request:
            ebay_client.create_offer("tok", "sku-1", listing)
        _, kwargs = mock_request.call_args
        self.assertIs(kwargs["json"]["includeCatalogProductDetails"], False)


class PublishOfferTests(unittest.TestCase):
    def test_publishes_without_scheduling_by_default(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {"listingId": "L1"})) as mock_request:
            listing_id = ebay_client.publish_offer("tok", "offer-1")
        self.assertEqual(listing_id, "L1")
        args, kwargs = mock_request.call_args
        self.assertIsNone(kwargs.get("json"))

    def test_raises_when_scheduled_and_native_not_supported(self):
        with patch("ebay_client.NATIVE_SCHEDULING_SUPPORTED", False):
            with self.assertRaises(ebay_client.EbayApiError):
                ebay_client.publish_offer("tok", "offer-1", scheduled_at="2026-09-01T10:00:00Z")

    def test_includes_scheduling_field_when_native_supported(self):
        with patch("ebay_client.NATIVE_SCHEDULING_SUPPORTED", True), \
             patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.publish_offer("tok", "offer-1", scheduled_at="2026-09-01T10:00:00Z")
        _, kwargs = mock_request.call_args
        self.assertIn("2026-09-01T10:00:00Z", str(kwargs["json"]))


class GetOfferTests(unittest.TestCase):
    def test_returns_offer_json(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {"offerId": "offer-1"})):
            offer = ebay_client.get_offer("tok", "offer-1")
        self.assertEqual(offer["offerId"], "offer-1")


class WithdrawOfferTests(unittest.TestCase):
    def test_posts_to_withdraw_path(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {})) as mock_request:
            ebay_client.withdraw_offer("tok", "offer-1")
        args, _ = mock_request.call_args
        self.assertEqual(args[0], "POST")
        self.assertIn("/offer/offer-1/withdraw", args[1])


class GetOrdersTests(unittest.TestCase):
    def test_returns_orders_list(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {"orders": [{"orderId": "O1"}]})):
            orders = ebay_client.get_orders("tok", "2026-08-01T00:00:00Z")
        self.assertEqual(orders, [{"orderId": "O1"}])

    def test_returns_empty_list_when_no_orders_key(self):
        with patch("ebay_client.httpx.request", return_value=_response(200, {})):
            orders = ebay_client.get_orders("tok", "2026-08-01T00:00:00Z")
        self.assertEqual(orders, [])

    def test_filter_query_is_passed_as_params_not_hand_built_into_the_url(self):
        # A raw f-string into the URL path leaves ":" in an ISO timestamp
        # unencoded, which eBay's API can reject - passing it as httpx
        # `params` lets httpx handle encoding correctly instead.
        with patch("ebay_client.httpx.request", return_value=_response(200, {"orders": []})) as mock_request:
            ebay_client.get_orders("tok", "2026-08-01T00:00:00Z")
        args, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["filter"], "creationdate:[2026-08-01T00:00:00Z..]")
        self.assertEqual(kwargs["params"]["limit"], "200")

    def test_normalizes_utc_offset_suffix_to_z(self):
        # Regression test: found live against real eBay production -
        # errorId 30810 "Invalid date format", with the "+00:00" suffix
        # datetime.isoformat() produces arriving at eBay as a literal space
        # ("...134065 00:00") instead. eBay's Fulfillment API filter wants
        # the "Z" suffix for UTC, not an explicit "+00:00" offset.
        with patch("ebay_client.httpx.request", return_value=_response(200, {"orders": []})) as mock_request:
            ebay_client.get_orders("tok", "2026-06-02T17:42:55.134065+00:00")
        _, kwargs = mock_request.call_args
        self.assertEqual(kwargs["params"]["filter"], "creationdate:[2026-06-02T17:42:55.134065Z..]")


if __name__ == "__main__":
    unittest.main()
