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

    def test_syncs_four_tabs_on_success(self):
        settings = {"refresh_token": "r1", "spreadsheet_id": "sheet-1"}
        with patch("main.db.get_google_sheets_settings", return_value=settings), \
             patch("main.db.all_cards", return_value=[]), \
             patch("main.db.all_purchases", return_value=[]), \
             patch("main.db.all_purchase_items", return_value=[]), \
             patch("main.db.all_ebay_listings", return_value=[]), \
             patch("main.db.all_ebay_sales", return_value=[]), \
             patch("main.db.save_google_sheets_settings") as mock_save, \
             patch("main.google_sheets_client.refresh_access_token", return_value="access-tok"), \
             patch("main.google_sheets_client.sync_to_sheets") as mock_sync:
            response = client.post("/api/sheets/sync")
        self.assertEqual(response.status_code, 200)
        tabs = mock_sync.call_args[0][2]
        self.assertEqual(set(tabs.keys()), {"Karten", "Käufe", "eBay", "Sync_Info"})
        self.assertIn("last_synced_at", mock_save.call_args[0][0])

    def test_returns_502_on_google_api_error(self):
        settings = {"refresh_token": "r1", "spreadsheet_id": "sheet-1"}
        with patch("main.db.get_google_sheets_settings", return_value=settings), \
             patch("main.db.all_cards", return_value=[]), \
             patch("main.db.all_purchases", return_value=[]), \
             patch("main.db.all_purchase_items", return_value=[]), \
             patch("main.db.all_ebay_listings", return_value=[]), \
             patch("main.db.all_ebay_sales", return_value=[]), \
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
        with patch("main.backup.build_backup_zip", return_value=b"fake-zip-bytes"):
            response = client.get("/api/backup")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "application/zip")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertIn(".zip", response.headers["content-disposition"])
        self.assertEqual(response.content, b"fake-zip-bytes")


if __name__ == "__main__":
    unittest.main()
