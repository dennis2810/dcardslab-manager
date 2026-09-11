"""Tests for webapp-poc/main.py's optional login (APP_PASSWORD + session
cookie, see _require_login()/login()/logout())."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

for _name in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scanner"))
sys.path.insert(0, str(REPO_ROOT / "integrations"))
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402


class DisabledByDefaultTests(unittest.TestCase):
    """main.APP_PASSWORD is "" unless the test explicitly patches it -
    every other test file's requests must keep working unauthenticated."""

    def test_protected_looking_page_is_not_gated_without_app_password(self):
        client = TestClient(main.app, follow_redirects=False)
        response = client.get("/dashboard.html")
        self.assertEqual(response.status_code, 200)

    def test_protected_looking_api_is_not_gated_without_app_password(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.db.list_ebay_listings", return_value=[]):
            response = client.get("/api/ebay/listings")
        self.assertEqual(response.status_code, 200)


class RequireLoginGateTests(unittest.TestCase):
    def test_unauthenticated_page_request_redirects_to_login_with_next(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", "s3cret"):
            response = client.get("/dashboard.html")
        self.assertEqual(response.status_code, 303)
        self.assertIn("/login.html", response.headers["location"])
        self.assertIn("next=%2Fdashboard.html", response.headers["location"])

    def test_unauthenticated_api_request_returns_401_json(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", "s3cret"):
            response = client.get("/api/ebay/listings")
        self.assertEqual(response.status_code, 401)
        self.assertIn("angemeldet", response.json()["detail"])

    def test_login_page_itself_stays_reachable(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", "s3cret"):
            response = client.get("/login.html")
        self.assertEqual(response.status_code, 200)

    def test_login_endpoint_stays_reachable_unauthenticated(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", "s3cret"):
            response = client.post("/api/login", json={"password": "wrong"})
        # 401 wegen falschem Passwort, nicht wegen des Gates selbst (sonst
        # gaebe es gar keine Moeglichkeit, sich anzumelden).
        self.assertEqual(response.status_code, 401)

    def test_static_assets_stay_reachable(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", "s3cret"):
            response = client.get("/assets/dcardslab-logo.png")
        self.assertEqual(response.status_code, 200)
        with patch("main.APP_PASSWORD", "s3cret"):
            response = client.get("/manifest.json")
        self.assertEqual(response.status_code, 200)
        with patch("main.APP_PASSWORD", "s3cret"):
            response = client.get("/sw.js")
        self.assertEqual(response.status_code, 200)


class LoginEndpointTests(unittest.TestCase):
    def test_rejects_wrong_password(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", "s3cret"):
            response = client.post("/api/login", json={"password": "wrong"})
        self.assertEqual(response.status_code, 401)

    def test_rejects_when_app_password_not_configured(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", ""):
            response = client.post("/api/login", json={"password": "anything"})
        self.assertEqual(response.status_code, 401)

    def test_accepts_correct_password_and_grants_access(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", "s3cret"):
            login_response = client.post("/api/login", json={"password": "s3cret"})
            self.assertEqual(login_response.status_code, 200)
            self.assertTrue(login_response.json()["ok"])
            with patch("main.db.list_ebay_listings", return_value=[]):
                response = client.get("/api/ebay/listings")
            self.assertEqual(response.status_code, 200)

    def test_logout_revokes_access(self):
        client = TestClient(main.app, follow_redirects=False)
        with patch("main.APP_PASSWORD", "s3cret"):
            client.post("/api/login", json={"password": "s3cret"})
            logout_response = client.post("/api/logout")
            self.assertEqual(logout_response.status_code, 200)
            response = client.get("/dashboard.html")
        self.assertEqual(response.status_code, 303)


if __name__ == "__main__":
    unittest.main()
