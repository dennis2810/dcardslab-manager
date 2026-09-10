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

    def test_shipped_flag_is_writable(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "shipped": True}
        response = MagicMock()
        response.data = [saved_row]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_card("card-1", {"shipped": True})
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row, {"shipped": True})

    def test_tags_field_is_writable(self):
        mock_client = MagicMock()
        saved_row = {"id": "card-1", "tags": "Rookie, Investment"}
        response = MagicMock()
        response.data = [saved_row]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_card("card-1", {"tags": "Rookie, Investment"})
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row, {"tags": "Rookie, Investment"})

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


class SetCardImagePathTests(unittest.TestCase):
    def test_writes_front_image_path(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "card-1", "front_image_path": "b1/1_front.jpg"}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.set_card_image_path("card-1", "front", "b1/1_front.jpg")
        self.assertEqual(result["front_image_path"], "b1/1_front.jpg")
        mock_client.table.return_value.update.assert_called_once_with({"front_image_path": "b1/1_front.jpg"})

    def test_writes_back_image_path(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "card-1", "back_image_path": "b1/1_back.jpg"}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.set_card_image_path("card-1", "back", "b1/1_back.jpg")
        mock_client.table.return_value.update.assert_called_once_with({"back_image_path": "b1/1_back.jpg"})

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.set_card_image_path("does-not-exist", "front", "x.jpg")
        self.assertIsNone(result)


