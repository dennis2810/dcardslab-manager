"""Tests for webapp-poc/portfolio.py - the weekly Portfolio-Wertverlauf
background snapshot job."""
import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import portfolio  # noqa: E402


class IsSnapshotDueTests(unittest.TestCase):
    def test_due_when_no_snapshot_exists(self):
        with patch("portfolio.db.latest_portfolio_snapshot", return_value=None):
            self.assertTrue(portfolio._is_snapshot_due())

    def test_not_due_when_recent_snapshot_exists(self):
        recent = (date.today() - timedelta(days=2)).isoformat()
        with patch("portfolio.db.latest_portfolio_snapshot", return_value={"snapshot_date": recent}):
            self.assertFalse(portfolio._is_snapshot_due())

    def test_due_when_snapshot_older_than_interval(self):
        stale = (date.today() - timedelta(days=10)).isoformat()
        with patch("portfolio.db.latest_portfolio_snapshot", return_value={"snapshot_date": stale}):
            self.assertTrue(portfolio._is_snapshot_due())

    def test_due_when_snapshot_date_unparseable(self):
        with patch("portfolio.db.latest_portfolio_snapshot", return_value={"snapshot_date": "not-a-date"}):
            self.assertTrue(portfolio._is_snapshot_due())


class RecordSnapshotNowTests(unittest.TestCase):
    def test_computes_value_and_records_snapshot(self):
        compute_fn = MagicMock(return_value=(1234.5, 42))
        with patch("portfolio.db.record_portfolio_snapshot", return_value={"id": "s1"}) as mock_record:
            result = portfolio.record_snapshot_now(compute_fn)
        compute_fn.assert_called_once()
        args = mock_record.call_args[0]
        self.assertEqual(args[0], date.today().isoformat())
        self.assertEqual(args[1], 1234.5)
        self.assertEqual(args[2], 42)
        self.assertEqual(result, {"id": "s1"})


class RunScheduledSnapshotOnceTests(unittest.TestCase):
    def test_does_nothing_when_not_due(self):
        compute_fn = MagicMock()
        with patch("portfolio._is_snapshot_due", return_value=False), \
             patch("portfolio.db.record_portfolio_snapshot") as mock_record:
            portfolio.run_scheduled_snapshot_once(compute_fn)
        compute_fn.assert_not_called()
        mock_record.assert_not_called()

    def test_records_when_due(self):
        compute_fn = MagicMock(return_value=(100.0, 5))
        with patch("portfolio._is_snapshot_due", return_value=True), \
             patch("portfolio.db.record_portfolio_snapshot") as mock_record:
            portfolio.run_scheduled_snapshot_once(compute_fn)
        mock_record.assert_called_once()

    def test_survives_failure(self):
        compute_fn = MagicMock(side_effect=RuntimeError("db down"))
        with patch("portfolio._is_snapshot_due", return_value=True):
            portfolio.run_scheduled_snapshot_once(compute_fn)  # must not raise


if __name__ == "__main__":
    unittest.main()
