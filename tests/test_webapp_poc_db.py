"""Tests for webapp-poc/db.py - scan_batches/cards persistence via the
Supabase Postgres client."""
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))
import db  # noqa: E402


def _mock_table(client, table_name, execute_return_data):
    """Wire client.table(table_name)....execute() to return an object
    whose .data is execute_return_data, mirroring postgrest-py's APIResponse."""
    response = MagicMock()
    response.data = execute_return_data
    builder = client.table.return_value
    builder.insert.return_value.execute.return_value = response
    builder.update.return_value.eq.return_value.execute.return_value = response
    builder.select.return_value.order.return_value.execute.return_value = response
    builder.select.return_value.eq.return_value.execute.return_value = response
    return response


class CreateBatchTests(unittest.TestCase):
    def test_inserts_batch_and_returns_id(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "scan_batches", [{"id": "batch-1", "card_count": 9, "status": "pending"}])
        with patch("db.get_client", return_value=mock_client):
            batch_id = db.create_batch(card_count=9)
        self.assertEqual(batch_id, "batch-1")
        mock_client.table.assert_any_call("scan_batches")
        insert_call = mock_client.table.return_value.insert
        insert_call.assert_called_once_with({"card_count": 9, "status": "pending"})


class UpdateBatchStatusTests(unittest.TestCase):
    def test_updates_status_by_id(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "scan_batches", [{"id": "batch-1", "status": "ok"}])
        with patch("db.get_client", return_value=mock_client):
            db.update_batch_status("batch-1", "ok")
        update_call = mock_client.table.return_value.update
        update_call.assert_called_once_with({"status": "ok"})
        update_call.return_value.eq.assert_called_once_with("id", "batch-1")


class InsertCardTests(unittest.TestCase):
    def test_inserts_card_with_all_fields(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "batch_id": "batch-1", "position_in_batch": 3, "title": "Max Mustermann"}
        _mock_table(mock_client, "cards", [saved_row])
        fields = dict.fromkeys(db.CARD_FIELDS, "")
        fields["title"] = "Max Mustermann"
        fields["is_numbered"] = 1
        fields["confidence"] = 90
        fields["status"] = "ok"

        with patch("db.get_client", return_value=mock_client):
            result = db.insert_card("batch-1", 3, fields, "batch-1/3_front.jpg", "batch-1/3_back.jpg")

        self.assertEqual(result, saved_row)
        insert_call = mock_client.table.return_value.insert
        row = insert_call.call_args[0][0]
        self.assertEqual(row["batch_id"], "batch-1")
        self.assertEqual(row["position_in_batch"], 3)
        self.assertEqual(row["title"], "Max Mustermann")
        self.assertIs(row["is_numbered"], True)
        self.assertEqual(row["recognition_status"], "ok")
        self.assertEqual(row["front_image_path"], "batch-1/3_front.jpg")
        self.assertEqual(row["back_image_path"], "batch-1/3_back.jpg")

    def test_is_numbered_false_when_zero(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "cards", [{"id": "card-1"}])
        fields = dict.fromkeys(db.CARD_FIELDS, "")
        fields["is_numbered"] = 0
        fields["confidence"] = 0
        fields["status"] = "nicht erkannt"

        with patch("db.get_client", return_value=mock_client):
            db.insert_card("batch-1", 1, fields, None, None)

        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertIs(row["is_numbered"], False)
        self.assertIsNone(row["front_image_path"])
        self.assertIsNone(row["back_image_path"])


class ListCardsTests(unittest.TestCase):
    def test_returns_all_cards_newest_first(self):
        mock_client = MagicMock()
        rows = [{"id": "card-2"}, {"id": "card-1"}]
        _mock_table(mock_client, "cards", rows)
        with patch("db.get_client", return_value=mock_client):
            result = db.list_cards()
        self.assertEqual(result, rows)
        mock_client.table.return_value.select.return_value.order.assert_called_once_with(
            "created_at", desc=True
        )


