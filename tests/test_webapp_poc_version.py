"""Tests for GET /api/version (webapp-poc/main.py) - lets a deployer verify
via a simple HTTP request whether an expected PR/commit actually reached the
running container, without needing shell access to the deployment host (same
motivation as ebay-oauth-server's /health with configured_scopes). All values
come from files the Dockerfile writes automatically at build time (a
dedicated git-info build stage plus a plain "date" call) - no --build-arg,
no git needed on the deployment host itself."""
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


class ExtractPrNumberTests(unittest.TestCase):
    def test_extracts_pr_number_from_a_github_merge_commit_subject(self):
        self.assertEqual(main._extract_pr_number("Merge pull request #89 from dennis2810/branch"), 89)

    def test_returns_none_for_a_subject_without_a_pr_reference(self):
        self.assertIsNone(main._extract_pr_number("Fix typo in README"))

    def test_returns_none_for_unbekannt(self):
        self.assertIsNone(main._extract_pr_number("unbekannt"))


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

    def test_reports_last_pr_and_commit_subject_when_set(self):
        with patch.object(main, "LAST_COMMIT_SUBJECT", "Merge pull request #89 from dennis2810/branch"), \
             patch.object(main, "LAST_PR", 89):
            response = client.get("/api/version")
        body = response.json()
        self.assertEqual(body["last_pr"], 89)
        self.assertEqual(body["last_commit_subject"], "Merge pull request #89 from dennis2810/branch")

    def test_defaults_to_unbekannt_outside_a_docker_build(self):
        # Kein GIT_COMMIT/LAST_COMMIT_SUBJECT/BUILD_TIME-File vorhanden
        # (z.B. lokaler Testlauf ausserhalb eines Docker-Builds).
        with patch.object(main, "GIT_COMMIT", "unbekannt"), \
             patch.object(main, "BUILD_TIME", "unbekannt"), \
             patch.object(main, "LAST_COMMIT_SUBJECT", "unbekannt"), \
             patch.object(main, "LAST_PR", None):
            response = client.get("/api/version")
        self.assertEqual(response.json(), {
            "git_commit": "unbekannt", "built_at": "unbekannt",
            "last_commit_subject": "unbekannt", "last_pr": None,
        })


if __name__ == "__main__":
    unittest.main()
