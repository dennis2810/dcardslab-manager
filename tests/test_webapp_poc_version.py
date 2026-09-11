"""Tests for GET /api/version (webapp-poc/main.py) - lets a deployer verify
via a simple HTTP request whether an expected PR/commit actually reached the
running container, without needing shell access to the deployment host (same
motivation as ebay-oauth-server's /health with configured_scopes)."""
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

client = TestClient(main.app)


class VersionEndpointTests(unittest.TestCase):
    def test_reports_git_commit_when_set(self):
        with patch.object(main, "GIT_COMMIT", "abc1234"):
            response = client.get("/api/version")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["git_commit"], "abc1234")

    def test_reports_build_time_when_set(self):
        with patch.object(main, "BUILD_TIME", "2026-09-11 14:00 UTC"):
            response = client.get("/api/version")
        self.assertEqual(response.json()["built_at"], "2026-09-11 14:00 UTC")

    def test_defaults_to_unbekannt_outside_a_docker_build(self):
        # Kein --build-arg gesetzt / kein BUILD_TIME-File vorhanden (z.B.
        # lokaler Testlauf ausserhalb eines Docker-Builds).
        with patch.object(main, "GIT_COMMIT", "unbekannt"), \
             patch.object(main, "BUILD_TIME", "unbekannt"):
            response = client.get("/api/version")
        self.assertEqual(response.json(), {"git_commit": "unbekannt", "built_at": "unbekannt"})


if __name__ == "__main__":
    unittest.main()