class AddCardExtraImageTests(unittest.TestCase):
    def test_appends_to_empty_list(self):
        mock_client = MagicMock()
        get_response = MagicMock()
        get_response.data = [{"id": "card-1", "extra_image_paths": ""}]
        update_response = MagicMock()
        update_response.data = [{"id": "card-1", "extra_image_paths": "b1/1_extra_abc.jpg"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = get_response
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = update_response
        with patch("db.get_client", return_value=mock_client):
            result = db.add_card_extra_image("card-1", "b1/1_extra_abc.jpg")
        self.assertEqual(result["extra_image_paths"], "b1/1_extra_abc.jpg")
        mock_client.table.return_value.update.assert_called_once_with(
            {"extra_image_paths": "b1/1_extra_abc.jpg"}
        )

    def test_appends_to_existing_list(self):
        mock_client = MagicMock()
        get_response = MagicMock()
        get_response.data = [{"id": "card-1", "extra_image_paths": "b1/1_extra_aaa.jpg"}]
        update_response = MagicMock()
        update_response.data = [{"id": "card-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = get_response
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = update_response
        with patch("db.get_client", return_value=mock_client):
            db.add_card_extra_image("card-1", "b1/1_extra_bbb.jpg")
        mock_client.table.return_value.update.assert_called_once_with(
            {"extra_image_paths": "b1/1_extra_aaa.jpg,b1/1_extra_bbb.jpg"}
        )

    def test_returns_none_when_card_not_found(self):
        mock_client = MagicMock()
        get_response = MagicMock()
        get_response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = get_response
        with patch("db.get_client", return_value=mock_client):
            result = db.add_card_extra_image("does-not-exist", "x.jpg")
        self.assertIsNone(result)
        mock_client.table.return_value.update.assert_not_called()


class RemoveCardExtraImageTests(unittest.TestCase):
    def test_removes_by_index_and_returns_removed_path(self):
        mock_client = MagicMock()
        get_response = MagicMock()
        get_response.data = [{"id": "card-1", "extra_image_paths": "b1/1_extra_aaa.jpg,b1/1_extra_bbb.jpg"}]
        update_response = MagicMock()
        update_response.data = [{"id": "card-1", "extra_image_paths": "b1/1_extra_bbb.jpg"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = get_response
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = update_response
        with patch("db.get_client", return_value=mock_client):
            updated, removed_path = db.remove_card_extra_image("card-1", 0)
        self.assertEqual(removed_path, "b1/1_extra_aaa.jpg")
        self.assertEqual(updated["extra_image_paths"], "b1/1_extra_bbb.jpg")
        mock_client.table.return_value.update.assert_called_once_with(
            {"extra_image_paths": "b1/1_extra_bbb.jpg"}
        )

    def test_returns_none_none_when_index_out_of_range(self):
        mock_client = MagicMock()
        get_response = MagicMock()
        get_response.data = [{"id": "card-1", "extra_image_paths": "b1/1_extra_aaa.jpg"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = get_response
        with patch("db.get_client", return_value=mock_client):
            updated, removed_path = db.remove_card_extra_image("card-1", 5)
        self.assertIsNone(updated)
        self.assertIsNone(removed_path)
        mock_client.table.return_value.update.assert_not_called()

    def test_returns_none_none_when_card_not_found(self):
        mock_client = MagicMock()
        get_response = MagicMock()
        get_response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = get_response
        with patch("db.get_client", return_value=mock_client):
            updated, removed_path = db.remove_card_extra_image("does-not-exist", 0)
        self.assertIsNone(updated)
        self.assertIsNone(removed_path)


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

    def test_q_filters_across_six_columns(self):
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
        self.assertIn("season_year.ilike.%Bayern%", filter_arg)
        self.assertIn("tags.ilike.%Bayern%", filter_arg)

    def test_q_strips_commas_and_parens_before_building_filter(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_cards(q="a,b(c)")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertEqual(filter_arg.count(","), 5)  # exactly the 5 clause separators between the 6 ilike terms
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


class ListCardsBySkuTests(unittest.TestCase):
    def test_returns_cards_linked_to_a_matching_sku(self):
        mock_client = _mock_client_for_tables(
            ebay_listings=MagicMock(),
            cards=MagicMock(),
        )
        mock_client.table("ebay_listings").select.return_value.ilike.return_value.execute.return_value.data = [
            {"card_id": "card-1"},
        ]
        mock_client.table("cards").select.return_value.in_.return_value.execute.return_value.data = [
            {"id": "card-1", "title": "Karte 1"},
        ]
        with patch("db.get_client", return_value=mock_client):
            result = db.list_cards_by_sku("webapp-000001")
        self.assertEqual(result, [{"id": "card-1", "title": "Karte 1"}])
        mock_client.table("ebay_listings").select.return_value.ilike.assert_called_once_with(
            "sku", "%webapp-000001%"
        )

    def test_returns_empty_list_when_no_listing_matches(self):
        mock_client = _mock_client_for_tables(ebay_listings=MagicMock())
        mock_client.table("ebay_listings").select.return_value.ilike.return_value.execute.return_value.data = []
        with patch("db.get_client", return_value=mock_client):
            result = db.list_cards_by_sku("nichts-da")
        self.assertEqual(result, [])


class FindDuplicateCardTests(unittest.TestCase):
    def test_returns_none_when_any_field_is_blank(self):
        with patch("db.get_client") as mock_get_client:
            result = db.find_duplicate_card("", "Set", "1")
        self.assertIsNone(result)
        mock_get_client.assert_not_called()

    def test_returns_matching_card_when_title_set_and_number_all_match(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "card-1", "title": "Karte 1", "set_name": "Set A", "card_number": "5", "card_no": 3}]
        chain = mock_client.table.return_value.select.return_value.ilike.return_value.ilike.return_value.ilike.return_value
        chain.limit.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.find_duplicate_card("Karte 1", "Set A", "5")
        self.assertEqual(result["id"], "card-1")

    def test_returns_none_when_no_match(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        chain = mock_client.table.return_value.select.return_value.ilike.return_value.ilike.return_value.ilike.return_value
        chain.limit.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.find_duplicate_card("Karte 1", "Set A", "5")
        self.assertIsNone(result)


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

    def test_rounds_total_price_and_shipping_to_two_decimals(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "purchases", [{"id": "purchase-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_purchase({"purchase_date": "2026-08-27", "total_price": "9.999", "shipping": "1.005"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["total_price"], 10.0)
        self.assertEqual(row["shipping"], 1.0)

    def test_blank_numeric_fields_become_none(self):
        # <input type=number> that's left empty sends "" - Postgres rejects
        # "" on a numeric column, so it must become NULL instead.
        mock_client = MagicMock()
        _mock_table(mock_client, "purchases", [{"id": "purchase-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_purchase({"purchase_date": "2026-08-27", "shipping": "", "total_price": ""})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertIsNone(row["shipping"])
        self.assertIsNone(row["total_price"])

    def test_rolls_back_purchase_and_items_on_any_item_insertion_error(self):
        # Nicht nur CardAlreadyLinkedError - jede Ausnahme waehrend der
        # Item-Verknuepfung (z.B. ein nicht existierender card_id, der die
        # FK-Constraint verletzt) muss den Kauf wieder abraeumen.
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "purchase-1"}]
        purchases_builder.insert.return_value.execute.return_value = purchases_response

        items_builder = MagicMock()
        dup_check = MagicMock()
        dup_check.data = []
        items_builder.select.return_value.eq.return_value.execute.return_value = dup_check
        items_builder.insert.return_value.execute.side_effect = RuntimeError("FK violation")

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            with self.assertRaises(RuntimeError):
                db.create_purchase({"purchase_date": "2026-08-27"}, items=[{"card_id": "does-not-exist"}])
        purchases_builder.delete.return_value.eq.assert_called_once_with("id", "purchase-1")

    def test_creates_purchase_with_single_item(self):
        purchases_builder = MagicMock()
        purchases_response = MagicMock()
        purchases_response.data = [{"id": "purchase-1"}]
        purchases_builder.insert.return_value.execute.return_value = purchases_response
        price_response = MagicMock()
        price_response.data = [{"total_price": 10.0, "shipping": 0}]
        purchases_builder.select.return_value.eq.return_value.execute.return_value = price_response

        items_builder = MagicMock()
        dup_check = MagicMock()
        dup_check.data = []
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1", "purchase_id": "purchase-1", "card_id": "card-1"}]
        items_builder.insert.return_value.execute.return_value = insert_response
        # add_purchase_item()'s dup-check, then _recompute_allocated_costs()'s
        # id lookup, then _list_purchase_items()'s final refresh - all three
        # go through the same select().eq().execute() chain, so they're
        # distinguished by call order via side_effect.
        recompute_ids = MagicMock()
        recompute_ids.data = [{"id": "item-1"}]
        final_items = MagicMock()
        final_items.data = [{"id": "item-1", "purchase_id": "purchase-1", "card_id": "card-1", "allocated_cost": 10.0}]
        items_builder.select.return_value.eq.return_value.execute.side_effect = [dup_check, recompute_ids, final_items]

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.create_purchase({"purchase_date": "2026-08-27", "total_price": 10.0}, items=[{"card_id": "card-1"}])
        self.assertEqual(len(result["items"]), 1)
        self.assertEqual(result["items"][0]["card_id"], "card-1")
        self.assertEqual(result["items"][0]["allocated_cost"], 10.0)
        items_builder.update.assert_called_once_with({"allocated_cost": 10.0})

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
        # platform ist nicht total_price/shipping -> keine Neuberechnung der
        # Kostenaufteilung noetig, purchases_builder.select() (der Preis-
        # Lookup in _recompute_allocated_costs()) darf daher nicht aufgerufen
        # werden.
        purchases_builder.select.assert_not_called()

    def test_recomputes_allocated_costs_when_total_price_changes(self):
        purchases_builder = MagicMock()
        update_response = MagicMock()
        update_response.data = [{"id": "p1", "total_price": 20.0}]
        purchases_builder.update.return_value.eq.return_value.execute.return_value = update_response
        price_response = MagicMock()
        price_response.data = [{"total_price": 20.0, "shipping": 0}]
        purchases_builder.select.return_value.eq.return_value.execute.return_value = price_response

        items_builder = MagicMock()
        ids_response = MagicMock()
        ids_response.data = [{"id": "item-1"}, {"id": "item-2"}]
        final_items = MagicMock()
        final_items.data = [{"id": "item-1", "allocated_cost": 10.0}, {"id": "item-2", "allocated_cost": 10.0}]
        items_builder.select.return_value.eq.return_value.execute.side_effect = [ids_response, final_items]

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase("p1", {"total_price": 20.0})
        self.assertEqual(result["items"], final_items.data)
        items_builder.update.assert_called_with({"allocated_cost": 10.0})

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_purchase("does-not-exist", {"platform": "x"})
        self.assertIsNone(result)

    def test_blank_numeric_fields_become_none(self):
        purchases_builder = MagicMock()
        update_response = MagicMock()
        update_response.data = [{"id": "p1"}]
        purchases_builder.update.return_value.eq.return_value.execute.return_value = update_response
        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = []
        items_builder.select.return_value.eq.return_value.execute.return_value = items_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            db.update_purchase("p1", {"shipping": ""})
        row = purchases_builder.update.call_args[0][0]
        self.assertIsNone(row["shipping"])


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


class SetPurchaseReceiptTests(unittest.TestCase):
    def test_writes_receipt_path_and_reattaches_items(self):
        purchases_builder = MagicMock()
        update_response = MagicMock()
        update_response.data = [{"id": "p1", "receipt_path": "p1/receipt.pdf"}]
        purchases_builder.update.return_value.eq.return_value.execute.return_value = update_response

        items_builder = MagicMock()
        items_response = MagicMock()
        items_response.data = [{"id": "item-1"}]
        items_builder.select.return_value.eq.return_value.execute.return_value = items_response

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.set_purchase_receipt("p1", "p1/receipt.pdf")
        self.assertEqual(result["receipt_path"], "p1/receipt.pdf")
        self.assertEqual(result["items"], [{"id": "item-1"}])
        purchases_builder.update.assert_called_once_with({"receipt_path": "p1/receipt.pdf"})

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.set_purchase_receipt("does-not-exist", "x/receipt.pdf")
        self.assertIsNone(result)


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

    def test_blank_allocated_cost_becomes_none(self):
        mock_client = MagicMock()
        dup_check = MagicMock()
        dup_check.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = dup_check
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1"}]
        mock_client.table.return_value.insert.return_value.execute.return_value = insert_response
        with patch("db.get_client", return_value=mock_client):
            db.add_purchase_item("p1", {"card_id": "card-1", "allocated_cost": ""})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertIsNone(row["allocated_cost"])

    def test_rounds_allocated_cost_to_two_decimals(self):
        mock_client = MagicMock()
        dup_check = MagicMock()
        dup_check.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = dup_check
        insert_response = MagicMock()
        insert_response.data = [{"id": "item-1"}]
        mock_client.table.return_value.insert.return_value.execute.return_value = insert_response
        with patch("db.get_client", return_value=mock_client):
            db.add_purchase_item("p1", {"card_id": "card-1", "allocated_cost": "0.333"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["allocated_cost"], 0.33)


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

    def test_rounds_manually_entered_allocated_cost(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "item-1", "allocated_cost": 4.2}]
        mock_client.table.return_value.update.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_purchase_item("p1", "item-1", {"allocated_cost": "4.196"})
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row["allocated_cost"], 4.2)


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


class RecomputeAllocatedCostsTests(unittest.TestCase):
    def test_splits_total_and_shipping_evenly_across_items(self):
        purchases_builder = MagicMock()
        price_response = MagicMock()
        price_response.data = [{"total_price": 30.0, "shipping": 6.0}]
        purchases_builder.select.return_value.eq.return_value.execute.return_value = price_response

        items_builder = MagicMock()
        ids_response = MagicMock()
        ids_response.data = [{"id": "item-1"}, {"id": "item-2"}, {"id": "item-3"}]
        final_items = MagicMock()
        final_items.data = [
            {"id": "item-1", "allocated_cost": 12.0},
            {"id": "item-2", "allocated_cost": 12.0},
            {"id": "item-3", "allocated_cost": 12.0},
        ]
        items_builder.select.return_value.eq.return_value.execute.side_effect = [ids_response, final_items]

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            result = db.recompute_purchase_item_costs("p1")
        self.assertEqual(result, final_items.data)
        self.assertEqual(items_builder.update.call_count, 3)
        items_builder.update.assert_called_with({"allocated_cost": 12.0})

    def test_noop_when_purchase_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.recompute_purchase_item_costs("does-not-exist")
        mock_client.table.return_value.update.assert_not_called()

    def test_noop_when_no_items_linked(self):
        purchases_builder = MagicMock()
        price_response = MagicMock()
        price_response.data = [{"total_price": 10.0, "shipping": 0}]
        purchases_builder.select.return_value.eq.return_value.execute.return_value = price_response

        items_builder = MagicMock()
        empty = MagicMock()
        empty.data = []
        items_builder.select.return_value.eq.return_value.execute.return_value = empty

        mock_client = _mock_client_for_tables(purchases=purchases_builder, purchase_items=items_builder)
        with patch("db.get_client", return_value=mock_client):
            db.recompute_purchase_item_costs("p1")
        items_builder.update.assert_not_called()


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


class EbayStatusByCardIdTests(unittest.TestCase):
    def test_returns_status_and_sku_keyed_by_card_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [
            {"card_id": "card-1", "status": "Veroeffentlicht", "sku": "webapp-000001", "price": 9.99},
            {"card_id": "card-2", "status": "Entwurf", "sku": "webapp-000002", "price": 0},
        ]
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.ebay_info_by_card_id(["card-1", "card-2", "card-3"])
        self.assertEqual(result, {
            "card-1": {"status": "Veroeffentlicht", "sku": "webapp-000001", "price": 9.99},
            "card-2": {"status": "Entwurf", "sku": "webapp-000002", "price": 0},
        })

    def test_empty_input_skips_query(self):
        mock_client = MagicMock()
        with patch("db.get_client", return_value=mock_client):
            result = db.ebay_info_by_card_id([])
        self.assertEqual(result, {})
        mock_client.table.assert_not_called()


class PriceResearchByCardIdTests(unittest.TestCase):
    def test_groups_average_and_count_by_card_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [
            {"card_id": "card-1", "price": 10.0},
            {"card_id": "card-1", "price": 20.0},
            {"card_id": "card-2", "price": 5.0},
        ]
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.price_research_by_card_ids(["card-1", "card-2", "card-3"])
        self.assertEqual(result, {
            "card-1": {"avg_price": 15.0, "count": 2},
            "card-2": {"avg_price": 5.0, "count": 1},
        })

    def test_empty_input_skips_query(self):
        mock_client = MagicMock()
        with patch("db.get_client", return_value=mock_client):
            result = db.price_research_by_card_ids([])
        self.assertEqual(result, {})
        mock_client.table.assert_not_called()


class ManualSaleInfoByCardIdTests(unittest.TestCase):
    def test_returns_channel_keyed_by_card_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [
            {"card_id": "card-1", "channel": "Kleinanzeigen"},
            {"card_id": "card-2", "channel": ""},
        ]
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.manual_sale_info_by_card_id(["card-1", "card-2", "card-3"])
        self.assertEqual(result, {
            "card-1": {"channel": "Kleinanzeigen"},
            "card-2": {"channel": ""},
        })

    def test_empty_input_skips_query(self):
        mock_client = MagicMock()
        with patch("db.get_client", return_value=mock_client):
            result = db.manual_sale_info_by_card_id([])
        self.assertEqual(result, {})
        mock_client.table.assert_not_called()


class PurchaseCostByCardIdTests(unittest.TestCase):
    def test_returns_allocated_cost_keyed_by_card_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"card_id": "card-1", "allocated_cost": 12.5}]
        mock_client.table.return_value.select.return_value.in_.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.purchase_cost_by_card_id(["card-1", "card-2"])
        self.assertEqual(result, {"card-1": 12.5})

    def test_empty_input_skips_query(self):
        mock_client = MagicMock()
        with patch("db.get_client", return_value=mock_client):
            result = db.purchase_cost_by_card_id([])
        self.assertEqual(result, {})
        mock_client.table.assert_not_called()


class CreateEbayListingTests(unittest.TestCase):
    def test_inserts_row_with_card_id_and_sku(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "ebay_listings", [{"id": "listing-1", "card_id": "card-1", "sku": "webapp-card-1"}])
        with patch("db.get_client", return_value=mock_client):
            result = db.create_ebay_listing("card-1", "webapp-card-1", {"title": "Musterkarte", "price": 9.99})
        self.assertEqual(result["id"], "listing-1")
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["card_id"], "card-1")
        self.assertEqual(row["sku"], "webapp-card-1")
        self.assertEqual(row["title"], "Musterkarte")

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "ebay_listings", [{"id": "listing-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_ebay_listing("card-1", "sku-1", {"title": "x", "not_a_real_column": "y"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertNotIn("not_a_real_column", row)

    def test_rounds_price_to_two_decimals(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "ebay_listings", [{"id": "listing-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_ebay_listing("card-1", "sku-1", {"title": "x", "price": "9.999"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["price"], 10.0)


class GetEbayListingTests(unittest.TestCase):
    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "ebay_listings", [])
        with patch("db.get_client", return_value=mock_client):
            self.assertIsNone(db.get_ebay_listing("does-not-exist"))


class GetEbayListingForCardTests(unittest.TestCase):
    def test_returns_single_object_when_linked(self):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = [
            {"id": "listing-1", "card_id": "card-1"}
        ]
        with patch("db.get_client", return_value=mock_client):
            result = db.get_ebay_listing_for_card("card-1")
        self.assertEqual(result["id"], "listing-1")

    def test_returns_none_when_not_linked(self):
        mock_client = MagicMock()
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value.data = []
        with patch("db.get_client", return_value=mock_client):
            self.assertIsNone(db.get_ebay_listing_for_card("card-1"))


class ListEbayListingsTests(unittest.TestCase):
    def test_status_filter(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        eq_builder = mock_client.table.return_value.select.return_value.eq.return_value
        eq_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_ebay_listings(status="Entwurf")
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with("status", "Entwurf")

    def test_q_filters_title(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_ebay_listings(q="Musterkarte")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertIn("title.ilike.%Musterkarte%", filter_arg)

    def test_q_filters_sku_too(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        or_builder = mock_client.table.return_value.select.return_value.or_.return_value
        or_builder.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_ebay_listings(q="webapp-000001")
        filter_arg = mock_client.table.return_value.select.return_value.or_.call_args[0][0]
        self.assertIn("sku.ilike.%webapp-000001%", filter_arg)


class UpdateEbayListingTests(unittest.TestCase):
    def test_updates_only_allowed_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "ebay_listings", [{"id": "listing-1", "price": 15.0}])
        with patch("db.get_client", return_value=mock_client):
            db.update_ebay_listing("listing-1", {"price": 15.0, "not_a_real_column": "x"})
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row["price"], 15.0)
        self.assertNotIn("not_a_real_column", row)

    def test_status_and_scheduling_fields_are_writable(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "ebay_listings", [{"id": "listing-1", "status": "Geplant"}])
        with patch("db.get_client", return_value=mock_client):
            db.update_ebay_listing("listing-1", {
                "status": "Geplant", "scheduled_at": "2026-09-01T10:00:00Z", "scheduling_mode": "app",
            })
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row["status"], "Geplant")
        self.assertEqual(row["scheduling_mode"], "app")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "ebay_listings", [])
        with patch("db.get_client", return_value=mock_client):
            self.assertIsNone(db.update_ebay_listing("does-not-exist", {"price": 1}))

    def test_rounds_price_to_two_decimals(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "ebay_listings", [{"id": "listing-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.update_ebay_listing("listing-1", {"price": "12.996"})
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row["price"], 13.0)


class DeleteEbayListingTests(unittest.TestCase):
    def test_deletes_and_returns_row(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "listing-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_ebay_listing("listing-1")
        self.assertEqual(result, {"id": "listing-1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "listing-1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            self.assertIsNone(db.delete_ebay_listing("does-not-exist"))
        mock_client.table.return_value.delete.assert_not_called()


class ListDueScheduledListingsTests(unittest.TestCase):
    def test_filters_out_not_yet_due_rows(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [
            {"id": "l1", "scheduled_at": "2020-01-01T00:00:00+00:00"},  # long past, due
            {"id": "l2", "scheduled_at": "2999-01-01T00:00:00+00:00"},  # far future, not due
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.list_due_scheduled_listings("app")
        self.assertEqual([r["id"] for r in result], ["l1"])


class ListNativeScheduledListingsTests(unittest.TestCase):
    def test_returns_all_native_scheduled_rows(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "l1", "scheduling_mode": "native"}]
        mock_client.table.return_value.select.return_value.eq.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.list_native_scheduled_listings()
        self.assertEqual(result, [{"id": "l1", "scheduling_mode": "native"}])


class ListListingsDueForPriceResearchTests(unittest.TestCase):
    def test_never_checked_listing_comes_before_long_checked_one(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [
            {"id": "l1", "status": "Veroeffentlicht", "last_price_research_at": "2020-01-01T00:00:00+00:00"},
            {"id": "l2", "status": "Veroeffentlicht", "last_price_research_at": None},
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.list_listings_due_for_price_research()
        self.assertEqual([r["id"] for r in result], ["l2", "l1"])

    def test_filters_out_recently_checked_listing(self):
        from datetime import datetime, timezone

        mock_client = MagicMock()
        response = MagicMock()
        response.data = [
            {"id": "l1", "status": "Veroeffentlicht", "last_price_research_at": datetime.now(timezone.utc).isoformat()},
            {"id": "l2", "status": "Veroeffentlicht", "last_price_research_at": None},
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.list_listings_due_for_price_research()
        self.assertEqual([r["id"] for r in result], ["l2"])

    def test_respects_limit(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [
            {"id": f"l{i}", "status": "Veroeffentlicht", "last_price_research_at": None} for i in range(5)
        ]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.list_listings_due_for_price_research(limit=2)
        self.assertEqual(len(result), 2)

    def test_queries_only_published_listings(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_listings_due_for_price_research()
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with("status", "Veroeffentlicht")


class MarkPriceResearchCheckedTests(unittest.TestCase):
    def test_writes_timestamp_for_listing(self):
        mock_client = MagicMock()
        with patch("db.get_client", return_value=mock_client):
            db.mark_price_research_checked("listing-1", "2026-09-10T12:00:00+00:00")
        mock_client.table.return_value.update.assert_called_once_with(
            {"last_price_research_at": "2026-09-10T12:00:00+00:00"}
        )
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with("id", "listing-1")


class LatestSaleSyncCursorTests(unittest.TestCase):
    def test_returns_none_when_no_sales_yet(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            self.assertIsNone(db.latest_sale_sync_cursor())

    def test_returns_created_at_of_most_recent_sale(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"created_at": "2026-08-27T10:00:00+00:00"}]
        mock_client.table.return_value.select.return_value.order.return_value.limit.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            self.assertEqual(db.latest_sale_sync_cursor(), "2026-08-27T10:00:00+00:00")


class UpsertEbaySaleTests(unittest.TestCase):
    def test_upserts_on_order_and_line_item(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "sale-1"}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.upsert_ebay_sale({"ebay_order_id": "O1", "ebay_line_item_id": "LI1"})
        self.assertEqual(result, {"id": "sale-1"})
        mock_client.table.return_value.upsert.assert_called_once_with(
            {"ebay_order_id": "O1", "ebay_line_item_id": "LI1"},
            on_conflict="ebay_order_id,ebay_line_item_id",
        )


class UpdateEbaySaleTests(unittest.TestCase):
    def test_updates_shipping_cost_rounded(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "sale-1", "shipping_cost": 3.5}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_ebay_sale("sale-1", {"shipping_cost": "3.499"})
        self.assertEqual(result["shipping_cost"], 3.5)
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row["shipping_cost"], 3.5)

    def test_updates_ebay_fees_rounded(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "sale-1", "ebay_fees": 2.15}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_ebay_sale("sale-1", {"ebay_fees": "2.149"})
        self.assertEqual(result["ebay_fees"], 2.15)
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row["ebay_fees"], 2.15)

    def test_updates_refunded_flag(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "sale-1", "refunded": True}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_ebay_sale("sale-1", {"refunded": True})
        self.assertTrue(result["refunded"])
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row, {"refunded": True})

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "sale-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_ebay_sale("sale-1", {"gross_price": 999, "notes": "hack"})
        mock_client.table.return_value.update.assert_not_called()

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_ebay_sale("does-not-exist", {"shipping_cost": 1})
        self.assertIsNone(result)


class GetSaleForCardTests(unittest.TestCase):
    def test_returns_most_recent_sale(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"card_id": "card-1", "sale_date": "2026-08-01T00:00:00+00:00", "gross_price": 20.0}]
        chain = mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value
        chain.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_sale_for_card("card-1")
        self.assertEqual(result["gross_price"], 20.0)

    def test_returns_none_when_no_sale(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        chain = mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.limit.return_value
        chain.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            self.assertIsNone(db.get_sale_for_card("card-1"))


class SalesByListingIdTests(unittest.TestCase):
    def test_keeps_most_recent_sale_per_listing(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [
            {"listing_id": "listing-1", "sale_date": "2026-08-02T00:00:00+00:00", "gross_price": 25.0},
            {"listing_id": "listing-1", "sale_date": "2026-08-01T00:00:00+00:00", "gross_price": 20.0},
            {"listing_id": "listing-2", "sale_date": "2026-08-03T00:00:00+00:00", "gross_price": 5.0},
        ]
        mock_client.table.return_value.select.return_value.in_.return_value.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.sales_by_listing_id(["listing-1", "listing-2"])
        self.assertEqual(result["listing-1"]["gross_price"], 25.0)
        self.assertEqual(result["listing-2"]["gross_price"], 5.0)

    def test_empty_input_skips_query(self):
        mock_client = MagicMock()
        with patch("db.get_client", return_value=mock_client):
            result = db.sales_by_listing_id([])
        self.assertEqual(result, {})
        mock_client.table.assert_not_called()


class GoogleSheetsSettingsTests(unittest.TestCase):
    def test_get_returns_none_when_no_row(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_google_sheets_settings()
        self.assertIsNone(result)

    def test_get_returns_the_single_row(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": True, "spreadsheet_id": "sheet-1"}]
        mock_client.table.return_value.select.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_google_sheets_settings()
        self.assertEqual(result["spreadsheet_id"], "sheet-1")

    def test_save_upserts_with_singleton_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": True, "spreadsheet_id": "sheet-2"}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.save_google_sheets_settings({"spreadsheet_id": "sheet-2"})
        row = mock_client.table.return_value.upsert.call_args[0][0]
        self.assertEqual(row["id"], True)
        self.assertEqual(row["spreadsheet_id"], "sheet-2")

    def test_save_ignores_unknown_fields(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": True}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.save_google_sheets_settings({"spreadsheet_id": "s1", "not_a_real_column": "x"})
        row = mock_client.table.return_value.upsert.call_args[0][0]
        self.assertNotIn("not_a_real_column", row)


class AppStatusTests(unittest.TestCase):
    def test_get_returns_none_when_no_row(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_app_status()
        self.assertIsNone(result)

    def test_get_returns_the_single_row(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": True, "last_backup_at": "2026-09-09T10:00:00+00:00"}]
        mock_client.table.return_value.select.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_app_status()
        self.assertEqual(result["last_backup_at"], "2026-09-09T10:00:00+00:00")

    def test_record_backup_downloaded_upserts_with_singleton_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": True, "last_backup_at": "2026-09-09T10:00:00+00:00"}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.record_backup_downloaded("2026-09-09T10:00:00+00:00")
        row = mock_client.table.return_value.upsert.call_args[0][0]
        self.assertEqual(row, {"id": True, "last_backup_at": "2026-09-09T10:00:00+00:00"})

    def test_set_low_stock_threshold_upserts_with_singleton_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": True, "low_stock_threshold": 3}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.set_low_stock_threshold(3)
        row = mock_client.table.return_value.upsert.call_args[0][0]
        self.assertEqual(row, {"id": True, "low_stock_threshold": 3})

    def test_record_auto_backup_upserts_with_singleton_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": True, "last_auto_backup_at": "2026-09-10T03:00:00+00:00"}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.record_auto_backup("2026-09-10T03:00:00+00:00")
        row = mock_client.table.return_value.upsert.call_args[0][0]
        self.assertEqual(row, {"id": True, "last_auto_backup_at": "2026-09-10T03:00:00+00:00"})


class DashboardGoalTests(unittest.TestCase):
    def test_get_returns_none_when_no_row_for_year(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_dashboard_goal(2026)
        self.assertIsNone(result)
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with("year", 2026)

    def test_get_returns_the_row_for_year(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"year": 2026, "metric": "revenue", "amount": 5000.0}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_dashboard_goal(2026)
        self.assertEqual(result["amount"], 5000.0)

    def test_set_upserts_on_year(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"year": 2026, "metric": "profit", "amount": 3000.0}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.set_dashboard_goal(2026, {"metric": "profit", "amount": 3000.0})
        row = mock_client.table.return_value.upsert.call_args[0][0]
        self.assertEqual(row, {"metric": "profit", "amount": 3000.0, "year": 2026})
        self.assertEqual(mock_client.table.return_value.upsert.call_args[1], {"on_conflict": "year"})

    def test_set_ignores_unknown_fields(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"year": 2026}]
        mock_client.table.return_value.upsert.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.set_dashboard_goal(2026, {"metric": "revenue", "amount": 1, "not_a_real_column": "x"})
        row = mock_client.table.return_value.upsert.call_args[0][0]
        self.assertNotIn("not_a_real_column", row)


class BackupReadFunctionsTests(unittest.TestCase):
    def _assert_reads_table(self, fn, table_name):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "row-1"}]
        mock_client.table.return_value.select.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = fn()
        mock_client.table.assert_called_once_with(table_name)
        mock_client.table.return_value.select.assert_called_once_with("*")
        self.assertEqual(result, [{"id": "row-1"}])

    def test_all_scan_batches(self):
        self._assert_reads_table(db.all_scan_batches, "scan_batches")

    def test_all_cards(self):
        self._assert_reads_table(db.all_cards, "cards")

    def test_all_purchases(self):
        self._assert_reads_table(db.all_purchases, "purchases")

    def test_all_purchase_items(self):
        self._assert_reads_table(db.all_purchase_items, "purchase_items")

    def test_all_ebay_listings(self):
        self._assert_reads_table(db.all_ebay_listings, "ebay_listings")

    def test_all_ebay_sales(self):
        self._assert_reads_table(db.all_ebay_sales, "ebay_sales")

    def test_all_inventory(self):
        self._assert_reads_table(db.all_inventory, "inventory")


class CreateInventoryItemTests(unittest.TestCase):
    def test_inserts_row_with_card_id(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "inventory", [{"id": "inv-1", "card_id": "card-1", "quantity": 1}])
        with patch("db.get_client", return_value=mock_client):
            result = db.create_inventory_item("card-1", {"quantity": 1, "condition": "NM"})
        self.assertEqual(result["id"], "inv-1")
        insert_call = mock_client.table.return_value.insert
        row = insert_call.call_args[0][0]
        self.assertEqual(row["card_id"], "card-1")
        self.assertEqual(row["quantity"], 1)
        self.assertEqual(row["condition"], "NM")

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "inventory", [{"id": "inv-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_inventory_item("card-1", {"quantity": 1, "not_a_column": "x"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertNotIn("not_a_column", row)


class ListInventoryTests(unittest.TestCase):
    def test_returns_rows_ordered_by_created_at(self):
        mock_client = MagicMock()
        rows = [{"id": "inv-1"}, {"id": "inv-2"}]
        _mock_table(mock_client, "inventory", rows)
        with patch("db.get_client", return_value=mock_client):
            result = db.list_inventory()
        self.assertEqual(result, rows)
        mock_client.table.return_value.select.return_value.order.assert_called_once_with("created_at")


class GetInventoryForCardTests(unittest.TestCase):
    def test_returns_rows_for_the_card_ordered_by_created_at(self):
        mock_client = MagicMock()
        rows = [{"id": "inv-1", "card_id": "card-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = rows
        with patch("db.get_client", return_value=mock_client):
            result = db.get_inventory_for_card("card-1")
        self.assertEqual(result, rows)
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with("card_id", "card-1")


class GetInventoryItemTests(unittest.TestCase):
    def test_returns_item_when_found(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "inventory", [{"id": "inv-1"}])
        with patch("db.get_client", return_value=mock_client):
            result = db.get_inventory_item("inv-1")
        self.assertEqual(result, {"id": "inv-1"})

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "inventory", [])
        with patch("db.get_client", return_value=mock_client):
            result = db.get_inventory_item("does-not-exist")
        self.assertIsNone(result)


class UpdateInventoryItemTests(unittest.TestCase):
    def test_updates_only_provided_fields(self):
        mock_client = MagicMock()
        saved_row = {"id": "inv-1", "location": "Regal 2"}
        response = MagicMock()
        response.data = [saved_row]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_inventory_item("inv-1", {"location": "Regal 2"})
        self.assertEqual(result, saved_row)
        mock_client.table.return_value.update.assert_called_once_with({"location": "Regal 2"})

    def test_blank_quantity_becomes_none(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "inv-1"}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_inventory_item("inv-1", {"quantity": ""})
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertIsNone(row["quantity"])

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_inventory_item("does-not-exist", {"location": "x"})
        self.assertIsNone(result)


class DeleteInventoryItemTests(unittest.TestCase):
    def test_deletes_and_returns_item(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "inv-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_inventory_item("inv-1")
        self.assertEqual(result, {"id": "inv-1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "inv-1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_inventory_item("does-not-exist")
        self.assertIsNone(result)


class ZeroInventoryForCardTests(unittest.TestCase):
    def test_sets_quantity_to_zero_for_all_rows_of_the_card(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "inv-1", "quantity": 0}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.zero_inventory_for_card("card-1")
        self.assertEqual(result, [{"id": "inv-1", "quantity": 0}])
        mock_client.table.return_value.update.assert_called_once_with({"quantity": 0})
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with("card_id", "card-1")


class RestoreInventoryForCardTests(unittest.TestCase):
    def test_sets_quantity_to_one_for_all_rows_of_the_card(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "inv-1", "quantity": 1}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.restore_inventory_for_card("card-1")
        self.assertEqual(result, [{"id": "inv-1", "quantity": 1}])
        mock_client.table.return_value.update.assert_called_once_with({"quantity": 1})
        mock_client.table.return_value.update.return_value.eq.assert_called_once_with("card_id", "card-1")


class ListWishlistItemsTests(unittest.TestCase):
    def test_returns_all_items_ordered_by_created_at_desc(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "w1", "title": "Karte A"}]
        mock_client.table.return_value.select.return_value.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.list_wishlist_items()
        self.assertEqual(result, [{"id": "w1", "title": "Karte A"}])
        mock_client.table.return_value.select.return_value.order.assert_called_once_with(
            "created_at", desc=True
        )

    def test_applies_search_filter_when_q_given(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.or_.return_value.order.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.list_wishlist_items(q="Bayern")
        mock_client.table.return_value.select.return_value.or_.assert_called_once()


class CreateWishlistItemTests(unittest.TestCase):
    def test_inserts_row_with_rounded_target_price(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "wishlist_items", [{"id": "w1"}])
        with patch("db.get_client", return_value=mock_client):
            result = db.create_wishlist_item({
                "title": "Karte A", "team": "FC Beispiel", "set_name": "Set 2026",
                "target_price": "9.999", "notes": "beim naechsten Angebot kaufen",
            })
        self.assertEqual(result["id"], "w1")
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["title"], "Karte A")
        self.assertEqual(row["target_price"], 10.0)
        self.assertEqual(row["notes"], "beim naechsten Angebot kaufen")

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "wishlist_items", [{"id": "w1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_wishlist_item({"title": "Karte A", "not_a_column": "x"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertNotIn("not_a_column", row)

    def test_blank_target_price_becomes_none(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "wishlist_items", [{"id": "w1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_wishlist_item({"title": "Karte A", "target_price": ""})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertIsNone(row["target_price"])


class UpdateWishlistItemTests(unittest.TestCase):
    def test_updates_rounded_target_price(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "w1", "target_price": 8.5}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_wishlist_item("w1", {"target_price": "8.499"})
        self.assertEqual(result["target_price"], 8.5)

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "w1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_wishlist_item("w1", {"not_a_column": "x"})
        self.assertEqual(result, {"id": "w1"})
        mock_client.table.return_value.update.assert_not_called()

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_wishlist_item("does-not-exist", {"title": "x"})
        self.assertIsNone(result)


class DeleteWishlistItemTests(unittest.TestCase):
    def test_deletes_and_returns_entry(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "w1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_wishlist_item("w1")
        self.assertEqual(result, {"id": "w1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "w1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_wishlist_item("does-not-exist")
        self.assertIsNone(result)


class CreatePriceResearchEntryTests(unittest.TestCase):
    def test_inserts_row_with_card_id_and_rounded_price(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "price_research", [{"id": "pr-1", "card_id": "card-1", "price": 12.5}])
        with patch("db.get_client", return_value=mock_client):
            result = db.create_price_research_entry("card-1", {"price": 12.499, "note": "eBay sold", "checked_at": "2026-09-01"})
        self.assertEqual(result["id"], "pr-1")
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["card_id"], "card-1")
        self.assertEqual(row["price"], 12.5)
        self.assertEqual(row["note"], "eBay sold")
        self.assertEqual(row["checked_at"], "2026-09-01")

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "price_research", [{"id": "pr-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_price_research_entry("card-1", {"price": 5, "not_a_column": "x"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertNotIn("not_a_column", row)


class ListPriceResearchForCardTests(unittest.TestCase):
    def test_returns_rows_for_the_card_ordered_by_checked_at(self):
        mock_client = MagicMock()
        rows = [{"id": "pr-1", "card_id": "card-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.order.return_value.execute.return_value.data = rows
        with patch("db.get_client", return_value=mock_client):
            result = db.list_price_research_for_card("card-1")
        self.assertEqual(result, rows)
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with("card_id", "card-1")
        mock_client.table.return_value.select.return_value.eq.return_value.order.assert_called_once_with("checked_at")


class DeletePriceResearchEntryTests(unittest.TestCase):
    def test_deletes_and_returns_entry(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "pr-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_price_research_entry("pr-1")
        self.assertEqual(result, {"id": "pr-1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "pr-1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_price_research_entry("does-not-exist")
        self.assertIsNone(result)


class CreateManualSaleTests(unittest.TestCase):
    def test_inserts_row_with_card_id_and_rounded_money_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "manual_sales", [{"id": "ms-1", "card_id": "card-1"}])
        with patch("db.get_client", return_value=mock_client):
            result = db.create_manual_sale("card-1", {
                "channel": "Kleinanzeigen", "sale_date": "2026-09-10", "gross_price": "12.499",
                "shipping_charged": "3.005", "shipping_cost": "2.5", "fees": "0.999", "notes": "Barzahlung",
            })
        self.assertEqual(result["id"], "ms-1")
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertEqual(row["card_id"], "card-1")
        self.assertEqual(row["channel"], "Kleinanzeigen")
        self.assertEqual(row["gross_price"], 12.5)
        self.assertEqual(row["shipping_charged"], 3.0)
        self.assertEqual(row["shipping_cost"], 2.5)
        self.assertEqual(row["fees"], 1.0)
        self.assertEqual(row["notes"], "Barzahlung")

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "manual_sales", [{"id": "ms-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_manual_sale("card-1", {"gross_price": 5, "not_a_column": "x"})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertNotIn("not_a_column", row)

    def test_blank_money_fields_become_none(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "manual_sales", [{"id": "ms-1"}])
        with patch("db.get_client", return_value=mock_client):
            db.create_manual_sale("card-1", {"gross_price": "", "fees": ""})
        row = mock_client.table.return_value.insert.call_args[0][0]
        self.assertIsNone(row["gross_price"])
        self.assertIsNone(row["fees"])


class GetManualSaleForCardTests(unittest.TestCase):
    def test_returns_the_row_for_the_card(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "ms-1", "card_id": "card-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_manual_sale_for_card("card-1")
        self.assertEqual(result["id"], "ms-1")
        mock_client.table.return_value.select.return_value.eq.assert_called_once_with("card_id", "card-1")

    def test_returns_none_when_no_manual_sale(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.get_manual_sale_for_card("card-1")
        self.assertIsNone(result)


class UpdateManualSaleTests(unittest.TestCase):
    def test_updates_rounded_money_fields(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "ms-1", "gross_price": 9.5}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_manual_sale("ms-1", {"gross_price": "9.499"})
        self.assertEqual(result["gross_price"], 9.5)
        row = mock_client.table.return_value.update.call_args[0][0]
        self.assertEqual(row["gross_price"], 9.5)

    def test_updates_refunded_flag(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "ms-1", "refunded": True}]
        mock_client.table.return_value.update.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_manual_sale("ms-1", {"refunded": True})
        self.assertTrue(result["refunded"])

    def test_ignores_unknown_fields(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "ms-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            db.update_manual_sale("ms-1", {"not_a_column": "x"})
        mock_client.table.return_value.update.assert_not_called()

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.update_manual_sale("does-not-exist", {"not_a_column": "x"})
        self.assertIsNone(result)


class DeleteManualSaleTests(unittest.TestCase):
    def test_deletes_and_returns_entry_with_card_id(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = [{"id": "ms-1", "card_id": "card-1"}]
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_manual_sale("ms-1")
        self.assertEqual(result, {"id": "ms-1", "card_id": "card-1"})
        mock_client.table.return_value.delete.return_value.eq.assert_called_once_with("id", "ms-1")

    def test_returns_none_when_not_found(self):
        mock_client = MagicMock()
        response = MagicMock()
        response.data = []
        mock_client.table.return_value.select.return_value.eq.return_value.execute.return_value = response
        with patch("db.get_client", return_value=mock_client):
            result = db.delete_manual_sale("does-not-exist")
        self.assertIsNone(result)


class StatisticsRowsTests(unittest.TestCase):
    def test_joins_purchase_and_sale_info_per_card(self):
        mock_client = MagicMock()

        def table(name):
            builder = MagicMock()
            response = MagicMock()
            if name == "cards":
                response.data = [
                    {"id": "card-1", "title": "Karte 1", "card_no": 1, "team": "FC Bayern", "set_name": "Topps 2026"},
                    {"id": "card-2", "title": "Karte 2", "card_no": 2, "team": "", "set_name": ""},
                    {"id": "card-3", "title": "Karte 3", "card_no": 3, "team": "", "set_name": ""},
                ]
                builder.select.return_value.execute.return_value = response
            elif name == "purchase_items":
                response.data = [{"card_id": "card-1", "purchase_id": "p1", "allocated_cost": 10.0}]
                builder.select.return_value.in_.return_value.execute.return_value = response
            elif name == "purchases":
                response.data = [{"id": "p1", "purchase_date": "2026-01-01", "platform": "eBay"}]
                builder.select.return_value.in_.return_value.execute.return_value = response
            elif name == "ebay_sales":
                response.data = [{
                    "card_id": "card-1", "sale_date": "2026-02-01T00:00:00+00:00",
                    "gross_price": 15.0, "ebay_fees": 1.75, "refunded": True,
                }]
                builder.select.return_value.in_.return_value.order.return_value.execute.return_value = response
            elif name == "ebay_listings":
                response.data = []
                builder.select.return_value.in_.return_value.execute.return_value = response
            elif name == "manual_sales":
                response.data = [{
                    "card_id": "card-3", "channel": "Kleinanzeigen", "sale_date": "2026-03-01T00:00:00+00:00",
                    "gross_price": 8.0, "fees": 0.5, "refunded": False,
                }]
                builder.select.return_value.in_.return_value.execute.return_value = response
            return builder

        mock_client.table.side_effect = table
        with patch("db.get_client", return_value=mock_client):
            rows = db.statistics_rows()

        by_id = {r["card_id"]: r for r in rows}
        self.assertEqual(by_id["card-1"]["cost"], 10.0)
        self.assertEqual(by_id["card-1"]["purchase_date"], "2026-01-01")
        self.assertEqual(by_id["card-1"]["sale_price"], 15.0)
        self.assertIsNone(by_id["card-2"]["cost"])
        self.assertIsNone(by_id["card-2"]["sale_price"])
        self.assertEqual(by_id["card-1"]["team"], "FC Bayern")
        self.assertEqual(by_id["card-1"]["set_name"], "Topps 2026")
        self.assertEqual(by_id["card-1"]["ebay_fees"], 1.75)
        self.assertIsNone(by_id["card-2"]["ebay_fees"])
        self.assertTrue(by_id["card-1"]["refunded"])
        self.assertFalse(by_id["card-2"]["refunded"])
        self.assertEqual(by_id["card-1"]["platform"], "eBay")
        self.assertEqual(by_id["card-2"]["platform"], "")
        self.assertEqual(by_id["card-1"]["channel"], "eBay")
        self.assertIsNone(by_id["card-2"]["channel"])
        # card-3 has no eBay sale, only a manual sale - statistics_rows()
        # must fall back to it instead of leaving the card "unsold".
        self.assertEqual(by_id["card-3"]["sale_price"], 8.0)
        self.assertEqual(by_id["card-3"]["ebay_fees"], 0.5)
        self.assertEqual(by_id["card-3"]["channel"], "Kleinanzeigen")
        self.assertEqual(by_id["card-3"]["sale_date"], "2026-03-01T00:00:00+00:00")
        self.assertFalse(by_id["card-3"]["refunded"])

    def test_returns_empty_list_when_no_cards(self):
        mock_client = MagicMock()
        _mock_table(mock_client, "cards", [])
        with patch("db.get_client", return_value=mock_client):
            self.assertEqual(db.statistics_rows(), [])


if __name__ == "__main__":
    unittest.main()
