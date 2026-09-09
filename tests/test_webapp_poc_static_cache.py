"""Tests for the no-cache-for-HTML middleware (webapp-poc/main.py) - ensures
static pages (help.html, release-notes.html, ...) always revalidate with the
server instead of being served from a browser's heuristic cache after a
deploy."""
import sys
import types
import unittest
from pathlib import Path

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


class NoCacheForHtmlMiddlewareTests(unittest.TestCase):
    def test_sets_no_cache_for_html_page(self):
        response = client.get("/release-notes.html")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_sets_no_cache_for_root(self):
        response = client.get("/")
        self.assertEqual(response.headers.get("cache-control"), "no-cache")

    def test_leaves_non_html_assets_untouched(self):
        response = client.get("/manifest.json")
        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(response.headers.get("cache-control"), "no-cache")


if __name__ == "__main__":
    unittest.main()
