"""Tests for webapp-poc/ebay_listing.py - pure logic, no HTTP/DB."""
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import ebay_listing  # noqa: E402


def _card(**overrides):
    card = {
        "title": "Musterkarte", "category": "Fußball", "theme": "",
        "manufacturer": "Topps", "set_name": "Bundesliga 2024",
        "season_year": "2024", "card_type": "", "variant": "",
        "team": "FC Beispiel", "card_number": "12", "serial_number": "",
    }
    card.update(overrides)
    return card


class SkuForCardTests(unittest.TestCase):
    def test_formats_as_zero_padded_sequential_number(self):
        # cards.id is a UUID - a poor SKU/inventory reference to read off
        # an eBay order export by eye. card_no is a short, sequential,
        # human-readable number instead (see supabase/schema.sql migration).
        self.assertEqual(ebay_listing.sku_for_card(123), "webapp-000123")

    def test_pads_small_numbers(self):
        self.assertEqual(ebay_listing.sku_for_card(1), "webapp-000001")


class GenerateTitleTests(unittest.TestCase):
    def test_builds_title_from_card_fields(self):
        title = ebay_listing.generate_title(_card())
        self.assertIn("Musterkarte", title)
        self.assertIn("Topps", title)
        self.assertIn("#12", title)

    def test_skips_empty_fields(self):
        title = ebay_listing.generate_title(_card(manufacturer="", variant=""))
        self.assertNotIn("  ", title)

    def test_truncates_to_max_len(self):
        long_title = ebay_listing.generate_title(_card(title="X" * 200), max_len=80)
        self.assertLessEqual(len(long_title), 80)


class GenerateDescriptionTests(unittest.TestCase):
    def test_includes_key_card_fields(self):
        description = ebay_listing.generate_description(_card())
        self.assertIn("Bundesliga 2024", description)
        self.assertIn("2024", description)
        self.assertIn("FC Beispiel", description)

    def test_is_html_with_the_sellers_standard_shipping_and_legal_footer(self):
        # The user's real eBay listings use a fixed HTML footer (shipping/
        # combined-shipping info, a link to their other listings, a legal
        # disclaimer) - eBay's listingDescription field accepts HTML, so
        # this is appended after the per-card details instead of the
        # previous plain-text-only description.
        description = ebay_listing.generate_description(_card())
        self.assertIn("<ul>", description)
        self.assertIn("Versand", description)
        self.assertIn("Kombiversand", description)
        self.assertIn(f'<a href="{ebay_listing.EBAY_SHOP_SEARCH_URL}"', description)
        self.assertIn("Privatverkauf", description)
        self.assertIn("Gewährleistung", description)

    def test_escapes_html_special_characters_in_card_fields(self):
        description = ebay_listing.generate_description(_card(team="A & B <script>"))
        self.assertNotIn("<script>", description)
        self.assertIn("A &amp; B &lt;script&gt;", description)


class DeriveListingTypeTests(unittest.TestCase):
    def test_known_sport_is_sport(self):
        self.assertEqual(ebay_listing.derive_listing_type(_card(category="Fußball")), "sport")
        self.assertEqual(ebay_listing.derive_listing_type(_card(category="Basketball")), "sport")

    def test_unknown_category_is_non_sport(self):
        self.assertEqual(ebay_listing.derive_listing_type(_card(category="Pokémon")), "non_sport")

    def test_missing_category_is_non_sport(self):
        self.assertEqual(ebay_listing.derive_listing_type(_card(category="")), "non_sport")


class BuildAspectsTests(unittest.TestCase):
    def test_sport_sets_sportart_aspect(self):
        aspects = ebay_listing.build_aspects(_card(category="Fußball"), "sport")
        self.assertEqual(aspects["Sportart"], ["Fußball"])

    def test_non_sport_sets_franchise_aspect_instead(self):
        card = _card(category="Pokémon")
        aspects = ebay_listing.build_aspects(card, "non_sport")
        self.assertNotIn("Sportart", aspects)
        self.assertEqual(aspects["Franchise"], ["Pokémon"])

    def test_empty_card_fields_are_omitted(self):
        aspects = ebay_listing.build_aspects(_card(team="", manufacturer=""), "sport")
        self.assertNotIn("Team / Verein", aspects)
        self.assertNotIn("Hersteller", aspects)

    def test_includes_team_manufacturer_set_season_card_number(self):
        aspects = ebay_listing.build_aspects(_card(), "sport")
        self.assertEqual(aspects["Team / Verein"], ["FC Beispiel"])
        self.assertEqual(aspects["Hersteller"], ["Topps"])
        self.assertEqual(aspects["Set / Serie"], ["Bundesliga 2024"])
        self.assertEqual(aspects["Saison / Jahr"], ["2024"])
        self.assertEqual(aspects["Kartennummer"], ["12"])


class RequiredAspectsTests(unittest.TestCase):
    """Reads the real eBay-provided CSV templates in templates/ebay/ - no
    mock needed, this is a regression test against the actual files."""

    def test_sport_requires_sportart(self):
        self.assertEqual(ebay_listing.required_aspects("sport"), ["Sportart"])

    def test_non_sport_requires_franchise(self):
        self.assertEqual(ebay_listing.required_aspects("non_sport"), ["Franchise"])


class MissingAspectsTests(unittest.TestCase):
    def test_empty_aspects_reports_all_required(self):
        self.assertEqual(ebay_listing.missing_aspects({}, "sport"), ["Sportart"])

    def test_complete_aspects_reports_nothing(self):
        aspects = {"Sportart": ["Fußball"]}
        self.assertEqual(ebay_listing.missing_aspects(aspects, "sport"), [])

    def test_blank_value_counts_as_missing(self):
        aspects = {"Sportart": []}
        self.assertEqual(ebay_listing.missing_aspects(aspects, "sport"), ["Sportart"])


class PriceResearchLinksTests(unittest.TestCase):
    def test_urlencodes_title_and_returns_both_links(self):
        links = ebay_listing.price_research_links("Musterkarte 2024 #12")
        self.assertIn("ebay_sold", links)
        self.assertIn("onepoint", links)
        self.assertIn("Musterkarte+2024", links["ebay_sold"])
        self.assertIn("130point.com", links["onepoint"])
        self.assertNotIn(" ", links["ebay_sold"])


class MatchSaleLineItemTests(unittest.TestCase):
    def test_matches_by_sku(self):
        listings_by_sku = {"webapp-card-1": {"id": "listing-1"}}
        result = ebay_listing.match_sale_line_item({"sku": "webapp-card-1"}, listings_by_sku)
        self.assertEqual(result, {"id": "listing-1"})

    def test_returns_none_for_unknown_sku(self):
        result = ebay_listing.match_sale_line_item({"sku": "unknown"}, {})
        self.assertIsNone(result)


if __name__ == "__main__":
    unittest.main()
