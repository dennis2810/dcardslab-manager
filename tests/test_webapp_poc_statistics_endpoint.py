"""Tests for GET /api/statistics (webapp-poc/main.py)."""
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


class StatisticsEndpointTests(unittest.TestCase):
    def test_computes_profit_margin_and_holding_days_for_a_sold_card(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": "webapp-000001",
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        row = body["rows"][0]
        self.assertEqual(row["profit"], 5.0)
        self.assertEqual(row["margin_pct"], 50.0)
        self.assertEqual(row["holding_days"], 10)
        self.assertEqual(body["summary"]["realized_profit"], 5.0)
        self.assertEqual(body["summary"]["total_cost"], 10.0)
        self.assertEqual(body["summary"]["total_revenue"], 15.0)
        self.assertEqual(body["summary"]["sold_count"], 1)
        self.assertEqual(body["summary"]["avg_margin_pct"], 50.0)
        self.assertEqual(body["summary"]["avg_holding_days"], 10.0)
        self.assertEqual(body["summary"]["avg_sale_price"], 15.0)
        self.assertEqual(body["summary"]["roi_pct"], 50.0)
        self.assertEqual(body["summary"]["open_count"], 0)

    def test_shipping_charged_and_cost_net_into_profit_as_pass_through(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
            "shipping_charged": 4.0, "shipping_cost": 4.0,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()
        # Gleich hohe Einnahme/Ausgabe beim Versand -> hebt sich im Gewinn auf.
        self.assertEqual(body["rows"][0]["profit"], 5.0)
        self.assertEqual(body["summary"]["total_shipping_charged"], 4.0)
        self.assertEqual(body["summary"]["total_shipping_cost"], 4.0)

    def test_shipping_difference_affects_profit(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
            "shipping_charged": 5.0, "shipping_cost": 3.0,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        # 15 + 5 - 10 - 3 = 7 statt 5 ohne Versand-Differenz.
        self.assertEqual(response.json()["rows"][0]["profit"], 7.0)

    def test_card_with_only_a_purchase_has_no_profit_fields(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": None, "sale_price": None,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        row = response.json()["rows"][0]
        self.assertIsNone(row["profit"])
        self.assertIsNone(row["margin_pct"])
        self.assertIsNone(row["holding_days"])
        summary = response.json()["summary"]
        self.assertEqual(summary["total_cost"], 10.0)
        self.assertEqual(summary["total_revenue"], 0)
        self.assertEqual(summary["sold_count"], 0)
        self.assertEqual(summary["open_count"], 1)
        self.assertIsNone(summary["avg_margin_pct"])
        self.assertIsNone(summary["roi_pct"])

    def test_groups_sold_cards_by_month(self):
        rows = [
            {
                "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
                "purchase_date": None, "cost": None,
                "sale_date": "2026-01-15T00:00:00+00:00", "sale_price": 10.0,
            },
            {
                "card_id": "card-2", "title": "Karte 2", "card_no": 2, "sku": None,
                "purchase_date": None, "cost": None,
                "sale_date": "2026-01-20T00:00:00+00:00", "sale_price": 20.0,
            },
            {
                "card_id": "card-3", "title": "Karte 3", "card_no": 3, "sku": None,
                "purchase_date": None, "cost": None,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 5.0,
            },
        ]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        monthly = {m["month"]: m for m in response.json()["monthly"]}
        # Kein cost bekannt -> profit bleibt None fuer diese Zeilen, der
        # Monats-Gewinn-Eimer bleibt entsprechend bei 0.
        self.assertEqual(monthly["2026-01"], {"month": "2026-01", "count": 2, "revenue": 30.0, "profit": 0.0})
        self.assertEqual(monthly["2026-02"], {"month": "2026-02", "count": 1, "revenue": 5.0, "profit": 0.0})

    def test_monthly_profit_sums_only_rows_with_known_cost(self):
        rows = [
            {
                "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
                "purchase_date": "2026-01-01", "cost": 4.0,
                "sale_date": "2026-01-15T00:00:00+00:00", "sale_price": 10.0,
            },
            {
                "card_id": "card-2", "title": "Karte 2", "card_no": 2, "sku": None,
                "purchase_date": None, "cost": None,
                "sale_date": "2026-01-20T00:00:00+00:00", "sale_price": 20.0,
            },
        ]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        monthly = {m["month"]: m for m in response.json()["monthly"]}
        # Karte 1: Gewinn 6.0 (10 - 4); Karte 2 hat keinen cost -> traegt
        # nicht zum Monats-Gewinn bei, aber weiterhin zum Umsatz.
        self.assertEqual(monthly["2026-01"]["revenue"], 30.0)
        self.assertEqual(monthly["2026-01"]["profit"], 6.0)

    def test_empty_rows_produce_empty_summary(self):
        with patch("main.db.statistics_rows", return_value=[]):
            response = client.get("/api/statistics")
        body = response.json()
        self.assertEqual(body["rows"], [])
        self.assertEqual(body["monthly"], [])
        self.assertEqual(body["summary"]["sold_count"], 0)
        self.assertEqual(body["summary"]["open_count"], 0)
        self.assertIsNone(body["summary"]["avg_margin_pct"])
        self.assertIsNone(body["summary"]["avg_holding_days"])
        self.assertIsNone(body["summary"]["avg_sale_price"])
        self.assertIsNone(body["summary"]["roi_pct"])


if __name__ == "__main__":
    unittest.main()
