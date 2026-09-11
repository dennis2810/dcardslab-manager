"""Tests for /api/sheets/* and /api/backup (webapp-poc/main.py)."""
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
import email_notify  # noqa: E402
import google_sheets_client  # noqa: E402

client = TestClient(main.app, follow_redirects=False)


class SheetsStatusEndpointTests(unittest.TestCase):
    def test_not_connected_when_no_settings(self):
        with patch("main.db.get_google_sheets_settings", return_value=None):
            response = client.get("/api/sheets/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["connected"])

    def test_connected_when_refresh_token_present(self):
        settings = {
            "refresh_token": "r1", "spreadsheet_id": "sheet-1",
            "connected_at": "2026-08-28T00:00:00+00:00", "last_synced_at": None,
        }
        with patch("main.db.get_google_sheets_settings", return_value=settings):
            response = client.get("/api/sheets/status")
        body = response.json()
        self.assertTrue(body["connected"])
        self.assertEqual(body["spreadsheet_id"], "sheet-1")


class SheetsOauthStartEndpointTests(unittest.TestCase):
    def test_redirects_to_google_auth_base(self):
        response = client.get("/api/sheets/oauth/start")
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.headers["location"].startswith(google_sheets_client.AUTH_BASE))


class SheetsOauthCallbackEndpointTests(unittest.TestCase):
    def test_error_param_redirects_with_error(self):
        with patch("main.google_sheets_client.exchange_code") as mock_exchange:
            response = client.get("/api/sheets/oauth/callback?error=access_denied")
        mock_exchange.assert_not_called()
        self.assertIn("sheets_error=access_denied", response.headers["location"])

    def test_missing_or_unknown_state_redirects_with_error(self):
        with patch("main.google_sheets_client.exchange_code") as mock_exchange:
            response = client.get("/api/sheets/oauth/callback?code=abc&state=unknown-state")
        mock_exchange.assert_not_called()
        self.assertIn("sheets_error=", response.headers["location"])

    def test_error_containing_special_characters_is_url_encoded_not_interpolated_raw(self):
        # Regression test: an unescaped f-string into the redirect URL lets
        # a "&"/"#" in the error text corrupt the query string, and CRLF in
        # the value would make uvicorn reject the Location header outright
        # (turning a clean error redirect into an unhandled 500). Google's
        # error text is external input, so it must go through urlencode().
        response = client.get("/api/sheets/oauth/callback?error=access_denied%26evil%3D1")
        location = response.headers["location"]
        self.assertNotIn("evil=1", location)  # the raw "&evil=1" must not survive unescaped
        self.assertTrue(location.startswith("/settings.html?sheets_error="))

    def test_google_api_error_message_is_url_encoded_in_redirect(self):
        with patch("main.google_sheets_client.exchange_code", side_effect=google_sheets_client.GoogleApiError("bad & broken")):
            start_response = client.get("/api/sheets/oauth/start")
            state = start_response.headers["location"].split("state=")[1].split("&")[0]
            response = client.get(f"/api/sheets/oauth/callback?code=abc&state={state}")
        location = response.headers["location"]
        self.assertNotIn(" ", location)
        self.assertNotIn("bad & broken", location)

    def test_valid_code_and_state_saves_token_and_redirects_to_settings(self):
        start_response = client.get("/api/sheets/oauth/start")
        state = start_response.headers["location"].split("state=")[1].split("&")[0]

        with patch("main.google_sheets_client.exchange_code", return_value={"refresh_token": "r1"}) as mock_exchange, \
             patch("main.db.save_google_sheets_settings") as mock_save:
            response = client.get(f"/api/sheets/oauth/callback?code=abc&state={state}")
        mock_exchange.assert_called_once_with("abc")
        self.assertEqual(mock_save.call_args[0][0]["refresh_token"], "r1")
        self.assertEqual(response.headers["location"], "/settings.html")


class UpdateSheetsSettingsEndpointTests(unittest.TestCase):
    def test_saves_spreadsheet_id(self):
        with patch("main.db.save_google_sheets_settings", return_value={"spreadsheet_id": "sheet-2"}) as mock_save:
            response = client.post("/api/sheets/settings", json={"spreadsheet_id": "sheet-2"})
        self.assertEqual(response.status_code, 200)
        mock_save.assert_called_once_with({"spreadsheet_id": "sheet-2"})

    def test_returns_400_for_empty_spreadsheet_id(self):
        response = client.post("/api/sheets/settings", json={"spreadsheet_id": "  "})
        self.assertEqual(response.status_code, 400)


