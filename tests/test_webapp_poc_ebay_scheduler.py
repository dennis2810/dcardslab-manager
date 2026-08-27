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


if __name__ == "__main__":
    unittest.main()
