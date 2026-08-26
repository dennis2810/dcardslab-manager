"""Tests for POST /api/scan persisting to Supabase (webapp-poc/main.py)."""
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

for _name in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scanner"))
sys.path.insert(0, str(REPO_ROOT / "integrations"))
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

from fastapi.testclient import TestClient  # noqa: E402

import main  # noqa: E402

client = TestClient(main.app)


def _fake_crop(upload_path, out_dir, quality, rotate):
    """Stand-in for scanner.process(): writes 9 tiny fake card files
    numbered 001..009 into out_dir, mirroring the real crop's output
    contract (a list of 9 file paths) without running OpenCV."""
    out_dir = Path(out_dir)
    files = []
    for n in range(1, 10):
        p = out_dir / f"{n:03d}.jpg"
        p.write_bytes(b"\xff\xd8\xff\xe0fake-card-image")
        files.append(str(p))
    return files


def _fake_recognize(front_path=None, back_path=None):
    return {
        "title": "Max Mustermann", "category": "Fußball", "theme": "", "manufacturer": "Topps",
        "set_name": "", "season_year": "2024", "card_type": "", "variant": "", "team": "FC Test",
        "position": "", "squad_number": "", "club_debut_season": "", "card_number": "12",
        "serial_number": "", "print_run": "", "is_numbered": 0, "confidence": 90, "raw": "",
        "status": "ok",
    }


class ScanEndpointPersistenceTests(unittest.TestCase):
    def setUp(self):
        self.batch_ids = iter(["batch-1"])
        self.card_counter = 0

        def fake_insert_card(batch_id, position, fields, front_path, back_path):
            self.card_counter += 1
            return {"id": f"card-{position}", "batch_id": batch_id, "position_in_batch": position}

        patches = {
            "main.scanner.process": MagicMock(side_effect=_fake_crop),
            "main.recognize_card": MagicMock(side_effect=_fake_recognize),
            "main.db.create_batch": MagicMock(return_value="batch-1"),
            "main.db.update_batch_status": MagicMock(),
            "main.db.insert_card": MagicMock(side_effect=fake_insert_card),
            "main.storage.upload_image": MagicMock(side_effect=lambda batch_id, pos, side, path: f"{batch_id}/{pos}_{side}.jpg"),
            "main.storage.signed_url": MagicMock(side_effect=lambda object_path, **_: f"https://signed/{object_path}"),
        }
        self._patchers = [patch(target, new) for target, new in patches.items()]
        self.mocks = {}
        for target, p in zip(patches, self._patchers):
            self.mocks[target] = p.start()
            self.addCleanup(p.stop)

    def _post_scan(self):
        files = {
            "front": ("front.jpg", b"fake-front-bytes", "image/jpeg"),
            "back": ("back.jpg", b"fake-back-bytes", "image/jpeg"),
        }
        return client.post("/api/scan", files=files)

    def test_creates_one_batch_for_the_scan(self):
        self._post_scan()
        self.mocks["main.db.create_batch"].assert_called_once_with(card_count=9)

    def test_uploads_both_images_for_every_card(self):
        self._post_scan()
        self.assertEqual(self.mocks["main.storage.upload_image"].call_count, 18)

    def test_inserts_nine_cards(self):
        self._post_scan()
        self.assertEqual(self.mocks["main.db.insert_card"].call_count, 9)

    def test_marks_batch_ok_when_all_cards_succeed(self):
        self._post_scan()
        self.mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "ok")

    def test_response_includes_batch_id_and_card_ids_and_urls(self):
        response = self._post_scan()
        body = response.json()
        self.assertEqual(body["batch_id"], "batch-1")
        self.assertEqual(len(body["cards"]), 9)
        first = body["cards"][0]
        self.assertEqual(first["id"], "card-1")
        self.assertEqual(first["front_image_url"], "https://signed/batch-1/1_front.jpg")
        self.assertEqual(first["title"], "Max Mustermann")

    def test_image_upload_failure_marks_only_that_card(self):
        def upload_side_effect(batch_id, pos, side, path):
            if pos == 5 and side == "front":
                raise RuntimeError("bucket down")
            return f"{batch_id}/{pos}_{side}.jpg"
        self.mocks["main.storage.upload_image"].side_effect = upload_side_effect

        response = self._post_scan()

        body = response.json()
        self.assertEqual(self.mocks["main.db.insert_card"].call_count, 9)
        failed_card = next(c for c in body["cards"] if c["number"] == 5)
        self.assertIn("bucket down", failed_card["image_error"])
        ok_card = next(c for c in body["cards"] if c["number"] == 1)
        self.assertNotIn("image_error", ok_card)
        self.mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "partial")

    def test_missing_back_image_is_persisted_with_german_status(self):
        def crop_with_missing_back(upload_path, out_dir, quality, rotate):
            # _crop_side() requires exactly 9 files back from process(), so
            # the back crop still yields 9 files here - it just never
            # produces a "004" (position 4's back card is substituted with
            # a "010" that back_map's int(p.stem) lookup never matches),
            # mirroring how back_map.get(4) comes back None in production
            # when the grid detector can't find a back card at that slot.
            out_dir = Path(out_dir)
            numbers = list(range(1, 10))
            if out_dir.name == "back_cards":
                numbers = [n for n in numbers if n != 4] + [10]
            files = []
            for n in numbers:
                p = out_dir / f"{n:03d}.jpg"
                p.write_bytes(b"\xff\xd8\xff\xe0fake-card-image")
                files.append(str(p))
            return files
        self.mocks["main.scanner.process"].side_effect = crop_with_missing_back

        response = self._post_scan()

        body = response.json()
        self.assertEqual(len(body["cards"]), 9)
        missing_card = next(c for c in body["cards"] if c["number"] == 4)
        self.assertIn("fehlt", missing_card["status"])

        insert_calls_for_4 = [
            call for call in self.mocks["main.db.insert_card"].call_args_list
            if call.args[1] == 4
        ]
        self.assertEqual(len(insert_calls_for_4), 1)
        self.assertIsNone(insert_calls_for_4[0].args[3])  # front_image_path
        self.assertIsNone(insert_calls_for_4[0].args[4])  # back_image_path

    def test_batch_marked_failed_when_every_card_fails(self):
        self.mocks["main.storage.upload_image"].side_effect = RuntimeError("network down")

        self._post_scan()

        self.mocks["main.db.update_batch_status"].assert_called_once_with("batch-1", "failed")


if __name__ == "__main__":
    unittest.main()
