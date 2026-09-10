"""Tests for webapp-poc/ebay_scheduler.py - the app-side background
scheduler (fallback path, see design spec 'Scheduling')."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import ebay_scheduler  # noqa: E402


class RunOnceAppModeTests(unittest.TestCase):
    def test_calls_publish_fn_for_each_due_listing(self):
        due = [{"id": "l1"}, {"id": "l2"}]
        publish_fn = MagicMock()
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=due), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=[]):
            ebay_scheduler.run_once(publish_fn)
        self.assertEqual(publish_fn.call_count, 2)
        publish_fn.assert_any_call({"id": "l1"})
        publish_fn.assert_any_call({"id": "l2"})

    def test_uses_app_scheduling_mode_when_listing_due(self):
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=[]) as mock_list, \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=[]):
            ebay_scheduler.run_once(MagicMock())
        mock_list.assert_called_once_with("app")

    def test_a_failing_listing_does_not_abort_the_rest(self):
        due = [{"id": "l1"}, {"id": "l2"}]

        def publish_fn(listing):
            if listing["id"] == "l1":
                raise RuntimeError("eBay lehnt ab")

        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=due), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=[]):
            ebay_scheduler.run_once(publish_fn)  # must not raise


class RunOnceResilienceTests(unittest.TestCase):
    """run_forever() calls run_once() in an infinite loop - if fetching the
    due/native lists itself blows up (e.g. Supabase briefly unreachable),
    that must not kill the whole background task; the next interval should
    still get a chance to succeed."""

    def test_survives_list_due_scheduled_listings_failure(self):
        with patch("ebay_scheduler.db.list_due_scheduled_listings", side_effect=RuntimeError("db down")), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=[]) as mock_native:
            ebay_scheduler.run_once(MagicMock())  # must not raise
        mock_native.assert_called_once()

    def test_survives_list_native_scheduled_listings_failure(self):
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=[]), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", side_effect=RuntimeError("db down")):
            ebay_scheduler.run_once(MagicMock())  # must not raise


class RunOnceNativeModeTests(unittest.TestCase):
    def test_fetches_token_once_per_round_not_per_listing(self):
        native = [{"id": "l1", "ebay_offer_id": "offer-1"}, {"id": "l2", "ebay_offer_id": "offer-2"}]
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=[]), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=native), \
             patch("ebay_scheduler.ebay_client.get_access_token", return_value="tok") as mock_token, \
             patch("ebay_scheduler.ebay_client.get_offer", return_value={}), \
             patch("ebay_scheduler.db.update_ebay_listing"):
            ebay_scheduler.run_once(MagicMock())
        mock_token.assert_called_once()

    def test_skips_native_polling_this_round_when_token_fetch_fails(self):
        native = [{"id": "l1", "ebay_offer_id": "offer-1"}]
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=[]), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=native), \
             patch("ebay_scheduler.ebay_client.get_access_token", side_effect=RuntimeError("nicht verbunden")), \
             patch("ebay_scheduler.ebay_client.get_offer") as mock_get_offer:
            ebay_scheduler.run_once(MagicMock())  # must not raise
        mock_get_offer.assert_not_called()

    def test_does_not_fetch_token_when_nothing_to_poll(self):
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=[]), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=[]), \
             patch("ebay_scheduler.ebay_client.get_access_token") as mock_token:
            ebay_scheduler.run_once(MagicMock())
        mock_token.assert_not_called()

    def test_flips_status_when_offer_reports_live(self):
        native = [{"id": "l1", "ebay_offer_id": "offer-1"}]
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=[]), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=native), \
             patch("ebay_scheduler.ebay_client.get_access_token", return_value="tok"), \
             patch("ebay_scheduler.ebay_client.get_offer", return_value={"listingId": "L1"}), \
             patch("ebay_scheduler.db.update_ebay_listing") as mock_update:
            ebay_scheduler.run_once(MagicMock())
        mock_update.assert_called_once_with("l1", {"status": "Veroeffentlicht"})

    def test_leaves_status_untouched_when_offer_still_not_live(self):
        native = [{"id": "l1", "ebay_offer_id": "offer-1"}]
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=[]), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=native), \
             patch("ebay_scheduler.ebay_client.get_access_token", return_value="tok"), \
             patch("ebay_scheduler.ebay_client.get_offer", return_value={}), \
             patch("ebay_scheduler.db.update_ebay_listing") as mock_update:
            ebay_scheduler.run_once(MagicMock())
        mock_update.assert_not_called()

    def test_a_failing_status_check_does_not_abort_the_rest(self):
        native = [{"id": "l1", "ebay_offer_id": "offer-1"}, {"id": "l2", "ebay_offer_id": "offer-2"}]
        with patch("ebay_scheduler.db.list_due_scheduled_listings", return_value=[]), \
             patch("ebay_scheduler.db.list_native_scheduled_listings", return_value=native), \
             patch("ebay_scheduler.ebay_client.get_access_token", return_value="tok"), \
             patch("ebay_scheduler.ebay_client.get_offer", side_effect=[RuntimeError("down"), {"listingId": "L2"}]), \
             patch("ebay_scheduler.db.update_ebay_listing") as mock_update:
            ebay_scheduler.run_once(MagicMock())  # must not raise
        mock_update.assert_called_once_with("l2", {"status": "Veroeffentlicht"})


class RunPriceResearchOnceTests(unittest.TestCase):
    def test_does_nothing_when_nothing_due(self):
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", return_value=[]), \
             patch("ebay_scheduler.ebay_client.get_application_access_token") as mock_token:
            ebay_scheduler.run_price_research_once()
        mock_token.assert_not_called()

    def test_survives_due_listings_fetch_failure(self):
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", side_effect=RuntimeError("db down")), \
             patch("ebay_scheduler.ebay_client.get_application_access_token") as mock_token:
            ebay_scheduler.run_price_research_once()  # must not raise
        mock_token.assert_not_called()

    def test_skips_the_round_when_token_fetch_fails(self):
        due = [{"id": "l1", "card_id": "card-1", "title": "Karte 1"}]
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", side_effect=RuntimeError("down")), \
             patch("ebay_scheduler.ebay_client.search_active_listings") as mock_search, \
             patch("ebay_scheduler.db.mark_price_research_checked") as mock_mark:
            ebay_scheduler.run_price_research_once()  # must not raise
        mock_search.assert_not_called()
        mock_mark.assert_not_called()

    def test_fetches_token_once_for_the_whole_batch(self):
        due = [
            {"id": "l1", "card_id": "card-1", "title": "Karte 1"},
            {"id": "l2", "card_id": "card-2", "title": "Karte 2"},
        ]
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok") as mock_token, \
             patch("ebay_scheduler.db.get_card", return_value={"title": "Karte"}), \
             patch("ebay_scheduler.ebay_client.search_active_listings", return_value=[]), \
             patch("ebay_scheduler.db.mark_price_research_checked"):
            ebay_scheduler.run_price_research_once()
        mock_token.assert_called_once()

    def test_inserts_average_price_entry_and_marks_checked(self):
        due = [{"id": "l1", "card_id": "card-1", "title": "Karte 1"}]
        results = [{"price": 10.0}, {"price": 20.0}, {"price": None}]
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.db.get_card", return_value={"title": "Karte 1"}), \
             patch("ebay_scheduler.ebay_client.search_active_listings", return_value=results) as mock_search, \
             patch("ebay_scheduler.db.create_price_research_entry") as mock_create, \
             patch("ebay_scheduler.db.mark_price_research_checked") as mock_mark:
            ebay_scheduler.run_price_research_once()
        mock_search.assert_called_once_with("tok", "Karte 1")
        args, _ = mock_create.call_args
        self.assertEqual(args[0], "card-1")
        self.assertEqual(args[1]["price"], 15.0)
        self.assertIn("2 aktiven eBay-Angeboten", args[1]["note"])
        mock_mark.assert_called_once()
        self.assertEqual(mock_mark.call_args.args[0], "l1")

    def test_falls_back_to_card_title_when_listing_has_none(self):
        due = [{"id": "l1", "card_id": "card-1", "title": ""}]
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.db.get_card", return_value={"title": "Karten-Titel"}), \
             patch("ebay_scheduler.ebay_client.search_active_listings", return_value=[]) as mock_search, \
             patch("ebay_scheduler.db.create_price_research_entry"), \
             patch("ebay_scheduler.db.mark_price_research_checked"):
            ebay_scheduler.run_price_research_once()
        mock_search.assert_called_once_with("tok", "Karten-Titel")

    def test_marks_checked_even_when_no_results_found(self):
        due = [{"id": "l1", "card_id": "card-1", "title": "Karte 1"}]
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.db.get_card", return_value={"title": "Karte 1"}), \
             patch("ebay_scheduler.ebay_client.search_active_listings", return_value=[]), \
             patch("ebay_scheduler.db.create_price_research_entry") as mock_create, \
             patch("ebay_scheduler.db.mark_price_research_checked") as mock_mark:
            ebay_scheduler.run_price_research_once()
        mock_create.assert_not_called()
        mock_mark.assert_called_once()

    def test_a_failing_listing_does_not_abort_the_batch_and_is_still_marked_checked(self):
        due = [
            {"id": "l1", "card_id": "card-1", "title": "Karte 1"},
            {"id": "l2", "card_id": "card-2", "title": "Karte 2"},
        ]
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.db.get_card", return_value={"title": "x"}), \
             patch("ebay_scheduler.ebay_client.search_active_listings",
                   side_effect=[RuntimeError("eBay down"), []]), \
             patch("ebay_scheduler.db.create_price_research_entry"), \
             patch("ebay_scheduler.db.mark_price_research_checked") as mock_mark:
            ebay_scheduler.run_price_research_once()  # must not raise
        self.assertEqual(mock_mark.call_count, 2)

    def test_skips_listing_with_no_query_but_still_marks_checked(self):
        due = [{"id": "l1", "card_id": "card-1", "title": ""}]
        with patch("ebay_scheduler.db.list_listings_due_for_price_research", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.db.get_card", return_value={"title": ""}), \
             patch("ebay_scheduler.ebay_client.search_active_listings") as mock_search, \
             patch("ebay_scheduler.db.mark_price_research_checked") as mock_mark:
            ebay_scheduler.run_price_research_once()
        mock_search.assert_not_called()
        mock_mark.assert_called_once()


class RunWishlistPriceCheckOnceTests(unittest.TestCase):
    def test_does_nothing_when_nothing_due(self):
        with patch("ebay_scheduler.db.list_wishlist_items_due_for_price_check", return_value=[]), \
             patch("ebay_scheduler.ebay_client.get_application_access_token") as mock_token:
            ebay_scheduler.run_wishlist_price_check_once()
        mock_token.assert_not_called()

    def test_survives_due_items_fetch_failure(self):
        with patch("ebay_scheduler.db.list_wishlist_items_due_for_price_check", side_effect=RuntimeError("db down")), \
             patch("ebay_scheduler.ebay_client.get_application_access_token") as mock_token:
            ebay_scheduler.run_wishlist_price_check_once()  # must not raise
        mock_token.assert_not_called()

    def test_skips_the_round_when_token_fetch_fails(self):
        due = [{"id": "w1", "title": "Karte 1"}]
        with patch("ebay_scheduler.db.list_wishlist_items_due_for_price_check", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", side_effect=RuntimeError("down")), \
             patch("ebay_scheduler.ebay_client.search_active_listings") as mock_search, \
             patch("ebay_scheduler.db.update_wishlist_price_check") as mock_update:
            ebay_scheduler.run_wishlist_price_check_once()  # must not raise
        mock_search.assert_not_called()
        mock_update.assert_not_called()

    def test_fetches_token_once_for_the_whole_batch(self):
        due = [{"id": "w1", "title": "Karte 1"}, {"id": "w2", "title": "Karte 2"}]
        with patch("ebay_scheduler.db.list_wishlist_items_due_for_price_check", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok") as mock_token, \
             patch("ebay_scheduler.ebay_client.search_active_listings", return_value=[]), \
             patch("ebay_scheduler.db.update_wishlist_price_check"):
            ebay_scheduler.run_wishlist_price_check_once()
        mock_token.assert_called_once()

    def test_stores_cheapest_match_and_marks_checked(self):
        due = [{"id": "w1", "title": "Karte 1", "team": "FC Bayern", "set_name": "Topps 2026"}]
        results = [
            {"price": 20.0, "title": "Angebot A", "item_web_url": "https://ebay.de/a"},
            {"price": 9.5, "title": "Angebot B", "item_web_url": "https://ebay.de/b"},
            {"price": None, "title": "Angebot C", "item_web_url": "https://ebay.de/c"},
        ]
        with patch("ebay_scheduler.db.list_wishlist_items_due_for_price_check", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.ebay_client.search_active_listings", return_value=results) as mock_search, \
             patch("ebay_scheduler.db.update_wishlist_price_check") as mock_update:
            ebay_scheduler.run_wishlist_price_check_once()
        mock_search.assert_called_once_with("tok", "Karte 1 FC Bayern Topps 2026")
        args, _ = mock_update.call_args[0]
        self.assertEqual(args, "w1")
        fields = mock_update.call_args[0][1]
        self.assertEqual(fields["last_match_price"], 9.5)
        self.assertEqual(fields["last_match_title"], "Angebot B")
        self.assertEqual(fields["last_match_url"], "https://ebay.de/b")
        self.assertIn("last_price_check_at", fields)

    def test_clears_match_when_no_results_found(self):
        due = [{"id": "w1", "title": "Karte 1"}]
        with patch("ebay_scheduler.db.list_wishlist_items_due_for_price_check", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.ebay_client.search_active_listings", return_value=[]), \
             patch("ebay_scheduler.db.update_wishlist_price_check") as mock_update:
            ebay_scheduler.run_wishlist_price_check_once()
        fields = mock_update.call_args[0][1]
        self.assertIsNone(fields["last_match_price"])

    def test_a_failing_item_does_not_abort_the_batch_and_is_still_marked_checked(self):
        due = [{"id": "w1", "title": "Karte 1"}, {"id": "w2", "title": "Karte 2"}]
        with patch("ebay_scheduler.db.list_wishlist_items_due_for_price_check", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.ebay_client.search_active_listings",
                   side_effect=[RuntimeError("eBay down"), []]), \
             patch("ebay_scheduler.db.update_wishlist_price_check") as mock_update:
            ebay_scheduler.run_wishlist_price_check_once()  # must not raise
        self.assertEqual(mock_update.call_count, 2)

    def test_skips_item_with_no_query_but_still_marks_checked(self):
        due = [{"id": "w1", "title": "", "team": "", "set_name": ""}]
        with patch("ebay_scheduler.db.list_wishlist_items_due_for_price_check", return_value=due), \
             patch("ebay_scheduler.ebay_client.get_application_access_token", return_value="tok"), \
             patch("ebay_scheduler.ebay_client.search_active_listings") as mock_search, \
             patch("ebay_scheduler.db.update_wishlist_price_check") as mock_update:
            ebay_scheduler.run_wishlist_price_check_once()
        mock_search.assert_not_called()
        mock_update.assert_called_once()


if __name__ == "__main__":
    unittest.main()
