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


class RotateImageTests(unittest.TestCase):
    _MARKER = (0, 255, 0)

    def _stub_download(self, mock_client, size=(100, 60), mark_size=20):
        img = Image.new("RGB", size, color=(200, 50, 50))
        # Mark a solid block in the top-left corner so rotation direction is
        # verifiable even after the lossy JPEG round-trip - a single pixel
        # doesn't reliably survive JPEG's 8x8 DCT blocks, a solid block does.
        for x in range(mark_size):
            for y in range(mark_size):
                img.putpixel((x, y), self._MARKER)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=95)
        mock_client.storage.from_.return_value.download.return_value = buf.getvalue()
        return img

    def _assert_region_is_marker(self, img, x0, y0, size=20):
        # Average over the region instead of a single pixel, and allow some
        # JPEG-compression slack instead of exact equality.
        r = g = b = 0
        for x in range(x0, x0 + size):
            for y in range(y0, y0 + size):
                px = img.getpixel((x, y))
                r += px[0]
                g += px[1]
                b += px[2]
        count = size * size
        avg = (r // count, g // count, b // count)
        self.assertLess(avg[0], 60)
        self.assertGreater(avg[1], 200)
        self.assertLess(avg[2], 60)

    def test_downloads_rotates_and_reuploads_to_same_path(self):
        mock_client = MagicMock()
        self._stub_download(mock_client, size=(100, 60))
        with patch("storage.get_client", return_value=mock_client):
            storage.rotate_image("batch-1/3_front.jpg", 90)

        mock_client.storage.from_.return_value.download.assert_called_once_with("batch-1/3_front.jpg")
        upload_call = mock_client.storage.from_.return_value.upload
        upload_call.assert_called_once()
        args, kwargs = upload_call.call_args
        self.assertEqual(args[0], "batch-1/3_front.jpg")
        self.assertEqual(kwargs["file_options"]["upsert"], "true")

        rotated = Image.open(io.BytesIO(args[1])).convert("RGB")
        self.assertEqual(rotated.size, (60, 100))  # dimensions swap on a 90-degree turn
        # A 90-degree clockwise turn moves the top-left marker to the top-right.
        self._assert_region_is_marker(rotated, 40, 0)

    def test_180_degrees_flips_content_but_keeps_dimensions(self):
        mock_client = MagicMock()
        self._stub_download(mock_client, size=(100, 60))
        with patch("storage.get_client", return_value=mock_client):
            storage.rotate_image("batch-1/3_back.jpg", 180)

        args, _ = mock_client.storage.from_.return_value.upload.call_args
        rotated = Image.open(io.BytesIO(args[1])).convert("RGB")
        self.assertEqual(rotated.size, (100, 60))
        # The marked block was top-left; after a 180-degree turn it belongs
        # in the bottom-right corner instead.
        self._assert_region_is_marker(rotated, 80, 40)


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
