"""Tests for GET /api/search (webapp-poc/main.py) - globale Suche."""
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


class GlobalSearchEndpointTests(unittest.TestCase):
    def test_empty_query_returns_empty_results_without_hitting_db(self):
        with patch("main.db.list_cards") as list_cards, \
             patch("main.db.list_cards_by_sku") as list_cards_by_sku, \
             patch("main.db.list_purchases") as list_purchases, \
             patch("main.db.search_sales") as search_sales:
            response = client.get("/api/search", params={"q": "   "})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cards": [], "purchases": [], "sales": []})
        list_cards.assert_not_called()
        list_cards_by_sku.assert_not_called()
        list_purchases.assert_not_called()
        search_sales.assert_not_called()

    def test_missing_query_param_returns_empty_results(self):
        response = client.get("/api/search")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"cards": [], "purchases": [], "sales": []})

    def test_merges_card_matches_and_sku_matches_without_duplicates(self):
        cards_by_text = [{"id": "card-1", "title": "Karte 1"}]
        cards_by_sku = [{"id": "card-1", "title": "Karte 1"}, {"id": "card-2", "title": "Karte 2"}]
        with patch("main.db.list_cards", return_value=cards_by_text) as list_cards, \
             patch("main.db.list_cards_by_sku", return_value=cards_by_sku) as list_cards_by_sku, \
             patch("main.db.list_purchases", return_value=[]), \
             patch("main.db.search_sales", return_value=[]):
            response = client.get("/api/search", params={"q": "Karte"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual([c["id"] for c in body["cards"]], ["card-1", "card-2"])
        list_cards.assert_called_once_with(q="Karte")
        list_cards_by_sku.assert_called_once_with("Karte")

    def test_includes_purchases_and_sales(self):
        purchases = [{"id": "purchase-1", "seller": "Kartenfan"}]
        sales = [{"card_id": "card-1", "title": "Karte 1", "channel": "eBay", "buyer_username": "kartenfan99"}]
        with patch("main.db.list_cards", return_value=[]), \
             patch("main.db.list_cards_by_sku", return_value=[]), \
             patch("main.db.list_purchases", return_value=purchases) as list_purchases, \
             patch("main.db.search_sales", return_value=sales) as search_sales:
            response = client.get("/api/search", params={"q": "kartenfan"})

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["purchases"], purchases)
        self.assertEqual(body["sales"], sales)
        list_purchases.assert_called_once_with(q="kartenfan")
        search_sales.assert_called_once_with("kartenfan")

    def test_results_capped_at_twenty_per_category(self):
        many_cards = [{"id": f"card-{i}", "title": f"Karte {i}"} for i in range(30)]
        many_purchases = [{"id": f"purchase-{i}"} for i in range(30)]
        many_sales = [{"card_id": f"card-{i}", "title": f"Karte {i}"} for i in range(30)]
        with patch("main.db.list_cards", return_value=many_cards), \
             patch("main.db.list_cards_by_sku", return_value=[]), \
             patch("main.db.list_purchases", return_value=many_purchases), \
             patch("main.db.search_sales", return_value=many_sales):
            response = client.get("/api/search", params={"q": "Karte"})

        body = response.json()
        self.assertEqual(len(body["cards"]), 20)
        self.assertEqual(len(body["purchases"]), 20)
        self.assertEqual(len(body["sales"]), 20)


if __name__ == "__main__":
    unittest.main()
