"""Tests for webapp-poc/storage.py - image compression + Supabase Storage
upload/signed-URL wrappers."""
import io
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))
import storage  # noqa: E402


def _write_fixture_image(tmp_path, size=(2400, 3200)):
    img = Image.new("RGB", size, color=(200, 50, 50))
    path = tmp_path / "fixture.png"
    img.save(path, format="PNG")
    return path


class CompressImageTests(unittest.TestCase):
    def test_downscales_large_image_to_max_edge(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _write_fixture_image(Path(tmp))
            data = storage.compress_image(fixture)
        result_img = Image.open(io.BytesIO(data))
        self.assertLessEqual(max(result_img.size), storage._MAX_EDGE)
        self.assertEqual(result_img.format, "JPEG")

    def test_leaves_small_image_edge_length_unchanged(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _write_fixture_image(Path(tmp), size=(400, 300))
            data = storage.compress_image(fixture)
        result_img = Image.open(io.BytesIO(data))
        self.assertEqual(result_img.size, (400, 300))


class UploadImageTests(unittest.TestCase):
    def test_uploads_compressed_bytes_to_expected_path(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _write_fixture_image(Path(tmp))
            mock_client = MagicMock()
            with patch("storage.get_client", return_value=mock_client):
                object_path = storage.upload_image("batch-123", 3, "front", fixture)

        self.assertEqual(object_path, "batch-123/3_front.jpg")
        mock_client.storage.from_.assert_called_once_with(storage.BUCKET)
        upload_call = mock_client.storage.from_.return_value.upload
        upload_call.assert_called_once()
        args, kwargs = upload_call.call_args
        self.assertEqual(args[0], "batch-123/3_front.jpg")
        self.assertEqual(kwargs["file_options"]["content-type"], "image/jpeg")

    def test_propagates_upload_errors_to_caller(self):
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            fixture = _write_fixture_image(Path(tmp))
            mock_client = MagicMock()
            mock_client.storage.from_.return_value.upload.side_effect = RuntimeError("bucket down")
            with patch("storage.get_client", return_value=mock_client):
                with self.assertRaises(RuntimeError):
                    storage.upload_image("batch-123", 3, "front", fixture)


class SignedUrlTests(unittest.TestCase):
    def test_returns_signed_url_from_client_response(self):
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.create_signed_url.return_value = {
            "signedURL": "https://example.supabase.co/signed/abc"
        }
        with patch("storage.get_client", return_value=mock_client):
            url = storage.signed_url("batch-123/3_front.jpg")
        self.assertEqual(url, "https://example.supabase.co/signed/abc")
        mock_client.storage.from_.return_value.create_signed_url.assert_called_once_with(
            "batch-123/3_front.jpg", 3600
        )


class DeleteImagesTests(unittest.TestCase):
    def test_removes_given_paths(self):
        mock_client = MagicMock()
        with patch("storage.get_client", return_value=mock_client):
            storage.delete_images(["b1/1_front.jpg", "b1/1_back.jpg"])
        mock_client.storage.from_.assert_called_once_with(storage.BUCKET)
        mock_client.storage.from_.return_value.remove.assert_called_once_with(
            ["b1/1_front.jpg", "b1/1_back.jpg"]
        )

    def test_filters_out_none_paths(self):
        mock_client = MagicMock()
        with patch("storage.get_client", return_value=mock_client):
            storage.delete_images(["b1/1_front.jpg", None])
        mock_client.storage.from_.return_value.remove.assert_called_once_with(["b1/1_front.jpg"])

    def test_noop_when_no_paths(self):
        mock_client = MagicMock()
        with patch("storage.get_client", return_value=mock_client):
            storage.delete_images([])
        mock_client.storage.from_.assert_not_called()


if __name__ == "__main__":
    unittest.main()
