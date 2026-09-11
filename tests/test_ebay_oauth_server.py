"""Regression tests for the eBay OAuth/API server (ebay-oauth-server/app.py).

Flask isn't installed in every environment this runs in, and importing
the real thing would start binding routes for no benefit here, so a
minimal Flask stub is installed before import - just enough surface
(Flask, @app.get/@app.post decorators, jsonify, request, redirect,
Response) for the module to import without a running server.

    python3 -m unittest discover -s tests -v
"""
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


def _install_flask_stub():
    if "flask" in sys.modules:
        return

    flask_module = types.ModuleType("flask")

    class _StubApp:
        def get(self, *_a, **_kw):
            return lambda fn: fn

        def post(self, *_a, **_kw):
            return lambda fn: fn

    class _StubRequest:
        args = {}

        def get_json(self, silent=True):
            return {}

    def _jsonify(*_a, **kw):
        # Real Flask's jsonify() accepts a positional dict OR kwargs -
        # existing app.py code (e.g. oauth_status()) uses the positional
        # form, so the stub needs to merge both to be a faithful double.
        result = {}
        for arg in _a:
            result.update(arg)
        result.update(kw)
        return result

    def _redirect(*_a, **_kw):
        return None

    flask_module.Flask = lambda *_a, **_kw: _StubApp()
    flask_module.jsonify = _jsonify
    flask_module.redirect = _redirect
    flask_module.request = _StubRequest()
    flask_module.Response = object
    sys.modules["flask"] = flask_module


_install_flask_stub()

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ebay-oauth-server"))
import app as oauth_server  # noqa: E402


class ConditionIdToEnumTests(unittest.TestCase):
    """The Inventory API's "condition" field takes a ConditionEnum string
    (e.g. "USED_VERY_GOOD"), not DCardsLab's numeric ConditionID - sending
    the numeric value directly fails with eBay errorId 2004 ("Could not
    serialize field [condition]")."""

    def test_ungraded_and_graded_ids_map_to_documented_enums(self):
        self.assertEqual(oauth_server.condition_id_to_enum("4000"), "USED_VERY_GOOD")
        self.assertEqual(oauth_server.condition_id_to_enum("2750"), "LIKE_NEW")

    def test_never_passes_a_bare_numeric_string_through(self):
        for condition_id in ("1000", "1500", "1750", "2000", "2500", "2750",
                              "3000", "4000", "5000", "6000", "7000"):
            enum_value = oauth_server.condition_id_to_enum(condition_id)
            self.assertFalse(str(enum_value).isdigit(), enum_value)

    def test_unknown_value_passes_through_unchanged(self):
        # Defensive fallback - an already-valid enum string (or a value
        # this table doesn't know about) must not be mangled.
        self.assertEqual(oauth_server.condition_id_to_enum("USED_GOOD"), "USED_GOOD")


class HealthTests(unittest.TestCase):
    """/health exposes the live-configured SCOPES value (not the scope of
    the last-issued token, see /api/oauth/status) - lets a deployer verify
    a scope change (e.g. adding sell.analytics.readonly) actually reached
    the running container, without needing shell access to it."""

    def test_reports_live_configured_scopes(self):
        with patch.object(oauth_server, "SCOPES", "api_scope/sell.inventory api_scope/sell.analytics.readonly"):
            result = oauth_server.health()
        self.assertEqual(result["configured_scopes"], "api_scope/sell.inventory api_scope/sell.analytics.readonly")


class InternalAccessTokenTests(unittest.TestCase):
    """webapp-poc's ebay_client.py calls this endpoint to get a token
    without ever seeing the refresh token itself - oauth-server stays the
    only place that holds eBay credentials."""

    def test_returns_token_when_authorized(self):
        with patch("app.refresh_access_token", return_value={
            "access_token": "tok-123", "expires_in": 7200,
        }):
            result = oauth_server.internal_access_token()
        self.assertEqual(result["access_token"], "tok-123")
        self.assertEqual(result["environment"], oauth_server.ENVIRONMENT)
        self.assertEqual(result["expires_in"], 7200)

    def test_returns_401_shape_when_not_authorized(self):
        with patch("app.refresh_access_token", side_effect=RuntimeError("Kein Refresh Token gespeichert.")):
            result, status = oauth_server.internal_access_token()
        self.assertEqual(status, 401)
        self.assertFalse(result["authorized"])


class GetApplicationAccessTokenTests(unittest.TestCase):
    """Client-credentials token for eBay's Buy APIs (Browse) - independent
    of the Sell-API user consent flow, so no stored refresh token needed."""

    def setUp(self):
        oauth_server._app_token_cache["access_token"] = None
        oauth_server._app_token_cache["expires_at"] = 0
        self.addCleanup(lambda: oauth_server._app_token_cache.update(access_token=None, expires_at=0))

    def test_requests_a_fresh_token_when_cache_is_empty(self):
        response = MagicMock()
        response.read.return_value = json.dumps({"access_token": "app-tok-1", "expires_in": 7200}).encode()
        response.__enter__.return_value = response
        with patch("app.urlopen", return_value=response) as mock_urlopen:
            token = oauth_server.get_application_access_token()
        self.assertEqual(token, "app-tok-1")
        request_obj = mock_urlopen.call_args[0][0]
        self.assertIn(b"grant_type=client_credentials", request_obj.data)

    def test_returns_cached_token_without_a_new_request(self):
        oauth_server._app_token_cache["access_token"] = "cached-tok"
        oauth_server._app_token_cache["expires_at"] = time.time() + 3600
        with patch("app.urlopen") as mock_urlopen:
            token = oauth_server.get_application_access_token()
        self.assertEqual(token, "cached-tok")
        mock_urlopen.assert_not_called()

    def test_refetches_once_the_cached_token_is_close_to_expiry(self):
        oauth_server._app_token_cache["access_token"] = "stale-tok"
        oauth_server._app_token_cache["expires_at"] = time.time() + 10
        response = MagicMock()
        response.read.return_value = json.dumps({"access_token": "fresh-tok", "expires_in": 7200}).encode()
        response.__enter__.return_value = response
        with patch("app.urlopen", return_value=response):
            token = oauth_server.get_application_access_token()
        self.assertEqual(token, "fresh-tok")


class InternalApplicationAccessTokenTests(unittest.TestCase):
    def test_returns_token_when_available(self):
        with patch("app.get_application_access_token", return_value="app-tok"):
            result = oauth_server.internal_application_access_token()
        self.assertEqual(result["access_token"], "app-tok")
        self.assertEqual(result["environment"], oauth_server.ENVIRONMENT)

    def test_returns_502_shape_on_failure(self):
        with patch("app.get_application_access_token", side_effect=RuntimeError("eBay Token API HTTP 500: boom")):
            result, status = oauth_server.internal_application_access_token()
        self.assertEqual(status, 502)
        self.assertFalse(result["authorized"])


if __name__ == "__main__":
    unittest.main()
