"""Tests for GET/PUT /api/dashboard-goal (webapp-poc/main.py)."""
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


class GetDashboardGoalEndpointTests(unittest.TestCase):
    def test_returns_stored_goal_for_current_year(self):
        goal = {"year": 2026, "metric": "profit", "amount": 5000.0}
        with patch("main.db.get_dashboard_goal", return_value=goal) as mock_get:
            response = client.get("/api/dashboard-goal")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), goal)
        mock_get.assert_called_once()

    def test_uses_explicit_year_param(self):
        with patch("main.db.get_dashboard_goal", return_value=None) as mock_get:
            client.get("/api/dashboard-goal?year=2025")
        mock_get.assert_called_once_with(2025)

    def test_returns_default_zero_amount_when_none_set(self):
        with patch("main.db.get_dashboard_goal", return_value=None):
            response = client.get("/api/dashboard-goal?year=2026")
        body = response.json()
        self.assertEqual(body["year"], 2026)
        self.assertEqual(body["metric"], "revenue")
        self.assertEqual(body["amount"], 0)


class SetDashboardGoalEndpointTests(unittest.TestCase):
    def test_saves_metric_and_amount_for_current_year(self):
        saved = {"year": 2026, "metric": "revenue", "amount": 10000.0}
        with patch("main.db.set_dashboard_goal", return_value=saved) as mock_set:
            response = client.put("/api/dashboard-goal", json={"metric": "revenue", "amount": 10000})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), saved)
        args, _ = mock_set.call_args
        self.assertEqual(args[1], {"metric": "revenue", "amount": 10000})

    def test_rejects_unknown_metric(self):
        response = client.put("/api/dashboard-goal", json={"metric": "banana", "amount": 100})
        self.assertEqual(response.status_code, 400)

    def test_defaults_amount_to_zero_when_missing(self):
        with patch("main.db.set_dashboard_goal", return_value={"year": 2026, "metric": "profit", "amount": 0}) as mock_set:
            client.put("/api/dashboard-goal", json={"metric": "profit"})
        args, _ = mock_set.call_args
        self.assertEqual(args[1]["amount"], 0)


if __name__ == "__main__":
    unittest.main()
