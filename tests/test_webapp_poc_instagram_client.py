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
        self.assertTrue(url.startswith(instagram_client.AUTH_BASE))
        self.assertIn("state=state-123", url)
        self.assertIn("client_id=app-1", url)
        self.assertIn("instagram_business_basic", url)

    def test_auth_base_is_the_www_instagram_com_login_page_not_api_subdomain(self):
        # Regression: api.instagram.com does NOT serve the login/consent
        # page (it's the token-exchange host) - pointing AUTH_BASE there
        # produced Instagram's generic "page not available" error instead
        # of the login dialog when the user tried to connect.
        self.assertEqual(instagram_client.AUTH_BASE, "https://www.instagram.com/oauth/authorize")


class ExchangeCodeTests(unittest.TestCase):
    def test_returns_access_token_and_ig_user_id(self):
        with patch("instagram_client.httpx.post", return_value=_response(200, {"access_token": "short-lived", "user_id": 179825768})):
            token, ig_user_id = instagram_client.exchange_code("auth-code")
        self.assertEqual(token, "short-lived")
        self.assertEqual(ig_user_id, "179825768")

    def test_raises_on_error(self):
        with patch("instagram_client.httpx.post", return_value=_response(400, {"error": "bad_code"}, text="bad_code")):
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

    def test_uses_views_metric_not_deprecated_profile_views(self):
        # Graph-API v22.0 hat "profile_views" durch "views" abgeloest -
        # Regressionstest, damit hier nicht versehentlich das veraltete
        # Metrik-Feld zurueckkommt (siehe Kommentar in get_insights()).
        with patch("instagram_client.httpx.get", return_value=_response(200, {"data": []})) as mock_get:
            instagram_client.get_insights("tok", "ig-1")
        params = mock_get.call_args.kwargs["params"]
        self.assertIn("views", params["metric"])
        self.assertNotIn("profile_views", params["metric"])

    def test_raises_on_error(self):
        with patch("instagram_client.httpx.get", return_value=_response(400, {"error": "boom"}, text="boom")):
            with self.assertRaises(instagram_client.InstagramApiError):
                instagram_client.get_insights("tok", "ig-1")


if __name__ == "__main__":
    unittest.main()