class GetCardTests(unittest.TestCase):
    def test_returns_card_when_found(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "cards", [{"id": "card-1", "title": "Karte"}])
        with patch("db.get_client", return_value=mock_client):
            result = db.get_card("card-1")
        self.assertEqual(result, {"id": "card-1", "title": "Karte"})

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "cards", [])
        with patch("db.get_client", return_value=mock_client):
            result = db.get_card("does-not-exist")
        self.assertIsNone(result)


class UpdateCardTests(unittest.TestCase):
    def test_updates_only_provided_fields(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "title": "Korrigierter Name"}
        response = MagicMock()
        response.data = [saved_row]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_card("card-1", {"title": "Korrigierter Name"})
        self.assertEqual(result, saved_row)
        mock_client.table.return_value.update.assert_called_once_with({"title": "Korrigierter Name"})
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with("id", "card-1")

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "team": "FC Bayern"}
        response = MagicMock()
        response.data = [saved_row]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_card("card-1", {"team": "FC Bayern", "not_a_real_column": "x"})
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row, {"team": "FC Bayern"})

    def test_never_writes_structural_columns(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "title": "x"}
        response = MagicMock()
        response.data = [saved_row]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_card("card-1", {
                "title": "x", "id": "other-id", "batch_id": "some-batch",
                "created_at": "2020-01-01T00:00:00", "front_image_path": "evil.jpg",
            })
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row, {"title": "x"})

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_card("does-not-exist", {"title": "x"})
        self.assertIsNone(result)

    def test_empty_valid_fields_returns_current_card(self):
        mock_client = MagicMock()
        existing = {"id": "card-1", "title": "Unveraendert"}
        response = MagicMock()
        response.data = [existing]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_card("card-1", {"not_a_real_column": "x"})
        self.assertEqual(result, existing)
        mock_client.table.return_value.update.assert_not_called()


class DeleteCardTests(unittest.TestCase):
    def test_deletes_card_and_returns_it(self):
        mock_client = MagicMock()
        existing = {"id": "card-1", "front_image_path": "b1/1_front.jpg", "back_image_path": None}
        select_response = MagicMock()
        select_response.data = [existing]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = select_response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_card("card-1")
        self.assertEqual(result, existing)
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "card-1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        select_response = MagicMock()
        select_response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = select_response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_card("does-not-exist")
        self.assertIsNone(result)
        mock_client.table.return_value.delete.assert_not_called()


