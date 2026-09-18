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

    def test_ebay_fees_reduce_profit(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
            "ebay_fees": 1.5,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()
        # 15 - 10 - 1.5 = 3.5 statt 5 ohne Gebuehr.
        self.assertEqual(body["rows"][0]["profit"], 3.5)
        self.assertEqual(body["summary"]["realized_profit"], 3.5)
        self.assertEqual(body["summary"]["total_ebay_fees"], 1.5)

    def test_refunded_sale_excludes_revenue_but_keeps_costs_as_loss(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
            "shipping_charged": 3.0, "ebay_fees": 1.5, "refunded": True,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()
        # Verkaufspreis (15) und erhaltener Versand (3) gingen zurueck an den
        # Kaeufer -> 0 - 10 (Einstandspreis) - 1.5 (eBay-Gebuehr, nicht
        # erstattet) = -11.5 Verlust. shipping_cost ist hier 0 (nicht gesetzt).
        self.assertEqual(body["rows"][0]["profit"], -11.5)
        self.assertEqual(body["summary"]["realized_profit"], -11.5)
        self.assertEqual(body["summary"]["total_revenue"], 0)
        self.assertEqual(body["summary"]["total_shipping_charged"], 0)
        self.assertEqual(body["summary"]["refunded_count"], 1)
        # Eine ruckerstattete Karte zaehlt nicht als "verkauft" fuer den
        # Durchschnitts-Verkaufspreis (kein echter Erloes erzielt).
        self.assertIsNone(body["summary"]["avg_sale_price"])

    def test_refunded_sale_still_counts_towards_monthly_bucket_but_not_revenue(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
            "refunded": True,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()
        bucket = body["monthly"][0]
        self.assertEqual(bucket["revenue"], 0)
        self.assertEqual(bucket["profit"], -10.0)

    def test_non_refunded_sale_defaults_correctly(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()
        self.assertEqual(body["summary"]["refunded_count"], 0)
        self.assertEqual(body["summary"]["total_revenue"], 15.0)

    def test_missing_ebay_fees_defaults_to_zero(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()
        self.assertEqual(body["rows"][0]["profit"], 5.0)
        self.assertEqual(body["summary"]["total_ebay_fees"], 0)

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
        self.assertEqual(monthly["2026-01"], {"month": "2026-01", "count": 2, "revenue": 30.0, "profit": 0.0, "cost": 0.0})
        self.assertEqual(monthly["2026-02"], {"month": "2026-02", "count": 1, "revenue": 5.0, "profit": 0.0, "cost": 0.0})

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
        # nicht zum Monats-Gewinn/-Einstandspreis bei, aber weiterhin zum Umsatz.
        self.assertEqual(monthly["2026-01"]["revenue"], 30.0)
        self.assertEqual(monthly["2026-01"]["profit"], 6.0)
        self.assertEqual(monthly["2026-01"]["cost"], 4.0)

    def test_team_and_set_ranking_group_sold_cards_by_profit(self):
        rows = [
            {
                "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
                "team": "FC Bayern", "set_name": "Topps 2026",
                "purchase_date": "2026-01-01", "cost": 4.0,
                "sale_date": "2026-01-15T00:00:00+00:00", "sale_price": 10.0,
            },
            {
                "card_id": "card-2", "title": "Karte 2", "card_no": 2, "sku": None,
                "team": "FC Bayern", "set_name": "Topps 2026",
                "purchase_date": "2026-01-01", "cost": 2.0,
                "sale_date": "2026-01-20T00:00:00+00:00", "sale_price": 5.0,
            },
            {
                "card_id": "card-3", "title": "Karte 3", "card_no": 3, "sku": None,
                "team": "Real Madrid", "set_name": "Panini 2026",
                "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 20.0,
            },
            {
                # Kein Team/Set -> darf im Ranking nicht auftauchen.
                "card_id": "card-4", "title": "Karte 4", "card_no": 4, "sku": None,
                "team": "", "set_name": "",
                "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 3.0,
            },
            {
                # Noch nicht verkauft -> darf im Ranking nicht auftauchen.
                "card_id": "card-5", "title": "Karte 5", "card_no": 5, "sku": None,
                "team": "FC Bayern", "set_name": "Topps 2026",
                "purchase_date": "2026-01-01", "cost": 5.0,
                "sale_date": None, "sale_price": None,
            },
        ]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()

        team_ranking = body["team_ranking"]
        self.assertEqual(len(team_ranking), 2)
        self.assertEqual(team_ranking[0]["name"], "Real Madrid")
        self.assertEqual(team_ranking[0]["profit"], 19.0)
        self.assertEqual(team_ranking[0]["count"], 1)
        self.assertEqual(team_ranking[1]["name"], "FC Bayern")
        self.assertEqual(team_ranking[1]["profit"], 9.0)
        self.assertEqual(team_ranking[1]["count"], 2)
        # Verkaufsgeschwindigkeit: Real Madrid 1.1.-1.2. = 31 Tage; FC Bayern
        # (1.1.-15.1. = 14 Tage, 1.1.-20.1. = 19 Tage) im Schnitt 16.5 Tage.
        self.assertEqual(team_ranking[0]["avg_holding_days"], 31.0)
        self.assertEqual(team_ranking[1]["avg_holding_days"], 16.5)

        set_ranking = body["set_ranking"]
        self.assertEqual(len(set_ranking), 2)
        self.assertEqual(set_ranking[0]["name"], "Panini 2026")
        self.assertEqual(set_ranking[1]["name"], "Topps 2026")

    def test_category_and_theme_ranking_group_sold_cards_by_profit(self):
        rows = [
            {
                "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
                "category": "Fußball", "theme": "Bundesliga",
                "purchase_date": "2026-01-01", "cost": 4.0,
                "sale_date": "2026-01-15T00:00:00+00:00", "sale_price": 10.0,
            },
            {
                "card_id": "card-2", "title": "Karte 2", "card_no": 2, "sku": None,
                "category": "Basketball", "theme": "NBA",
                "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 20.0,
            },
            {
                # Keine Kategorie/Thema -> darf im Ranking nicht auftauchen.
                "card_id": "card-3", "title": "Karte 3", "card_no": 3, "sku": None,
                "category": "", "theme": "",
                "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 3.0,
            },
        ]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()

        category_ranking = body["category_ranking"]
        self.assertEqual(len(category_ranking), 2)
        self.assertEqual(category_ranking[0]["name"], "Basketball")
        self.assertEqual(category_ranking[0]["profit"], 19.0)
        self.assertEqual(category_ranking[1]["name"], "Fußball")
        self.assertEqual(category_ranking[1]["profit"], 6.0)

        theme_ranking = body["theme_ranking"]
        self.assertEqual(len(theme_ranking), 2)
        self.assertEqual(theme_ranking[0]["name"], "NBA")
        self.assertEqual(theme_ranking[1]["name"], "Bundesliga")

    def test_price_bucket_ranking_groups_by_sale_price_range_in_fixed_order(self):
        rows = [
            {
                "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
                "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": "2026-01-15T00:00:00+00:00", "sale_price": 120.0,
            },
            {
                "card_id": "card-2", "title": "Karte 2", "card_no": 2, "sku": None,
                "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": "2026-01-20T00:00:00+00:00", "sale_price": 5.0,
            },
            {
                "card_id": "card-3", "title": "Karte 3", "card_no": 3, "sku": None,
                "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 8.0,
            },
        ]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()

        buckets = body["price_bucket_ranking"]
        # Feste Preisklassen-Reihenfolge (nicht nach Gewinn sortiert) - "< 10 €"
        # kommt vor "> 100 €", obwohl letztere Klasse hier mehr Gewinn brachte.
        self.assertEqual([b["name"] for b in buckets], ["< 10 €", "> 100 €"])
        self.assertEqual(buckets[0]["count"], 2)
        self.assertEqual(buckets[1]["count"], 1)

    def test_forecast_extrapolates_a_rising_linear_trend(self):
        rows = [
            {
                "card_id": f"card-{i}", "title": f"Karte {i}", "card_no": i, "sku": None,
                "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": f"2026-{month:02d}-01T00:00:00+00:00", "sale_price": price,
            }
            for i, (month, price) in enumerate([(1, 10.0), (2, 20.0), (3, 30.0)], start=1)
        ]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        body = response.json()

        forecast = body["forecast"]
        self.assertEqual([f["month"] for f in forecast], ["2026-04", "2026-05", "2026-06"])
        # Umsatz steigt exakt linear um 10 je Monat (10, 20, 30) -> Fortsetzung 40, 50, 60.
        self.assertEqual([f["projected_revenue"] for f in forecast], [40.0, 50.0, 60.0])

    def test_forecast_is_empty_with_fewer_than_two_months_of_data(self):
        rows = [{
            "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
            "purchase_date": "2026-01-01", "cost": 1.0,
            "sale_date": "2026-01-15T00:00:00+00:00", "sale_price": 10.0,
        }]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        self.assertEqual(response.json()["forecast"], [])

    def test_platform_ranking_averages_profit_and_margin(self):
        rows = [
            {
                "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
                "platform": "eBay", "purchase_date": "2026-01-01", "cost": 5.0,
                "sale_date": "2026-01-15T00:00:00+00:00", "sale_price": 10.0,
            },
            {
                "card_id": "card-2", "title": "Karte 2", "card_no": 2, "sku": None,
                "platform": "eBay", "purchase_date": "2026-01-01", "cost": 10.0,
                "sale_date": "2026-01-20T00:00:00+00:00", "sale_price": 15.0,
            },
            {
                "card_id": "card-3", "title": "Karte 3", "card_no": 3, "sku": None,
                "platform": "Kleinanzeigen", "purchase_date": "2026-01-01", "cost": 2.0,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 20.0,
            },
            {
                # Keine Plattform -> darf im Ranking nicht auftauchen.
                "card_id": "card-4", "title": "Karte 4", "card_no": 4, "sku": None,
                "platform": "", "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 3.0,
            },
            {
                # Noch nicht verkauft -> darf im Ranking nicht auftauchen.
                "card_id": "card-5", "title": "Karte 5", "card_no": 5, "sku": None,
                "platform": "eBay", "purchase_date": "2026-01-01", "cost": 5.0,
                "sale_date": None, "sale_price": None,
            },
        ]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        ranking = response.json()["platform_ranking"]

        self.assertEqual(len(ranking), 2)
        self.assertEqual(ranking[0]["name"], "Kleinanzeigen")
        self.assertEqual(ranking[0]["count"], 1)
        self.assertEqual(ranking[0]["avg_profit"], 18.0)
        self.assertEqual(ranking[0]["avg_margin_pct"], 900.0)
        self.assertEqual(ranking[1]["name"], "eBay")
        self.assertEqual(ranking[1]["count"], 2)
        self.assertEqual(ranking[1]["avg_profit"], 5.0)
        self.assertEqual(ranking[1]["avg_margin_pct"], 75.0)

    def test_channel_ranking_averages_profit_and_margin(self):
        rows = [
            {
                "card_id": "card-1", "title": "Karte 1", "card_no": 1, "sku": None,
                "channel": "eBay", "purchase_date": "2026-01-01", "cost": 5.0,
                "sale_date": "2026-01-15T00:00:00+00:00", "sale_price": 10.0,
            },
            {
                "card_id": "card-2", "title": "Karte 2", "card_no": 2, "sku": None,
                "channel": "eBay", "purchase_date": "2026-01-01", "cost": 10.0,
                "sale_date": "2026-01-20T00:00:00+00:00", "sale_price": 15.0,
            },
            {
                "card_id": "card-3", "title": "Karte 3", "card_no": 3, "sku": None,
                "channel": "Kleinanzeigen", "purchase_date": "2026-01-01", "cost": 2.0,
                "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 20.0,
            },
            {
                # Noch nicht verkauft -> kein Kanal, darf im Ranking nicht auftauchen.
                "card_id": "card-4", "title": "Karte 4", "card_no": 4, "sku": None,
                "channel": None, "purchase_date": "2026-01-01", "cost": 1.0,
                "sale_date": None, "sale_price": None,
            },
        ]
        with patch("main.db.statistics_rows", return_value=rows):
            response = client.get("/api/statistics")
        ranking = response.json()["channel_ranking"]

        self.assertEqual(len(ranking), 2)
        self.assertEqual(ranking[0]["name"], "Kleinanzeigen")
        self.assertEqual(ranking[0]["count"], 1)
        self.assertEqual(ranking[0]["avg_profit"], 18.0)
        self.assertEqual(ranking[0]["avg_margin_pct"], 900.0)
        self.assertEqual(ranking[1]["name"], "eBay")
        self.assertEqual(ranking[1]["count"], 2)
        self.assertEqual(ranking[1]["avg_profit"], 5.0)
        self.assertEqual(ranking[1]["avg_margin_pct"], 75.0)

    def test_empty_rows_produce_empty_summary(self):
        with patch("main.db.statistics_rows", return_value=[]):
            response = client.get("/api/statistics")
        body = response.json()
        self.assertEqual(body["rows"], [])
        self.assertEqual(body["monthly"], [])
        self.assertEqual(body["team_ranking"], [])
        self.assertEqual(body["set_ranking"], [])
        self.assertEqual(body["platform_ranking"], [])
        self.assertEqual(body["channel_ranking"], [])
        self.assertEqual(body["summary"]["sold_count"], 0)
        self.assertEqual(body["summary"]["open_count"], 0)
        self.assertEqual(body["summary"]["refunded_count"], 0)
        self.assertIsNone(body["summary"]["avg_margin_pct"])
        self.assertIsNone(body["summary"]["avg_holding_days"])
        self.assertIsNone(body["summary"]["avg_sale_price"])
        self.assertIsNone(body["summary"]["roi_pct"])


class EuerEndpointTests(unittest.TestCase):
    # EUER-Vorbereitung: Cash-Basis (jede Position im Jahr ihres eigenen
    # Datums), analog zum bestehenden DATEV-Export auf statistics-sales.html
    # - siehe main.py's _compute_euer().
    def test_purchase_booked_in_purchase_year_sale_in_sale_year(self):
        rows = [{
            "card_id": "card-1", "purchase_date": "2025-12-20", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
        }]
        with patch("main.db.statistics_rows", return_value=rows), \
                patch("main.db.list_business_expenses", return_value=[]), \
                patch("main.db.is_euer_year_locked", return_value=False):
            response_2025 = client.get("/api/euer", params={"year": 2025})
            response_2026 = client.get("/api/euer", params={"year": 2026})
        body_2025 = response_2025.json()
        body_2026 = response_2026.json()
        self.assertEqual(next(i["amount"] for i in body_2025["expenses"] if i["label"] == "Wareneinkauf"), 10.0)
        self.assertEqual(body_2025["income_total"], 0.0)
        self.assertEqual(next(i["amount"] for i in body_2026["income"] if i["label"] == "Verkaufserlöse"), 15.0)
        self.assertEqual(next(i["amount"] for i in body_2026["expenses"] if i["label"] == "Wareneinkauf"), 0.0)

    def test_refund_excludes_revenue_but_keeps_shipping_and_fees_as_expense(self):
        rows = [{
            "card_id": "card-1", "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-02-01T00:00:00+00:00", "sale_price": 15.0,
            "shipping_charged": 4.0, "shipping_cost": 3.0, "ebay_fees": 1.0, "refunded": True,
        }]
        with patch("main.db.statistics_rows", return_value=rows), \
                patch("main.db.list_business_expenses", return_value=[]), \
                patch("main.db.is_euer_year_locked", return_value=False):
            response = client.get("/api/euer", params={"year": 2026})
        body = response.json()
        self.assertEqual(next(i["amount"] for i in body["income"] if i["label"] == "Verkaufserlöse"), 0.0)
        self.assertEqual(next(i["amount"] for i in body["income"] if i["label"] == "Erhaltene Versandkosten"), 0.0)
        self.assertEqual(next(i["amount"] for i in body["expenses"] if i["label"] == "Versandkosten"), 3.0)
        self.assertEqual(next(i["amount"] for i in body["expenses"] if i["label"] == "Verkaufsgebühren"), 1.0)

    def test_other_business_expenses_counted_by_expense_date_year(self):
        expenses = [
            {"id": "e1", "expense_date": "2026-03-01", "category": "Porto", "amount": 12.5, "note": ""},
            {"id": "e2", "expense_date": "2025-12-01", "category": "Porto", "amount": 99.0, "note": ""},
        ]
        with patch("main.db.statistics_rows", return_value=[]), \
                patch("main.db.list_business_expenses", return_value=expenses), \
                patch("main.db.is_euer_year_locked", return_value=False):
            response = client.get("/api/euer", params={"year": 2026})
        body = response.json()
        self.assertEqual(
            next(i["amount"] for i in body["expenses"] if i["label"] == "Sonstige Betriebsausgaben"), 12.5
        )

    def test_profit_is_income_total_minus_expense_total(self):
        rows = [{
            "card_id": "card-1", "purchase_date": "2026-01-01", "cost": 10.0,
            "sale_date": "2026-01-11T00:00:00+00:00", "sale_price": 15.0,
        }]
        with patch("main.db.statistics_rows", return_value=rows), \
                patch("main.db.list_business_expenses", return_value=[]), \
                patch("main.db.is_euer_year_locked", return_value=False):
            response = client.get("/api/euer", params={"year": 2026})
        body = response.json()
        self.assertEqual(body["income_total"], 15.0)
        self.assertEqual(body["expense_total"], 10.0)
        self.assertEqual(body["profit"], 5.0)

    def test_defaults_to_current_year_when_year_omitted(self):
        with patch("main.db.statistics_rows", return_value=[]), \
                patch("main.db.list_business_expenses", return_value=[]), \
                patch("main.db.is_euer_year_locked", return_value=False):
            response = client.get("/api/euer")
        self.assertEqual(response.status_code, 200)
        self.assertIn("year", response.json())

    def test_locked_flag_reflects_is_euer_year_locked(self):
        with patch("main.db.statistics_rows", return_value=[]), \
                patch("main.db.list_business_expenses", return_value=[]), \
                patch("main.db.is_euer_year_locked", return_value=True) as mock_locked:
            response = client.get("/api/euer", params={"year": 2026})
        self.assertTrue(response.json()["locked"])
        mock_locked.assert_called_once_with(2026)


class EuerLockedYearsEndpointTests(unittest.TestCase):
    def test_lists_locked_years(self):
        with patch("main.db.list_locked_euer_years", return_value=[2024, 2025]):
            response = client.get("/api/euer/locked-years")
        self.assertEqual(response.json()["years"], [2024, 2025])

    def test_locks_a_year(self):
        with patch("main.db.lock_euer_year", return_value={"year": 2025}) as mock_lock:
            response = client.post("/api/euer/locked-years/2025")
        self.assertEqual(response.status_code, 200)
        mock_lock.assert_called_once_with(2025)

    def test_unlocks_a_year(self):
        with patch("main.db.unlock_euer_year") as mock_unlock:
            response = client.delete("/api/euer/locked-years/2025")
        self.assertEqual(response.status_code, 204)
        mock_unlock.assert_called_once_with(2025)


class BusinessExpenseLockGuardTests(unittest.TestCase):
    def test_create_rejected_when_year_locked(self):
        with patch("main.db.is_euer_year_locked", return_value=True):
            response = client.post("/api/business-expenses", json={"expense_date": "2025-06-01", "amount": 5})
        self.assertEqual(response.status_code, 409)

    def test_create_allowed_when_year_not_locked(self):
        with patch("main.db.is_euer_year_locked", return_value=False), \
                patch("main.db.create_business_expense", return_value={"id": "e1"}):
            response = client.post("/api/business-expenses", json={"expense_date": "2026-06-01", "amount": 5})
        self.assertEqual(response.status_code, 200)

    def test_update_rejected_when_existing_expense_year_locked(self):
        with patch("main.db.get_business_expense", return_value={"id": "e1", "expense_date": "2025-06-01"}), \
                patch("main.db.is_euer_year_locked", return_value=True):
            response = client.patch("/api/business-expenses/e1", json={"amount": 9})
        self.assertEqual(response.status_code, 409)

    def test_update_rejected_when_moving_into_locked_year(self):
        with patch("main.db.get_business_expense", return_value={"id": "e1", "expense_date": "2026-06-01"}), \
                patch("main.db.is_euer_year_locked", return_value=True) as mock_locked:
            response = client.patch("/api/business-expenses/e1", json={"expense_date": "2025-01-01"})
        self.assertEqual(response.status_code, 409)
        mock_locked.assert_called_once_with(2025)

    def test_delete_rejected_when_existing_expense_year_locked(self):
        with patch("main.db.get_business_expense", return_value={"id": "e1", "expense_date": "2025-06-01"}), \
                patch("main.db.is_euer_year_locked", return_value=True):
            response = client.delete("/api/business-expenses/e1")
        self.assertEqual(response.status_code, 409)

    def test_delete_allowed_when_year_not_locked(self):
        with patch("main.db.get_business_expense", return_value={"id": "e1", "expense_date": "2026-06-01"}), \
                patch("main.db.is_euer_year_locked", return_value=False), \
                patch("main.db.delete_business_expense", return_value={"id": "e1"}):
            response = client.delete("/api/business-expenses/e1")
        self.assertEqual(response.status_code, 204)


if __name__ == "__main__":
    unittest.main()
