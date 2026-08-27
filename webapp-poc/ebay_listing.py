"""Pure logic for eBay listings: title/description generation, listing-type
(Sport/Non-Sport) derivation, item specifics (aspects), price-research
links, and sales-sync matching. No HTTP, no DB - easy to unit test.
"""
import csv
from pathlib import Path
from urllib.parse import quote_plus

TEMPLATES_DIR = Path(__file__).resolve().parent.parent / "templates" / "ebay"

CATEGORY_IDS = {"sport": "261328", "non_sport": "183050"}
TEMPLATE_FILES = {
    "sport": TEMPLATES_DIR / "eBay-category-listing-template_261328.csv",
    "non_sport": TEMPLATES_DIR / "eBay-category-listing-template_non_sport.csv",
}

# Only a default guess for the automatic Sport/Non-Sport derivation - always
# overridable in the UI (see spec, section "Kartentyp").
_KNOWN_SPORTS = {
    "Fußball", "Basketball", "Baseball", "Eishockey", "American Football",
    "Tennis", "Boxen", "Golf", "Motorsport", "Formel 1", "Wrestling",
    "Rugby", "Cricket",
}


def sku_for_card(card_id):
    return f"webapp-{card_id}"


def generate_title(card, max_len=80):
    number = card.get("card_number")
    parts = [
        card.get("season_year", ""), card.get("manufacturer", ""),
        card.get("set_name", ""), card.get("title", ""), card.get("team", ""),
        f"#{number}" if number else "", card.get("variant", ""),
    ]
    return " ".join(p for p in parts if p).strip()[:max_len]


def generate_description(card):
    lines = [generate_title(card, max_len=200)]
    for label, key in (
        ("Set", "set_name"), ("Saison", "season_year"),
        ("Team", "team"), ("Kartennummer", "card_number"),
    ):
        value = card.get(key)
        if value:
            lines.append(f"{label}: {value}")
    lines.append("Zustand: siehe Angebot. Versand aus Deutschland.")
    return "\n".join(lines)


def derive_listing_type(card):
    category = str(card.get("category") or "").strip()
    return "sport" if category in _KNOWN_SPORTS else "non_sport"


def build_aspects(card, listing_type):
    aspects = {}
    category = str(card.get("category") or "").strip()
    if category:
        aspects["Sportart" if listing_type == "sport" else "Franchise"] = [category]
    for label, key in (
        ("Team / Verein", "team"), ("Hersteller", "manufacturer"),
        ("Set / Serie", "set_name"), ("Saison / Jahr", "season_year"),
        ("Kartennummer", "card_number"),
    ):
        value = str(card.get(key) or "").strip()
        if value:
            aspects[label] = [value]
    return aspects


def required_aspects(listing_type):
    path = TEMPLATE_FILES[listing_type]
    rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
    if len(rows) < 2:
        return []
    labels = []
    for header in rows[1]:
        header = str(header or "").strip()
        if header.startswith("*C:"):
            labels.append(header[len("*C:"):].split(" - (ID:")[0].strip())
    return labels


def missing_aspects(aspects, listing_type):
    return [label for label in required_aspects(listing_type) if not aspects.get(label)]


def price_research_links(title):
    q = quote_plus(title)
    return {
        "ebay_sold": f"https://www.ebay.de/sch/i.html?_nkw={q}&LH_Sold=1&LH_Complete=1",
        "onepoint": f"https://130point.com/sales/?search={q}",
    }


def match_sale_line_item(line_item, listings_by_sku):
    return listings_by_sku.get(line_item.get("sku"))
