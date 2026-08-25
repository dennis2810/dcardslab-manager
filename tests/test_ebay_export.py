"""Regression tests for the eBay active-listing export.

The app module normally requires tkinter, OpenCV and a real Tesseract/Google
setup just to import. None of that is needed to exercise the pure export
logic, so the GUI/vision dependencies are stubbed out before import. Run
with:

    python3 -m unittest discover -s tests -v
"""
import csv
import json
import sqlite3
import sys
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path


def _stub(name):
    if name in sys.modules:
        return sys.modules[name]
    module = types.ModuleType(name)
    sys.modules[name] = module
    return module


_stub("cv2")
_stub("numpy")
_tkinter = _stub("tkinter")
for _sub in ("ttk", "filedialog", "messagebox", "simpledialog"):
    setattr(_tkinter, _sub, _stub(f"tkinter.{_sub}"))

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "app"))
import dcardlabs_manager as app  # noqa: E402


class CardConditionValueTests(unittest.TestCase):
    """_ebay_card_condition_value maps the physical card grade to eBay's
    'CD:Kartenzustand' descriptor value IDs."""

    def test_maps_known_physical_conditions(self):
        self.assertEqual(app._ebay_card_condition_value("NM"), "400010")
        self.assertEqual(app._ebay_card_condition_value("EX"), "400011")
        self.assertEqual(app._ebay_card_condition_value("VG"), "400012")
        self.assertEqual(app._ebay_card_condition_value("G"), "400013")
        self.assertEqual(app._ebay_card_condition_value("Poor"), "400013")

    def test_unknown_but_present_condition_falls_back_to_near_mint(self):
        # Documents the existing fallback behaviour: an unrecognised but
        # non-empty value (e.g. a raw eBay ConditionID like "4000") still
        # resolves to 400010 rather than raising or exporting a blank cell.
        self.assertEqual(app._ebay_card_condition_value("4000"), "400010")

    def test_empty_condition_yields_empty_string(self):
        self.assertEqual(app._ebay_card_condition_value(""), "")
        self.assertEqual(app._ebay_card_condition_value(None), "")


class RequiredAspectsTests(unittest.TestCase):
    """Regression: _ebay_required_aspects() must only report real
    category-specific item aspects (C:/CD:/CDA: columns), not core File
    Exchange fields like *Description or *StartPrice - otherwise every
    export is blocked regardless of how complete the card data is."""

    def _full_card(self, **overrides):
        card = {
            "card_id": 1, "category": "Fußball", "theme": "Bundesliga",
            "team": "FC Test", "manufacturer": "Panini", "set_name": "2024/25",
            "title": "Test Spieler", "season_year": "2024/25", "card_number": "123",
            "card_type": "Base", "variant": "", "front_image": "front.jpg",
            "back_image": "back.jpg",
        }
        card.update(overrides)
        return card

    def test_core_listing_fields_are_not_treated_as_aspects(self):
        aspects = app._ebay_required_aspects("football")
        for core_field in (
            "*Description", "*Format", "*Duration", "*StartPrice",
            "*Quantity", "*Location", "*DispatchTimeMax",
            "*ReturnsAcceptedOption",
        ):
            self.assertNotIn(core_field, aspects)

    def test_fully_populated_card_passes_validation(self):
        errors = app._ebay_draft_validation(
            self._full_card(), "Test Spieler", "4000", "9.99", "FixedPrice",
            "261328", "Testbeschreibung", "football",
        )
        self.assertEqual(errors, [])

    def test_sport_is_derived_from_template_not_card_category(self):
        # Sportart is now auto-derived from the selected export template
        # (football -> "Fussball"), independent of the card's own
        # "Kategorie" field, so an empty card category must not block
        # validation.
        errors = app._ebay_draft_validation(
            self._full_card(category=""), "Test Spieler", "4000", "9.99",
            "FixedPrice", "261328", "Testbeschreibung", "football",
        )
        self.assertEqual(errors, [])
        self.assertTrue(app._ebay_required_aspect_value("Sportart", {}, "football"))

    def test_missing_images_are_still_caught(self):
        errors = app._ebay_draft_validation(
            self._full_card(front_image="", back_image=""), "Test Spieler",
            "4000", "9.99", "FixedPrice", "261328", "Testbeschreibung",
            "football",
        )
        self.assertTrue(any("Bilder" in e for e in errors), errors)


