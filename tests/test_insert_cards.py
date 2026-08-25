"""Regression test for insert_cards() writing the AI-recognized team into
the actual `team` column, not just the internal `ocr_team` column.

Bug: the scan grid's "Team" column reads cards.team (see refresh()'s
SELECT), but insert_cards()'s INSERT column list never included `team` -
only `ocr_team`. So every scanned card showed an empty Team cell even
when the AI correctly recognized the club, matching what a user reported
after the AI-recognition switch actually started returning team data.

Uses a real (temporary, isolated) SQLite database and image files - the
app module normally requires tkinter/OpenCV just to import, so those are
stubbed first, same as tests/test_ebay_export.py.

    python3 -m unittest discover -s tests -v
"""
import sys
import tempfile
import types
import unittest
from pathlib import Path


def _stub(name):
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


_stub("cv2")
_stub("numpy")
_tkinter = _stub("tkinter")
for _sub in ("ttk", "filedialog", "messagebox", "simpledialog"):
    setattr(_tkinter, _sub, _stub(f"tkinter.{_sub}"))

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))
import dcardlabs_manager as app  # noqa: E402


class InsertCardsTeamColumnTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        tmp_path = Path(self._tmp.name)

        # Isolate every path insert_cards()/db() touch from the real repo.
        self._patched = {
            "DB": tmp_path / "test.db",
            "IMAGE_ROOT": tmp_path / "images" / "cards",
            "BACKUP_ROOT": tmp_path / "backups",
            "LOG_ROOT": tmp_path / "logs",
        }
        self._originals = {name: getattr(app, name) for name in self._patched}
        for name, value in self._patched.items():
            setattr(app, name, value)
        self.addCleanup(lambda: [setattr(app, n, v) for n, v in self._originals.items()])

        front = tmp_path / "front.jpg"
        back = tmp_path / "back.jpg"
        front.write_bytes(b"fake-front-image")
        back.write_bytes(b"fake-back-image")
        self.front, self.back = front, back

    def test_team_is_written_to_the_team_column_not_only_ocr_team(self):
        pair = {
            "number": 1, "title": "Test Spieler", "ocr_status": "ok",
            "ocr_confidence": 90, "ocr_raw": "", "front": str(self.front),
            "back": str(self.back), "team": "Bayern München",
            "theme": "Bundesliga", "category": "Fußball",
        }
        app.insert_cards([pair], batch_id=1)

        c = app.db()
        row = c.execute("SELECT team, ocr_team FROM cards WHERE title=?", ("Test Spieler",)).fetchone()
        c.close()

        self.assertIsNotNone(row, "insert_cards() did not insert a row")
        team, ocr_team = row
        self.assertEqual(team, "Bayern München")
        self.assertEqual(ocr_team, "Bayern München")


if __name__ == "__main__":
    unittest.main()