class ListCardsFilterTests(unittest.TestCase):
    def test_no_filters_behaves_like_before(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "card-1"}]
        mock_client.table.return_value.select.return_value.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.list_cards()
        self.assertEqual(result, [{"id": "card-1"}])
        mock_client.table.return_value.select.return_value.or_.assert_not_called()

    def test_q_filters_across_four_columns(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_cards(q="Bayern")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertIn("title.ilike.%Bayern%", filter_arg)
        self.assertIn("team.ilike.%Bayern%", filter_arg)
        self.assertIn("set_name.ilike.%Bayern%", filter_arg)
        self.assertIn("card_number.ilike.%Bayern%", filter_arg)

    def test_q_strips_commas_and_parens_before_building_filter(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_cards(q="a,b(c)")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertEqual(filter_arg.count(","), 3)  # exactly the 3 clause separators between the 4 ilike terms
        self.assertNotIn("(", filter_arg)
        self.assertNotIn(")", filter_arg)

    def test_status_filters_by_recognition_status(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        eq_builder = mock_client.table.return_value.select.return_value.eq.return_value
        eq_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_cards(status="prüfen")
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with(
            "recognition_status", "prüfen"
        )


def _mock_client_for_tables(**table_builders):
    """client.table(name) liefert table_builders[name] statt eines einzigen
    geteilten Mocks - noetig fuer Funktionen, die mehr als eine Tabelle
    anfassen (z.B. create_purchase() -> purchases UND purchase_items)."""
    client = MagicMock()
    client.table.side_effect = lambda name: table_builders[name]
    return client


class CreatePurchaseTests(unittest.TestCase):
    def test_creates_purchase_without_items(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "purchases", [{"id": "purchase-1", "purchase_date": "2026-08-27"}])
        with patch("db.get_client", return_value=mock_client):
            result = db.create_purchase({"purchase_date": "2026-08-27"})
        self.assertEqual(result["id"], "purchase-1")
        self.assertEqual(result["items"], [])
        mock_client.table.return_value.insert.assert_called_once_with(
            {"purchase_date": "2026-08-27"}
        )

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "purchases", [{"id": "purchase-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_purchase({"purchase_date": "2026-08-27", "not_a_real_column": "x"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertNotIn("not_a_real_column", row)

    def test_creates_purchase_with_single_item(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "purchase-1"}]
        purchases_builder.insert.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        dup_check = MagicMock()
        dup_check.data = []
        items_builder.select.return_value.eq.return_value.execute.return_value = dup_check
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1", "purchase_id": "purchase-1", "card_id": "card-1"}]
        items_builder.insert.return_value.execute.return_value = insert_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.create_purchase({"purchase_date": "2026-08-27"}, items=[{"card_id": "card-1"}])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["card_id"], "card-1")

    def test_rolls_back_purchase_and_items_when_an_item_is_already_linked(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "purchase-1"}]
        purchases_builder.insert.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        not_linked = MagicMock()
        not_linked.data = []
        already_linked = MagicMock()
        already_linked.data = [{"id": "existing-item"}]
        items_builder.select.return_value.eq.return_value.execute.side_effect = [not_linked, already_linked]
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1", "purchase_id": "purchase-1", "card_id": "card-1"}]
        items_builder.insert.return_value.execute.return_value = insert_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            with self.assertRaises(db.CardAlreadyLinkedError):
                db.create_purchase(
                    {"purchase_date": "2026-08-27"},
                    items=[{"card_id": "card-1"}, {"card_id": "card-2"}],
                )
        items_builder.delete.return_value.eq.assert_called_once_with("id", "item-1")
        purchases_builder.delete.return_value.eq.assert_called_once_with("id", "purchase-1")


class ListPurchasesTests(unittest.TestCase):
    def test_computes_item_count_per_purchase(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "p1"}, {"id": "p2"}]
        purchases_builder.select.return_value.order.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{"purchase_id": "p1"}, {"purchase_id": "p1"}, {"purchase_id": "p2"}]
        items_builder.select.return_value.in_.return_value.execute.return_value = items_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.list_purchases()
        counts = {p["id"]: p["item_count"] for p in result}
        self.assertEqual(counts, {"p1": 2, "p2": 1})

    def test_q_filters_platform_seller_notes(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_purchases(q="eBay")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertIn("platform.ilike.%eBay%", filter_arg)
        self.assertIn("seller.ilike.%eBay%", filter_arg)
        self.assertIn("notes.ilike.%eBay%", filter_arg)


class GetPurchaseTests(unittest.TestCase):
    def test_returns_purchase_with_items(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "p1", "platform": "eBay"}]
        purchases_builder.select.return_value.eq.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{"id": "item-1", "card_id": "card-1"}]
        items_builder.select.return_value.eq.return_value.execute.return_value = items_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.get_purchase("p1")
        self.assertEqual(result["platform"], "eBay")
        self.assertEqual(result["items"], [{"id": "item-1", "card_id": "card-1"}])

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_purchase("does-not-exist")
        self.assertIsNone(result)


class UpdatePurchaseTests(unittest.TestCase):
    def test_updates_only_provided_fields_and_reattaches_items(self):
        purchases_builder = MagicMock()
        update_response = MagicMock()
        update_response.data = [{"id": "p1", "platform": "Kleinanzeigen"}]
        purchases_builder.update.return_value.eq.return_value.execute.return_value = update_response

        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{"id": "item-1"}]
        items_builder.select.return_value.eq.return_value.execute.return_value = items_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase("p1", {"platform": "Kleinanzeigen"})
        self.assertEqual(result["platform"], "Kleinanzeigen")
        self.assertEqual(result["items"], [{"id": "item-1"}])

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase("does-not-exist", {"platform": "x"})
        self.assertIsNone(result)