class SyncToSheetsEndpointTests(unittest.TestCase):
    def test_returns_401_when_not_connected(self):
        with patch("main.db.get_google_sheets_settings", return_value=None):
            response = client.post("/api/sheets/sync")
        self.assertEqual(response.status_code, 401)

    def test_returns_400_when_no_spreadsheet_id(self):
        with patch("main.db.get_google_sheets_settings", return_value={"refresh_token": "r1", "spreadsheet_id": ""}):
            response = client.post("/api/sheets/sync")
        self.assertEqual(response.status_code, 400)

    def _patch_sheets_data_sources(self):
        return [
            patch("main.db.all_cards", return_value=[]),
            patch("main.db.all_purchases", return_value=[]),
            patch("main.db.all_purchase_items", return_value=[]),
            patch("main.db.all_ebay_listings", return_value=[]),
            patch("main.db.all_ebay_sales", return_value=[]),
            patch("main.db.list_inventory", return_value=[]),
            patch("main.db.list_wishlist_items", return_value=[]),
            patch("main.db.list_private_collection_cards", return_value=[]),
            patch("main.db.statistics_rows", return_value=[]),
        ]

    def test_syncs_nine_tabs_on_success(self):
        settings = {"refresh_token": "r1", "spreadsheet_id": "sheet-1"}
        patchers = self._patch_sheets_data_sources()
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        with patch("main.db.get_google_sheets_settings", return_value=settings), \
             patch("main.db.save_google_sheets_settings") as mock_save, \
             patch("main.google_sheets_client.refresh_access_token", return_value="access-tok"), \
             patch("main.google_sheets_client.sync_to_sheets") as mock_sync:
            response = client.post("/api/sheets/sync")
        self.assertEqual(response.status_code, 200)
        tabs = mock_sync.call_args[0][2]
        self.assertEqual(
            set(tabs.keys()),
            {
                "Karten", "Käufe", "eBay", "Inventar", "Wunschliste", "Private Sammlung",
                "Statistiken", "Dashboard", "Sync_Info",
            },
        )
        self.assertIn("last_synced_at", mock_save.call_args[0][0])

    def test_wunschliste_tab_lists_items(self):
        settings = {"refresh_token": "r1", "spreadsheet_id": "sheet-1"}
        patchers = self._patch_sheets_data_sources()
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        wishlist_item = {
            "id": "w1", "title": "Lionel Messi", "team": "PSG", "set_name": "Panini",
            "target_price": 12.5, "notes": "Auto gesucht",
        }
        with patch("main.db.get_google_sheets_settings", return_value=settings), \
             patch("main.db.save_google_sheets_settings"), \
             patch("main.db.list_wishlist_items", return_value=[wishlist_item]), \
             patch("main.google_sheets_client.refresh_access_token", return_value="access-tok"), \
             patch("main.google_sheets_client.sync_to_sheets") as mock_sync:
            response = client.post("/api/sheets/sync")
        self.assertEqual(response.status_code, 200)
        tabs = mock_sync.call_args[0][2]
        headers, rows = tabs["Wunschliste"]
        self.assertIn("title", headers)
        self.assertEqual(rows[0][headers.index("title")], "Lionel Messi")

    def test_private_sammlung_tab_lists_cards(self):
        settings = {"refresh_token": "r1", "spreadsheet_id": "sheet-1"}
        patchers = self._patch_sheets_data_sources()
        for p in patchers:
            p.start()
            self.addCleanup(p.stop)
        private_card = {
            "id": "c1", "title": "Kylian Mbappe", "team": "PSG", "set_name": "Panini",
            "card_number": "12", "tags": "auto",
        }
        with patch("main.db.get_google_sheets_settings", return_value=settings), \
             patch("main.db.save_google_sheets_settings"), \
             patch("main.db.list_private_collection_cards", return_value=[private_card]), \
             patch("main.google_sheets_client.refresh_access_token", return_value="access-tok"), \
             patch("main.google_sheets_client.sync_to_sheets") as mock_sync:
            response = client.post("/api/sheets/sync")
        self.assertEqual(response.status_code, 200)
        tabs = mock_sync.call_args[0][2]
        headers, rows = tabs["Private Sammlung"]
        self.assertIn("title", headers)
        self.assertEqual(rows[0][headers.index("title")], "Kylian Mbappe")

    def test_returns_502_on_google_api_error(self):
        settings = {"refresh_token": "r1", "spreadsheet_id": "sheet-1"}
        with patch("main.db.get_google_sheets_settings", return_value=settings), \
             patch("main.google_sheets_client.refresh_access_token", side_effect=google_sheets_client.GoogleApiError("boom")):
            response = client.post("/api/sheets/sync")
        self.assertEqual(response.status_code, 502)

    def test_returns_401_on_google_not_connected_error(self):
        settings = {"refresh_token": "r1", "spreadsheet_id": "sheet-1"}
        with patch("main.db.get_google_sheets_settings", return_value=settings), \
             patch("main.db.all_cards", return_value=[]), \
             patch("main.db.all_purchases", return_value=[]), \
             patch("main.db.all_purchase_items", return_value=[]), \
             patch("main.db.all_ebay_listings", return_value=[]), \
             patch("main.db.all_ebay_sales", return_value=[]), \
             patch("main.google_sheets_client.refresh_access_token", side_effect=google_sheets_client.GoogleNotConnectedError("expired")):
            response = client.post("/api/sheets/sync")
        self.assertEqual(response.status_code, 401)


