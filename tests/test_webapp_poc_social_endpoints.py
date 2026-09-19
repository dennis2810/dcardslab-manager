"""Tests for /api/instagram/* and /api/social/video (webapp-poc/main.py).
Same structure as tests/test_webapp_poc_sheets_endpoints.py."""
import io
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

for _name in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scanner"))
sys.path.insert(0, str(REPO_ROOT / "integrations"))
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402
import instagram_client  # noqa: E402
import social_video  # noqa: E402

client = TestClient(main.app, follow_redirects=False)


class InstagramStatusEndpointTests(unittest.TestCase):
    def test_not_connected_when_no_settings(self):
        with patch("main.db.get_instagram_settings", return_value=None):
            response = client.get("/api/instagram/status")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["connected"])

    def test_connected_when_token_and_ig_user_id_present(self):
        settings = {
            "access_token": "tok", "ig_user_id": "ig-1", "username": "dcardslab",
            "connected_at": "2026-09-19T00:00:00+00:00", "last_synced_at": None,
        }
        with patch("main.db.get_instagram_settings", return_value=settings):
            response = client.get("/api/instagram/status")
        body = response.json()
        self.assertTrue(body["connected"])
        self.assertEqual(body["username"], "dcardslab")

    def test_not_connected_when_token_present_but_no_ig_user_id(self):
        settings = {"access_token": "tok", "ig_user_id": ""}
        with patch("main.db.get_instagram_settings", return_value=settings):
            response = client.get("/api/instagram/status")
        self.assertFalse(response.json()["connected"])


class InstagramOauthStartEndpointTests(unittest.TestCase):
    def test_redirects_to_facebook_auth_base(self):
        response = client.get("/api/instagram/oauth/start")
        self.assertEqual(response.status_code, 307)
        self.assertTrue(response.headers["location"].startswith(instagram_client.AUTH_BASE))


class InstagramOauthCallbackEndpointTests(unittest.TestCase):
    def test_error_param_redirects_with_error(self):
        with patch("main.instagram_client.exchange_code") as mock_exchange:
            response = client.get("/api/instagram/oauth/callback?error=access_denied")
        mock_exchange.assert_not_called()
        self.assertIn("instagram_error=access_denied", response.headers["location"])

    def test_missing_or_unknown_state_redirects_with_error(self):
        with patch("main.instagram_client.exchange_code") as mock_exchange:
            response = client.get("/api/instagram/oauth/callback?code=abc&state=unknown-state")
        mock_exchange.assert_not_called()
        self.assertIn("instagram_error=", response.headers["location"])

    def test_valid_flow_saves_settings_and_redirects_to_social(self):
        start_response = client.get("/api/instagram/oauth/start")
        state = start_response.headers["location"].split("state=")[1].split("&")[0]

        with patch("main.instagram_client.exchange_code", return_value=("short-lived", "ig-1")) as mock_exchange, \
             patch("main.instagram_client.exchange_for_long_lived_token", return_value="long-lived"), \
             patch("main.instagram_client.get_account_summary", return_value={"username": "dcardslab"}), \
             patch("main.db.save_instagram_settings") as mock_save:
            response = client.get(f"/api/instagram/oauth/callback?code=abc&state={state}")
        mock_exchange.assert_called_once_with("abc")
        saved = mock_save.call_args[0][0]
        self.assertEqual(saved["access_token"], "long-lived")
        self.assertEqual(saved["ig_user_id"], "ig-1")
        self.assertEqual(saved["username"], "dcardslab")
        self.assertEqual(response.headers["location"], "/social.html")

    def test_api_error_during_exchange_redirects_with_error(self):
        start_response = client.get("/api/instagram/oauth/start")
        state = start_response.headers["location"].split("state=")[1].split("&")[0]
        with patch("main.instagram_client.exchange_code",
                   side_effect=instagram_client.InstagramApiError("boom")):
            response = client.get(f"/api/instagram/oauth/callback?code=abc&state={state}")
        self.assertIn("instagram_error=", response.headers["location"])


class InstagramDisconnectEndpointTests(unittest.TestCase):
    def test_clears_settings(self):
        with patch("main.db.save_instagram_settings") as mock_save:
            response = client.post("/api/instagram/disconnect")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.json()["connected"])
        mock_save.assert_called_once_with({"access_token": "", "ig_user_id": "", "username": ""})


