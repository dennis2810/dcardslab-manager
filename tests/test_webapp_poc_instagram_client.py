"""Tests for webapp-poc/instagram_client.py. All HTTP is mocked, same depth
as tests/test_webapp_poc_google_sheets_client.py."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import instagram_client  # noqa: E402


def _response(status_code=200, json_data=None, text=""):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text or str(json_data or "")
    return resp


class AuthorizationUrlTests(unittest.TestCase):
    def test_includes_state_and_scope(self):
        with patch.object(instagram_client, "APP_ID", "app-1"), \
             patch.object(instagram_client, "REDIRECT_URI", "https://x/callback"):
            url = instagram_client.authorization_url("state-123")
        self.assertIn("state=state-123", url)
        self.assertIn("client_id=app-1", url)
        self.assertIn("instagram_basic", url)


class ExchangeCodeTests(unittest.TestCase):
    def test_returns_access_token(self):
        with patch("instagram_client.httpx.get", return_value=_response(200, {"access_token": "short-lived"})):
            token = instagram_client.exchange_code("auth-code")
        self.assertEqual(token, "short-lived")

    def test_raises_on_error(self):
        with patch("instagram_client.httpx.get", return_value=_response(400, {"error": "bad_code"}, text="bad_code")):
            with self.assertRaises(instagram_client.InstagramApiError):
                instagram_client.exchange_code("bad-code")


class ExchangeForLongLivedTokenTests(unittest.TestCase):
    def test_returns_long_lived_token(self):
        with patch("instagram_client.httpx.get", return_value=_response(200, {"access_token": "long-lived"})):
            token = instagram_client.exchange_for_long_lived_token("short-lived")
        self.assertEqual(token, "long-lived")

    def test_raises_on_error(self):
        with patch("instagram_client.httpx.get", return_value=_response(400, {"error": "boom"}, text="boom")):
            with self.assertRaises(instagram_client.InstagramApiError):
                instagram_client.exchange_for_long_lived_token("tok")


class FindInstagramBusinessAccountTests(unittest.TestCase):
    def test_returns_first_linked_ig_account(self):
        pages = {"data": [
            {"name": "Seite ohne Instagram"},
            {"name": "Seite mit Instagram", "instagram_business_account": {"id": "ig-1"}},
        ]}
        with patch("instagram_client.httpx.get", return_value=_response(200, pages)):
            ig_id = instagram_client.find_instagram_business_account("tok")
        self.assertEqual(ig_id, "ig-1")

    def test_raises_when_no_page_has_instagram_linked(self):
        pages = {"data": [{"name": "Seite ohne Instagram"}]}
        with patch("instagram_client.httpx.get", return_value=_response(200, pages)):
            with self.assertRaises(instagram_client.NoInstagramAccountError):
                instagram_client.find_instagram_business_account("tok")

    def test_raises_api_error_on_failure(self):
        with patch("instagram_client.httpx.get", return_value=_response(500, {"error": "boom"}, text="boom")):
            with self.assertRaises(instagram_client.InstagramApiError):
                instagram_client.find_instagram_business_account("tok")


class GetAccountSummaryTests(unittest.TestCase):
    def test_returns_summary(self):
        summary = {"username": "dcardslab", "followers_count": 100, "media_count": 5}
        with patch("instagram_client.httpx.get", return_value=_response(200, summary)):
            result = instagram_client.get_account_summary("tok", "ig-1")
        self.assertEqual(result["username"], "dcardslab")

    def test_raises_not_connected_on_expired_session(self):
        with patch("instagram_client.httpx.get", return_value=_response(400, {"error": "session"}, text="Error validating access token: Session has expired")):
            with self.assertRaises(instagram_client.InstagramNotConnectedError):
                instagram_client.get_account_summary("tok", "ig-1")

    def test_raises_api_error_on_other_failures(self):
        with patch("instagram_client.httpx.get", return_value=_response(500, {"error": "server_error"}, text="server_error")):
            with self.assertRaises(instagram_client.InstagramApiError):
                instagram_client.get_account_summary("tok", "ig-1")


class GetInsightsTests(unittest.TestCase):
    def test_returns_metric_list(self):
        data = {"data": [{"name": "reach", "values": [{"value": 42}]}]}
        with patch("instagram_client.httpx.get", return_value=_response(200, data)):
            insights = instagram_client.get_insights("tok", "ig-1")
        self.assertEqual(insights[0]["name"], "reach")

    def test_raises_on_error(self):
        with patch("instagram_client.httpx.get", return_value=_response(400, {"error": "boom"}, text="boom")):
            with self.assertRaises(instagram_client.InstagramApiError):
                instagram_client.get_insights("tok", "ig-1")


if __name__ == "__main__":
    unittest.main()