class DownloadBackupEndpointTests(unittest.TestCase):
    def test_returns_zip_with_attachment_headers(self):
        with patch("main.backup.build_backup_zip", return_value=b"fake-zip-bytes"), \
             patch("main.db.record_backup_downloaded"):
            response = client.get("/api/backup")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn(".zip", response.headers["content-disposition"])
        self.assertEqual(response.content, b"fake-zip-bytes")

    def test_records_the_download_timestamp(self):
        with patch("main.backup.build_backup_zip", return_value=b"fake-zip-bytes"), \
             patch("main.db.record_backup_downloaded") as mock_record:
            client.get("/api/backup")
        mock_record.assert_called_once()
        # ISO-Zeitstempel, kein leerer/None-Wert.
        self.assertIsInstance(mock_record.call_args.args[0], str)
        self.assertTrue(mock_record.call_args.args[0])

    def test_recording_failure_does_not_break_the_download(self):
        with patch("main.backup.build_backup_zip", return_value=b"fake-zip-bytes"), \
             patch("main.db.record_backup_downloaded", side_effect=RuntimeError("db down")):
            response = client.get("/api/backup")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"fake-zip-bytes")


class AppStatusEndpointTests(unittest.TestCase):
    def test_returns_last_backup_at(self):
        with patch("main.db.get_app_status", return_value={"last_backup_at": "2026-09-09T10:00:00+00:00"}):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["last_backup_at"], "2026-09-09T10:00:00+00:00")

    def test_returns_none_when_never_backed_up(self):
        with patch("main.db.get_app_status", return_value=None):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["last_backup_at"])

    def test_returns_low_stock_threshold(self):
        with patch("main.db.get_app_status", return_value={"low_stock_threshold": 3}):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["low_stock_threshold"], 3)

    def test_low_stock_threshold_defaults_to_zero(self):
        with patch("main.db.get_app_status", return_value=None):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["low_stock_threshold"], 0)

    def test_returns_auto_relist_enabled(self):
        with patch("main.db.get_app_status", return_value={"auto_relist_enabled": True}):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["auto_relist_enabled"])

    def test_auto_relist_enabled_defaults_to_false(self):
        with patch("main.db.get_app_status", return_value=None):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["auto_relist_enabled"])

    def test_returns_last_auto_backup_at(self):
        with patch("main.db.get_app_status", return_value={"last_auto_backup_at": "2026-09-10T03:00:00+00:00"}):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["last_auto_backup_at"], "2026-09-10T03:00:00+00:00")

    def test_returns_sender_address(self):
        with patch("main.db.get_app_status", return_value={"sender_address": "DCardsLab\nMusterstr. 1"}):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sender_address"], "DCardsLab\nMusterstr. 1")

    def test_sender_address_defaults_to_empty_string(self):
        with patch("main.db.get_app_status", return_value=None):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sender_address"], "")

    def test_returns_activity_cleared_at(self):
        with patch("main.db.get_app_status", return_value={"activity_cleared_at": "2026-09-10T12:00:00+00:00"}):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["activity_cleared_at"], "2026-09-10T12:00:00+00:00")

    def test_activity_cleared_at_defaults_to_none(self):
        with patch("main.db.get_app_status", return_value=None):
            response = client.get("/api/app-status")
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["activity_cleared_at"])

    def test_returns_notification_settings(self):
        status = {
            "notify_on_sale": True, "smtp_host": "smtp.example.com", "smtp_port": 587,
            "smtp_username": "user@example.com", "smtp_from": "dcardslab@example.com",
            "smtp_to": "me@example.com", "smtp_use_tls": False, "smtp_password": "secret",
        }
        with patch("main.db.get_app_status", return_value=status):
            response = client.get("/api/app-status")
        body = response.json()
        self.assertTrue(body["notify_on_sale"])
        self.assertEqual(body["smtp_host"], "smtp.example.com")
        self.assertEqual(body["smtp_port"], 587)
        self.assertEqual(body["smtp_username"], "user@example.com")
        self.assertEqual(body["smtp_from"], "dcardslab@example.com")
        self.assertEqual(body["smtp_to"], "me@example.com")
        self.assertFalse(body["smtp_use_tls"])
        self.assertTrue(body["smtp_password_set"])
        self.assertNotIn("smtp_password", body)

    def test_notification_settings_default_when_no_row(self):
        with patch("main.db.get_app_status", return_value=None):
            response = client.get("/api/app-status")
        body = response.json()
        self.assertFalse(body["notify_on_sale"])
        self.assertEqual(body["smtp_host"], "")
        self.assertIsNone(body["smtp_port"])
        self.assertTrue(body["smtp_use_tls"])
        self.assertFalse(body["smtp_password_set"])