class InstagramInsightsEndpointTests(unittest.TestCase):
    def test_returns_401_when_not_connected(self):
        with patch("main.db.get_instagram_settings", return_value=None):
            response = client.get("/api/instagram/insights")
        self.assertEqual(response.status_code, 401)

    def test_returns_summary_and_insights_when_connected(self):
        settings = {"access_token": "tok", "ig_user_id": "ig-1"}
        summary = {"username": "dcardslab", "followers_count": 42, "media_count": 3}
        insights = [{"name": "reach", "values": [{"value": 100}]}]
        with patch("main.db.get_instagram_settings", return_value=settings), \
             patch("main.db.save_instagram_settings") as mock_save, \
             patch("main.instagram_client.get_account_summary", return_value=summary), \
             patch("main.instagram_client.get_insights", return_value=insights):
            response = client.get("/api/instagram/insights")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["summary"]["followers_count"], 42)
        self.assertEqual(body["insights"][0]["name"], "reach")
        self.assertIn("last_synced_at", mock_save.call_args[0][0])

    def test_returns_401_on_not_connected_error(self):
        settings = {"access_token": "tok", "ig_user_id": "ig-1"}
        with patch("main.db.get_instagram_settings", return_value=settings), \
             patch("main.instagram_client.get_account_summary",
                   side_effect=instagram_client.InstagramNotConnectedError("expired")):
            response = client.get("/api/instagram/insights")
        self.assertEqual(response.status_code, 401)

    def test_returns_502_on_api_error(self):
        settings = {"access_token": "tok", "ig_user_id": "ig-1"}
        with patch("main.db.get_instagram_settings", return_value=settings), \
             patch("main.instagram_client.get_account_summary", side_effect=instagram_client.InstagramApiError("boom")):
            response = client.get("/api/instagram/insights")
        self.assertEqual(response.status_code, 502)


def _fake_jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (200, 300), color=(0, 255, 0)).save(buf, format="JPEG")
    return buf.getvalue()


class GenerateSocialVideoEndpointTests(unittest.TestCase):
    def test_returns_400_when_no_card_ids(self):
        response = client.post("/api/social/video", json={"card_ids": []})
        self.assertEqual(response.status_code, 400)

    def test_returns_400_when_too_many_card_ids(self):
        response = client.post("/api/social/video", json={"card_ids": ["c"] * (social_video.MAX_CARDS + 1)})
        self.assertEqual(response.status_code, 400)

    def test_returns_503_when_ffmpeg_unavailable(self):
        with patch("main.social_video.ffmpeg_available", return_value=False):
            response = client.post("/api/social/video", json={"card_ids": ["c1"]})
        self.assertEqual(response.status_code, 503)

    def test_returns_404_when_no_cards_found(self):
        with patch("main.social_video.ffmpeg_available", return_value=True), \
             patch("main.db.get_cards_by_ids", return_value=[]):
            response = client.post("/api/social/video", json={"card_ids": ["c1"]})
        self.assertEqual(response.status_code, 404)

    def test_returns_422_when_no_photo_could_be_loaded(self):
        card = {"id": "c1", "title": "Karte 1", "front_image_path": "p1"}
        with patch("main.social_video.ffmpeg_available", return_value=True), \
             patch("main.db.get_cards_by_ids", return_value=[card]), \
             patch("main.storage.signed_urls", return_value={}):
            response = client.post("/api/social/video", json={"card_ids": ["c1"]})
        self.assertEqual(response.status_code, 422)

    def test_builds_reel_and_returns_video(self):
        card = {"id": "c1", "title": "Karte 1", "front_image_path": "p1"}
        listing = {"price": 9.99}
        fake_photo_response = MagicMock()
        fake_photo_response.content = _fake_jpeg_bytes()
        fake_photo_response.raise_for_status = MagicMock()

        def fake_build_reel(cards, output_path):
            Path(output_path).write_bytes(b"fake-mp4-bytes")

        with patch("main.social_video.ffmpeg_available", return_value=True), \
             patch("main.db.get_cards_by_ids", return_value=[card]), \
             patch("main.storage.signed_urls", return_value={"p1": "https://img.example/p1.jpg"}), \
             patch("main.httpx.get", return_value=fake_photo_response), \
             patch("main.db.get_ebay_listing_for_card", return_value=listing), \
             patch("main.social_video.build_reel", side_effect=fake_build_reel) as mock_build:
            response = client.post("/api/social/video", json={"card_ids": ["c1"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "video/mp4")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertEqual(response.content, b"fake-mp4-bytes")
        payload = mock_build.call_args[0][0]
        self.assertEqual(payload[0]["title"], "Karte 1")
        self.assertIn("9.99", payload[0]["price_text"])

    def test_returns_502_on_video_generation_error(self):
        card = {"id": "c1", "title": "Karte 1", "front_image_path": "p1"}
        fake_photo_response = MagicMock()
        fake_photo_response.content = _fake_jpeg_bytes()
        fake_photo_response.raise_for_status = MagicMock()

        with patch("main.social_video.ffmpeg_available", return_value=True), \
             patch("main.db.get_cards_by_ids", return_value=[card]), \
             patch("main.storage.signed_urls", return_value={"p1": "https://img.example/p1.jpg"}), \
             patch("main.httpx.get", return_value=fake_photo_response), \
             patch("main.db.get_ebay_listing_for_card", return_value=None), \
             patch("main.social_video.build_reel", side_effect=social_video.VideoGenerationError("boom")):
            response = client.post("/api/social/video", json={"card_ids": ["c1"]})
        self.assertEqual(response.status_code, 502)


if __name__ == "__main__":
    unittest.main()
