"""Tests for webapp-poc/backup.py."""
import io
import sys
import zipfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import backup  # noqa: E402


def _cards():
    return [
        {"id": "c1", "front_image_path": "b1/1_front.jpg", "back_image_path": "b1/1_back.jpg"},
        {"id": "c2", "front_image_path": None, "back_image_path": None},
    ]


class BuildBackupZipTests(unittest.TestCase):
    def _patch_db(self, **overrides):
        patches = {
            "backup.db.all_scan_batches": MagicMock(return_value=[{"id": "b1"}]),
            "backup.db.all_cards": MagicMock(return_value=_cards()),
            "backup.db.all_purchases": MagicMock(return_value=[]),
            "backup.db.all_purchase_items": MagicMock(return_value=[]),
            "backup.db.all_ebay_listings": MagicMock(return_value=[]),
            "backup.db.all_ebay_sales": MagicMock(return_value=[]),
        }
        patches.update(overrides)
        patchers = [patch(target, new) for target, new in patches.items()]
        for p in patchers:
            self.addCleanup(p.stop)
        return {target: p.start() for target, p in zip(patches, patchers)}

    def test_contains_all_table_json_files(self):
        self._patch_db()
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.return_value = b"fake-image-bytes"
        with patch("backup.get_client", return_value=mock_client):
            data = backup.build_backup_zip()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        for table in ("scan_batches", "cards", "purchases", "purchase_items", "ebay_listings", "ebay_sales"):
            self.assertIn(f"{table}.json", names)

    def test_includes_images_for_cards_that_have_them(self):
        self._patch_db()
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.return_value = b"fake-image-bytes"
        with patch("backup.get_client", return_value=mock_client):
            data = backup.build_backup_zip()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        self.assertIn("images/b1/1_front.jpg", names)
        self.assertIn("images/b1/1_back.jpg", names)

    def test_skips_image_that_fails_to_download_instead_of_crashing(self):
        self._patch_db()
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.side_effect = RuntimeError("Storage down")
        with patch("backup.get_client", return_value=mock_client):
            data = backup.build_backup_zip()  # must not raise
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        self.assertIn("cards.json", names)  # tables still present
        self.assertNotIn("images/b1/1_front.jpg", names)

    def test_does_not_query_images_for_cards_without_paths(self):
        self._patch_db()
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.return_value = b"fake-image-bytes"
        with patch("backup.get_client", return_value=mock_client):
            backup.build_backup_zip()
        # Only 2 downloads (front+back for c1) - c2 has no image paths.
        self.assertEqual(mock_client.storage.from_.return_value.download.call_count, 2)


if __name__ == "__main__":
    unittest.main()
