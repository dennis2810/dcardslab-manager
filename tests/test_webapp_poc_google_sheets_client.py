"""Tests for webapp-poc/google_sheets_client.py. All HTTP is mocked,
same depth as tests/test_webapp_poc_ebay_client.py."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import google_sheets_client  # noqa: E402


def _response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text or str(json_data or "")
    return resp


class AuthorizationUrlTests(unittest.TestCase):
    def test_includes_state_and_scope(self):
        with patch.object(google_sheets_client, "CLIENT_ID", "cid"), \
             patch.object(google_sheets_client, "REDIRECT_URI", "https://x/callback"):
            url = google_sheets_client.authorization_url("state-123")
        self.assertIn("state=state-123", url)
        self.assertIn("client_id=cid", url)
        self.assertIn("access_type=offline", url)
        self.assertIn("prompt=consent", url)


class ExchangeCodeTests(unittest.TestCase):
    def test_returns_token_dict(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(200, {"access_token": "a", "refresh_token": "r"})):
            token = google_sheets_client.exchange_code("auth-code")
        self.assertEqual(token["refresh_token"], "r")

    def test_raises_on_error(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(400, {"error": "invalid_grant"}, text="invalid_grant")):
            with self.assertRaises(google_sheets_client.GoogleApiError):
                google_sheets_client.exchange_code("bad-code")


class RefreshAccessTokenTests(unittest.TestCase):
    def test_returns_access_token(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(200, {"access_token": "fresh"})):
            token = google_sheets_client.refresh_access_token("refresh-tok")
        self.assertEqual(token, "fresh")

    def test_raises_not_connected_on_invalid_grant(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(400, {"error": "invalid_grant"}, text="invalid_grant")):
            with self.assertRaises(google_sheets_client.GoogleNotConnectedError):
                google_sheets_client.refresh_access_token("stale-tok")

    def test_raises_api_error_on_other_failures(self):
        with patch("google_sheets_client.httpx.post", return_value=_response(500, {"error": "server_error"}, text="server_error")):
            with self.assertRaises(google_sheets_client.GoogleApiError):
                google_sheets_client.refresh_access_token("tok")


class SyncToSheetsTests(unittest.TestCase):
    def _meta_response(self, titles):
        return _response(200, {"sheets": [
            {"properties": {"title": t, "sheetId": i}} for i, t in enumerate(titles)
        ]})

    def test_creates_missing_tab_then_writes_values(self):
        responses = [
            self._meta_response([]),  # get spreadsheet metadata
            _response(200, {}),  # batchUpdate: addSheet
            self._meta_response(["Karten"]),  # re-fetch metadata for sheetId
            _response(200, {}),  # values.clear
            _response(200, {}),  # values.update
            _response(200, {}),  # batchUpdate: freeze header row
        ]
        with patch("google_sheets_client.httpx.request", side_effect=responses) as mock_request:
            google_sheets_client.sync_to_sheets("tok", "sheet-1", {"Karten": (["ID", "Titel"], [["1", "Max"]])})
        self.assertGreaterEqual(mock_request.call_count, 4)

    def test_reuses_existing_tab_without_creating_it(self):
        responses = [
            self._meta_response(["Karten"]),  # get spreadsheet metadata - already exists
            _response(200, {}),  # values.clear
            _response(200, {}),  # values.update
            _response(200, {}),  # batchUpdate: freeze header row
        ]
        with patch("google_sheets_client.httpx.request", side_effect=responses) as mock_request:
            google_sheets_client.sync_to_sheets("tok", "sheet-1", {"Karten": (["ID"], [["1"]])})
        self.assertEqual(mock_request.call_count, 4)

    def test_raises_on_google_error(self):
        with patch("google_sheets_client.httpx.request", return_value=_response(403, {"error": "forbidden"}, text="forbidden")):
            with self.assertRaises(google_sheets_client.GoogleApiError):
                google_sheets_client.sync_to_sheets("tok", "sheet-1", {"Karten": ([], [])})


if __name__ == "__main__":
    unittest.main()