class ClearActivityEndpointTests(unittest.TestCase):
    def test_stores_current_timestamp(self):
        with patch("main.db.set_activity_cleared", return_value={"activity_cleared_at": "now"}) as mock_set:
            response = client.post("/api/app-status/clear-activity")
        self.assertEqual(response.status_code, 200)
        mock_set.assert_called_once()
        timestamp = mock_set.call_args[0][0]
        self.assertTrue(timestamp.endswith("+00:00"))


class LowStockThresholdEndpointTests(unittest.TestCase):
    def test_sets_threshold(self):
        with patch("main.db.set_low_stock_threshold", return_value={"id": True, "low_stock_threshold": 5}) as mock_set:
            response = client.put("/api/low-stock-threshold", json={"threshold": 5})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["low_stock_threshold"], 5)
        mock_set.assert_called_once_with(5)

    def test_rejects_negative_threshold(self):
        response = client.put("/api/low-stock-threshold", json={"threshold": -1})
        self.assertEqual(response.status_code, 400)

    def test_rejects_non_integer_threshold(self):
        response = client.put("/api/low-stock-threshold", json={"threshold": "abc"})
        self.assertEqual(response.status_code, 400)


class AutoRelistEnabledEndpointTests(unittest.TestCase):
    def test_enables_switch(self):
        with patch("main.db.set_auto_relist_enabled", return_value={"id": True, "auto_relist_enabled": True}) as mock_set:
            response = client.put("/api/auto-relist-enabled", json={"enabled": True})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["auto_relist_enabled"])
        mock_set.assert_called_once_with(True)

    def test_disables_switch(self):
        with patch("main.db.set_auto_relist_enabled", return_value={"id": True, "auto_relist_enabled": False}) as mock_set:
            response = client.put("/api/auto-relist-enabled", json={"enabled": False})
        self.assertEqual(response.status_code, 200)
        mock_set.assert_called_once_with(False)


class SenderAddressEndpointTests(unittest.TestCase):
    def test_sets_address(self):
        with patch("main.db.set_sender_address", return_value={"id": True, "sender_address": "DCardsLab\nMusterstr. 1"}) as mock_set:
            response = client.put("/api/sender-address", json={"address": "DCardsLab\nMusterstr. 1"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["sender_address"], "DCardsLab\nMusterstr. 1")
        mock_set.assert_called_once_with("DCardsLab\nMusterstr. 1")

    def test_allows_empty_address(self):
        with patch("main.db.set_sender_address", return_value={"id": True, "sender_address": ""}) as mock_set:
            response = client.put("/api/sender-address", json={"address": ""})
        self.assertEqual(response.status_code, 200)
        mock_set.assert_called_once_with("")


class NotificationSettingsEndpointTests(unittest.TestCase):
    def test_saves_settings_including_password(self):
        fields = {
            "smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_from": "a@b.de",
            "smtp_to": "me@b.de", "smtp_password": "secret", "notify_on_sale": True,
        }
        with patch("main.db.save_notification_settings", return_value={"id": True}) as mock_save:
            response = client.put("/api/notification-settings", json=fields)
        self.assertEqual(response.status_code, 200)
        mock_save.assert_called_once_with(fields)

    def test_leaving_password_blank_does_not_forward_it(self):
        fields = {"smtp_host": "smtp.example.com", "smtp_password": ""}
        with patch("main.db.save_notification_settings", return_value={"id": True}) as mock_save:
            client.put("/api/notification-settings", json=fields)
        saved = mock_save.call_args[0][0]
        self.assertNotIn("smtp_password", saved)

    def test_omitting_password_entirely_still_saves_other_fields(self):
        fields = {"smtp_host": "smtp.example.com"}
        with patch("main.db.save_notification_settings", return_value={"id": True}) as mock_save:
            client.put("/api/notification-settings", json=fields)
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved["smtp_host"], "smtp.example.com")
        self.assertNotIn("smtp_password", saved)