class DeletePurchaseTests(unittest.TestCase):
    def test_deletes_and_returns_purchase(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "p1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_purchase("p1")
        self.assertEqual(result, {"id": "p1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "p1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_purchase("does-not-exist")
        self.assertIsNone(result)
        mock_client.table.return_value.delete.assert_not_called()


class AddPurchaseItemTests(unittest.TestCase):
    def test_links_card_to_purchase(self):
        mock_client = MagicMock()
        dup_check = MagicMock()
        dup_check.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = dup_check
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1", "purchase_id": "p1", "card_id": "card-1"}]
        mock_client.table.return_value.insert.return_value.execute.return_value = insert_response
        with patch("db.get_client", return_value=mock_client):
            result = db.add_purchase_item("p1", {"card_id": "card-1", "allocated_cost": 12.5})
        self.assertEqual(result["card_id"], "card-1")
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["allocated_cost"], 12.5)
        self.assertEqual(row["quantity"], 1)  # default

    def test_raises_when_card_already_linked(self):
        mock_client = MagicMock()
        dup_check = MagicMock()
        dup_check.data = [{"id": "existing-item"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = dup_check
        with patch("db.get_client", return_value=mock_client):
            with self.assertRaises(db.CardAlreadyLinkedError):
                db.add_purchase_item("p1", {"card_id": "card-1"})
        mock_client.table.return_value.insert.assert_not_called()


class UpdatePurchaseItemTests(unittest.TestCase):
    def test_updates_only_provided_fields(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "item-1", "notes": "LP statt NM"}]
        mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase_item("p1", "item-1", {"notes": "LP statt NM"})
        self.assertEqual(result["notes"], "LP statt NM")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase_item("p1", "does-not-exist", {"notes": "x"})
        self.assertIsNone(result)


class DeletePurchaseItemTests(unittest.TestCase):
    def test_deletes_and_returns_item(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "item-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_purchase_item("p1", "item-1")
        self.assertEqual(result, {"id": "item-1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "item-1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_purchase_item("p1", "does-not-exist")
        self.assertIsNone(result)
        mock_client.table.return_value.delete.assert_not_called()


class GetPurchaseForCardTests(unittest.TestCase):
    def test_returns_flat_purchase_info_when_linked(self):
        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{
            "id": "item-1", "purchase_id": "p1",
            "allocated_cost": 12.5, "quantity": 1, "notes": "",
        }]
        items_builder.select.return_value.eq.return_value.execute.return_value = items_response

        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{
            "id": "p1", "purchase_date": "2026-08-27", "platform": "eBay", "seller": "cardguy88",
        }]
        purchases_builder.select.return_value.eq.return_value.execute.return_value = purchases_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.get_purchase_for_card("card-1")
        self.assertEqual(result["purchase_id"], "p1")
        self.assertEqual(result["item_id"], "item-1")
        self.assertEqual(result["platform"], "eBay")
        self.assertEqual(result["allocated_cost"], 12.5)

    def test_returns_none_when_not_linked(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_purchase_for_card("card-1")
        self.assertIsNone(result)


class CardsWithPurchaseTests(unittest.TestCase):
    def test_returns_set_of_linked_card_ids(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"card_id": "card-1"}, {"card_id": "card-2"}]
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.cards_with_purchase(["card-1", "card-2", "card-3"])
        self.assertEqual(result, {"card-1", "card-2"})

    def test_empty_input_skips_query(self):
        mock_client = MagicMock()
        with patch("db.get_client", return_value=mock_client):
            result = db.cards_with_purchase([])
        self.assertEqual(result, set())
        mock_client.table.assert_not_called()


if __name__ == "__main__":
    unittest.main()