class ExportOfferIntegrationTest(unittest.TestCase):
    """End-to-end: builds a temp DB with one fully-valid card and runs the
    real active-listing export, then checks the generated CSV row."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)

        self._orig_db = app.DB
        app.DB = tmp / "dcardlabs_test.db"
        self.addCleanup(lambda: setattr(app, "DB", self._orig_db))

        self.out_dir = tmp / "export"
        self.out_dir.mkdir()

        fake_gdrive = _stub("google_drive_sync")
        fake_gdrive.upload_card_images = (
            lambda base, card_id, front_path=None, back_path=None: {
                "urls": ["https://example.invalid/front.jpg"]
            }
        )

        # The tkinter stubs are bare ModuleType objects with no attributes of
        # their own yet, so there is nothing to save/restore here.
        app.filedialog.askdirectory = lambda **kwargs: str(self.out_dir)
        app.messagebox.askyesno = lambda *a, **k: True
        self.errors_shown = []
        app.messagebox.showerror = lambda title, msg, **k: self.errors_shown.append(msg)
        app.messagebox.showinfo = lambda *a, **k: None

        conn = app.db()
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            """INSERT INTO cards
               (card_id, category, theme, team, manufacturer, set_name, title,
                season_year, card_number, card_type, variant, front_image,
                back_image, created_at)
               VALUES (1,'Fußball','Bundesliga','FC Test','Panini','2024/25',
                       'Test Spieler','2024/25','123','Base','','front.jpg',
                       'back.jpg',?)""",
            (now,),
        )
        conn.execute(
            "INSERT INTO inventory (card_id, quantity, condition) VALUES (1,1,'EX')"
        )
        conn.execute(
            """INSERT INTO ebay_listings
               (card_id, title, description, condition, price, listing_format,
                category, sku, status, created_at, updated_at)
               VALUES (1,'Test Spieler','Testbeschreibung','4000',9.99,
                       'FixedPrice','261328','DC-000001','Entwurf',?,?)""",
            (now, now),
        )
        conn.commit()
        conn.close()

    def test_export_writes_actual_card_condition_not_always_near_mint(self):
        app.ebay_export_offer_from_template(None, template_key="football", schedule_hours=2)

        self.assertEqual(self.errors_shown, [], "export reported an error: %s" % self.errors_shown)

        csv_files = list(self.out_dir.glob("**/*.csv"))
        self.assertEqual(len(csv_files), 1, f"expected exactly one export CSV, found {csv_files}")

        with csv_files[0].open(encoding="utf-8-sig") as f:
            rows = list(csv.reader(f, delimiter=";"))
        header_row = next(r for r in rows if r and r[0].startswith("*Action"))
        data_row = rows[-1]
        hm = app._ebay_template_header_map(header_row)
        hidx = {h: i for i, h in enumerate(header_row)}

        condition_id = data_row[hidx[hm["condition"]]]
        card_condition = data_row[hidx[hm["card_condition"]]]

        self.assertEqual(condition_id, "4000")
        # Regression: this used to always be 400010 (Near Mint) no matter
        # what the card's actual inventory condition was.
        self.assertEqual(card_condition, "400011")  # EX -> 400011


class SandboxOfferConditionTest(unittest.TestCase):
    """ebay_sandbox_create_offer must send eBay's numeric ConditionID
    (e.g. "4000"), never the generic "NEW" enum (-> ConditionID 1000,
    rejected by eBay for Trading Cards categories like 261328 with
    errorId 25059) or the raw UI display text ("4000 - Ungraded")."""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmpdir.cleanup)
        tmp = Path(self._tmpdir.name)

        self._orig_db = app.DB
        app.DB = tmp / "dcardlabs_test_sandbox.db"
        self.addCleanup(lambda: setattr(app, "DB", self._orig_db))

        conn = app.db()
        now = datetime.now().isoformat(timespec="seconds")
        conn.execute(
            "INSERT INTO cards (card_id, title, created_at) VALUES (1,'Test Karte',?)",
            (now,),
        )
        conn.execute("INSERT INTO inventory (card_id, quantity) VALUES (1,1)")
        conn.commit()
        conn.close()

    def _sent_payload(self, condition_arg, template_key="football"):
        """Call ebay_sandbox_create_offer with a stubbed HTTP layer and
        return the full JSON body it sent."""
        captured = {}

        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def read(self):
                return json.dumps({
                    "success": True, "offer": {"offer_id": "999"},
                }).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            captured["body"] = json.loads(req.data.decode("utf-8"))
            return FakeResponse()

        orig_urlopen = app.urllib.request.urlopen
        app.urllib.request.urlopen = fake_urlopen
        try:
            app.ebay_sandbox_create_offer(
                1, "Titel", "Beschreibung", condition_arg, 9.99,
                "Festpreis", "261328", "SKU1", template_key,
            )
        finally:
            app.urllib.request.urlopen = orig_urlopen
        return captured["body"]

    def _sent_condition(self, condition_arg, template_key="football"):
        return self._sent_payload(condition_arg, template_key)["condition"]

    def test_ui_display_text_is_reduced_to_the_numeric_condition_id(self):
        self.assertEqual(self._sent_condition("4000 – Ungraded"), "4000")
        self.assertEqual(self._sent_condition("2750 – Graded"), "2750")

    def test_bare_numeric_condition_id_still_works(self):
        self.assertEqual(self._sent_condition("4000"), "4000")

    def test_never_sends_the_generic_new_enum(self):
        # "NEW" resolves to ConditionID 1000 in eBay's generic enum, which
        # is invalid for category 261328 (errorId 25059).
        self.assertNotEqual(self._sent_condition("4000 – Ungraded"), "NEW")

    def test_sportart_aspect_is_included_for_the_selected_template(self):
        # Regression: category 261328 requires the "Sportart" item
        # specific (eBay errorId 25002, "Das Artikelmerkmal Sportart
        # fehlt.") - it was missing from the aspects payload entirely.
        body = self._sent_payload("4000 – Ungraded", template_key="football")
        self.assertEqual(body["aspects"].get("Sportart"), ["Fußball"])

    def test_inventory_item_failure_surfaces_the_actual_ebay_error(self):
        # Regression: the OAuth server reports inventory-item failures as
        # {"error": "<generic wrapper text>", "response": {<real eBay
        # error JSON>}} - the client used to only look at
        # result["offer"]["response"] (never populated for this failure
        # shape), so the real eBay error message was silently dropped and
        # only the generic wrapper text reached the user.
        class FakeResponse:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *exc_info):
                return False

            def read(self):
                return json.dumps({
                    "success": False,
                    "error": "eBay Inventory Item konnte nicht erstellt/aktualisiert werden.",
                    "response": {
                        "errors": [{"message": "Ganz genau dieser eBay-Fehlertext"}]
                    },
                }).encode("utf-8")

        def fake_urlopen(req, timeout=None):
            return FakeResponse()

        orig_urlopen = app.urllib.request.urlopen
        app.urllib.request.urlopen = fake_urlopen
        try:
            with self.assertRaises(RuntimeError) as ctx:
                app.ebay_sandbox_create_offer(
                    1, "Titel", "Beschreibung", "4000 – Ungraded", 9.99,
                    "Festpreis", "261328", "SKU1",
                )
        finally:
            app.urllib.request.urlopen = orig_urlopen
        self.assertIn("Ganz genau dieser eBay-Fehlertext", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
