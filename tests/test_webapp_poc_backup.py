"""Tests for webapp-poc/backup.py."""
import io
import sys
import zipfile
import unittest
from datetime import datetime, timedelta, timezone
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
            "backup.db.all_inventory": MagicMock(return_value=[]),
            "backup.db.all_price_research": MagicMock(return_value=[]),
            "backup.db.all_manual_sales": MagicMock(return_value=[]),
            "backup.db.all_wishlist_items": MagicMock(return_value=[]),
            "backup.db.all_wishlist_price_checks": MagicMock(return_value=[]),
            "backup.db.all_description_templates": MagicMock(return_value=[]),
            "backup.db.all_portfolio_value_snapshots": MagicMock(return_value=[]),
            "backup.db.all_dashboard_goals": MagicMock(return_value=[]),
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
        for table in (
            "scan_batches", "cards", "purchases", "purchase_items",
            "ebay_listings", "ebay_sales", "inventory", "price_research",
            "manual_sales", "wishlist_items", "wishlist_price_checks",
            "description_templates", "portfolio_value_snapshots", "dashboard_goals",
        ):
            self.assertIn(f"{table}.json", names)

    def test_excludes_tables_with_credentials(self):
        # google_sheets_settings (refresh_token) und app_status (smtp_password)
        # duerfen nie im Backup landen, siehe _TABLE_NAMES-Kommentar.
        self._patch_db()
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.return_value = b"fake-image-bytes"
        with patch("backup.get_client", return_value=mock_client):
            data = backup.build_backup_zip()
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        self.assertNotIn("google_sheets_settings.json", names)
        self.assertNotIn("app_status.json", names)

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

    def test_skips_a_table_that_does_not_exist_yet_instead_of_crashing(self):
        # z.B. eine Migration (wie price_research), die in dieser Supabase-
        # Instanz noch nicht eingespielt wurde - der Rest des Backups muss
        # trotzdem fertig werden.
        self._patch_db(**{"backup.db.all_price_research": MagicMock(side_effect=RuntimeError('relation "price_research" does not exist'))})
        mock_client = MagicMock()
        mock_client.storage.from_.return_value.download.return_value = b"fake-image-bytes"
        with patch("backup.get_client", return_value=mock_client):
            data = backup.build_backup_zip()  # must not raise
        with zipfile.ZipFile(io.BytesIO(data)) as zf:
            names = zf.namelist()
        self.assertIn("cards.json", names)
        self.assertNotIn("price_research.json", names)


class RunScheduledBackupOnceTests(unittest.TestCase):
    def test_uploads_and_records_when_never_backed_up(self):
        with patch("backup.db.get_app_status", return_value=None), \
             patch("backup.build_backup_zip", return_value=b"fake-zip-bytes"), \
             patch("backup.storage.upload_backup") as mock_upload, \
             patch("backup.storage.prune_old_backups") as mock_prune, \
             patch("backup.db.record_auto_backup") as mock_record:
            backup.run_scheduled_backup_once()
        mock_upload.assert_called_once()
        filename, data = mock_upload.call_args[0]
        self.assertTrue(filename.startswith("backup-") and filename.endswith(".zip"))
        self.assertEqual(data, b"fake-zip-bytes")
        mock_prune.assert_called_once()
        mock_record.assert_called_once()

    def test_skips_when_last_backup_within_interval(self):
        recent = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with patch("backup.db.get_app_status", return_value={"last_auto_backup_at": recent}), \
             patch("backup.build_backup_zip") as mock_build, \
             patch("backup.storage.upload_backup") as mock_upload:
            backup.run_scheduled_backup_once()
        mock_build.assert_not_called()
        mock_upload.assert_not_called()

    def test_runs_when_last_backup_older_than_interval(self):
        old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
        with patch("backup.db.get_app_status", return_value={"last_auto_backup_at": old}), \
             patch("backup.build_backup_zip", return_value=b"fake-zip-bytes"), \
             patch("backup.storage.upload_backup") as mock_upload, \
             patch("backup.storage.prune_old_backups"), \
             patch("backup.db.record_auto_backup"):
            backup.run_scheduled_backup_once()
        mock_upload.assert_called_once()

    def test_does_not_crash_when_upload_fails(self):
        with patch("backup.db.get_app_status", return_value=None), \
             patch("backup.build_backup_zip", return_value=b"fake-zip-bytes"), \
             patch("backup.storage.upload_backup", side_effect=RuntimeError("bucket down")), \
             patch("backup.db.record_auto_backup") as mock_record:
            backup.run_scheduled_backup_once()  # must not raise
        mock_record.assert_not_called()


class RunBackupNowTests(unittest.TestCase):
    # Manuelles Antriggern (Einstellungen-Button) - anders als
    # run_scheduled_backup_once() bewusst OHNE den _is_backup_due()-Gate,
    # da ein expliziter Klick nicht durch den 7-Tage-Automatik-Rhythmus
    # blockiert werden soll.
    def test_uploads_regardless_of_last_backup_time(self):
        recent = datetime.now(timezone.utc).isoformat()
        with patch("backup.db.get_app_status", return_value={"last_auto_backup_at": recent}), \
             patch("backup.build_backup_zip", return_value=b"fake-zip-bytes"), \
             patch("backup.storage.upload_backup") as mock_upload, \
             patch("backup.storage.prune_old_backups") as mock_prune, \
             patch("backup.db.record_auto_backup") as mock_record:
            backup.run_backup_now()
        mock_upload.assert_called_once()
        mock_prune.assert_called_once()
        mock_record.assert_called_once()

    def test_propagates_errors_to_caller(self):
        with patch("backup.build_backup_zip", return_value=b"fake-zip-bytes"), \
             patch("backup.storage.upload_backup", side_effect=RuntimeError("bucket down")):
            with self.assertRaises(RuntimeError):
                backup.run_backup_now()


if __name__ == "__main__":
    unittest.main()