class SendTestNotificationEndpointTests(unittest.TestCase):
    def test_sends_test_email_with_current_settings(self):
        settings = {"smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_from": "a@b.de", "smtp_to": "me@b.de"}
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email") as mock_send:
            response = client.post("/api/notification-settings/test")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["sent"])
        mock_send.assert_called_once()
        self.assertEqual(mock_send.call_args[0][0], settings)

    def test_returns_400_when_not_configured(self):
        with patch("main.db.get_app_status", return_value={}), \
             patch("main.email_notify.send_email", side_effect=email_notify.EmailNotConfiguredError("x")):
            response = client.post("/api/notification-settings/test")
        self.assertEqual(response.status_code, 400)

    def test_returns_502_on_smtp_failure(self):
        settings = {"smtp_host": "smtp.example.com", "smtp_port": 587, "smtp_from": "a@b.de", "smtp_to": "me@b.de"}
        with patch("main.db.get_app_status", return_value=settings), \
             patch("main.email_notify.send_email", side_effect=OSError("connection refused")):
            response = client.post("/api/notification-settings/test")
        self.assertEqual(response.status_code, 502)


class RunBackupNowEndpointTests(unittest.TestCase):
    def test_triggers_backup_and_returns_timestamp(self):
        with patch("main.backup.run_backup_now") as mock_run, \
             patch("main.db.get_app_status", return_value={"last_auto_backup_at": "2026-09-10T12:00:00+00:00"}):
            response = client.post("/api/backup/run-now")
        self.assertEqual(response.status_code, 200)
        mock_run.assert_called_once()
        self.assertEqual(response.json()["last_auto_backup_at"], "2026-09-10T12:00:00+00:00")

    def test_returns_502_when_backup_fails(self):
        with patch("main.backup.run_backup_now", side_effect=RuntimeError("bucket down")):
            response = client.post("/api/backup/run-now")
        self.assertEqual(response.status_code, 502)
        self.assertIn("bucket down", response.json()["detail"])


class ListPortfolioSnapshotsEndpointTests(unittest.TestCase):
    def test_returns_snapshots(self):
        snapshots = [{"snapshot_date": "2026-09-01", "total_value": 500, "card_count": 10}]
        with patch("main.db.list_portfolio_snapshots", return_value=snapshots):
            response = client.get("/api/portfolio/snapshots")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["snapshots"], snapshots)


class CreatePortfolioSnapshotNowEndpointTests(unittest.TestCase):
    def test_triggers_snapshot_and_returns_it(self):
        snapshot = {"id": "s1", "snapshot_date": "2026-09-11", "total_value": 1234.5, "card_count": 42}
        with patch("main.portfolio.record_snapshot_now", return_value=snapshot) as mock_record:
            response = client.post("/api/portfolio/snapshot-now")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), snapshot)
        mock_record.assert_called_once_with(main._compute_portfolio_value)

    def test_returns_502_on_failure(self):
        with patch("main.portfolio.record_snapshot_now", side_effect=RuntimeError("db down")):
            response = client.post("/api/portfolio/snapshot-now")
        self.assertEqual(response.status_code, 502)
        self.assertIn("db down", response.json()["detail"])


class ComputePortfolioValueTests(unittest.TestCase):
    def test_sums_value_of_in_stock_items_only(self):
        items = [
            {"quantity": 2, "value": 20.0},
            {"quantity": 0, "value": 100.0},
            {"quantity": 1, "value": 9.99},
        ]
        with patch("main.db.list_inventory", return_value=[{"id": "i1"}, {"id": "i2"}, {"id": "i3"}]), \
             patch("main._expand_inventory_items", return_value=items):
            total_value, card_count = main._compute_portfolio_value()
        self.assertEqual(total_value, 29.99)
        self.assertEqual(card_count, 2)

    def test_ignores_items_with_unknown_value(self):
        items = [{"quantity": 1, "value": None}, {"quantity": 1, "value": 5.0}]
        with patch("main.db.list_inventory", return_value=[{"id": "i1"}, {"id": "i2"}]), \
             patch("main._expand_inventory_items", return_value=items):
            total_value, card_count = main._compute_portfolio_value()
        self.assertEqual(total_value, 5.0)
        self.assertEqual(card_count, 2)


if __name__ == "__main__":
    unittest.main()
