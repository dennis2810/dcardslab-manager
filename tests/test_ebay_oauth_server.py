"""Regression tests for the eBay OAuth/API server (ebay-oauth-server/app.py).

Flask isn't installed in every environment this runs in, and importing
the real thing would start binding routes for no benefit here, so a
minimal Flask stub is installed before import - just enough surface
(Flask, @app.get/@app.post decorators, jsonify, request, redirect,
Response) for the module to import without a running server.

    python3 -m unittest discover -s tests -v
"""
import sys
import types
import unittest
from pathlib import Path


def _install_flask_stub():
    if "flask" in sys.modules:
        return

    flask_module = types.ModuleType("flask")

    class _StubApp:
        def get(self, *_a, **_kw):
            return lambda fn: fn

        def post(self, *_a, **_kw):
            return lambda fn: fn

    class _StubRequest:
        args = {}

        def get_json(self, silent=True):
            return {}

    def _jsonify(*_a, **kw):
        return kw

    def _redirect(*_a, **_kw):
        return None

    flask_module.Flask = lambda *_a, **_kw: _StubApp()
    flask_module.jsonify = _jsonify
    flask_module.redirect = _redirect
    flask_module.request = _StubRequest()
    flask_module.Response = object
    sys.modules["flask"] = flask_module


_install_flask_stub()

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "ebay-oauth-server"))
import app as oauth_server  # noqa: E402


class ConditionIdToEnumTests(unittest.TestCase):
    """The Inventory API's "condition" field takes a ConditionEnum string
    (e.g. "USED_VERY_GOOD"), not DCardsLab's numeric ConditionID - sending
    the numeric value directly fails with eBay errorId 2004 ("Could not
    serialize field [condition]")."""

    def test_ungraded_and_graded_ids_map_to_documented_enums(self):
        self.assertEqual(oauth_server.condition_id_to_enum("4000"), "USED_VERY_GOOD")
        self.assertEqual(oauth_server.condition_id_to_enum("2750"), "LIKE_NEW")

    def test_never_passes_a_bare_numeric_string_through(self):
        for condition_id in ("1000", "1500", "1750", "2000", "2500", "2750",
                              "3000", "4000", "5000", "6000", "7000"):
            enum_value = oauth_server.condition_id_to_enum(condition_id)
            self.assertFalse(str(enum_value).isdigit(), enum_value)

    def test_unknown_value_passes_through_unchanged(self):
        # Defensive fallback - an already-valid enum string (or a value
        # this table doesn't know about) must not be mangled.
        self.assertEqual(oauth_server.condition_id_to_enum("USED_GOOD"), "USED_GOOD")


if __name__ == "__main__":
    unittest.main()
