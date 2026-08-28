"""Pure logic for eBay listings: title/description generation, listing-type
(Sport/Non-Sport) derivation, item specifics (aspects), price-research
links, and sales-sync matching. No HTTP, no DB - easy to unit test.
"""
import csv
import html
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


# The seller's fixed link to their own other eBay listings, used unchanged
# across every generated description (see EBAY_SHOP_SEARCH_URL below).
EBAY_SHOP_SEARCH_URL = "https://ebay.de/sch/dennis281086/m.html"

# The seller's real standard footer (shipping/combined-shipping info, a
# link to their other listings, a legal disclaimer) - eBay's
# listingDescription field accepts HTML, so this replaces the previous
# plain-text-only footer with the seller's actual, already-in-use listing
# text (provided by the user, not portable from the desktop app - that one
# never had this content).
_DESCRIPTION_FOOTER_HTML = f"""<p><strong>Versand &amp; Kombiversand:</strong></p>
<ul>
  <li><strong>Sicher verpackt:</strong> Jede Karte wird geschützt in einer weichen Hülle (Sleeve) und zusätzlich in einer festen Plastikhülle (Toploader) knicksicher versendet.</li>
  <li><strong>Versandrabatt: Kombiversand ist aktiv!</strong> Egal wie viele Karten du bei mir kaufst, du zahlst nur einmalig die Versandkosten für den ersten Artikel. Jede weitere Karte reist komplett kostenlos mit.</li>
  <li><em>Wichtig bei Großbestellungen:</em> Bitte vor der Zahlung die Gesamtrechnung abwarten, falls die Kartenanzahl das Gewicht für einen Standardbrief überschreitet.</li>
</ul>
<p>🔗 <strong>Mehr Karten entdecken:</strong></p>
<p>👉 <a href="{EBAY_SHOP_SEARCH_URL}"><strong>Hier klicken, um meine anderen Sammelkarten anzusehen und Versandkosten zu sparen!</strong></a></p>
<p><em>Rechtlicher Hinweis: Dies ist ein Privatverkauf. Der Verkauf erfolgt unter Ausschluss jeglicher Gewährleistung, Sachmängelhaftung oder Rücknahme.</em></p>"""


def generate_description(card):
    lines = [f"<p><strong>{html.escape(generate_title(card, max_len=200))}</strong></p>"]
    items = []
    for label, key in (
        ("Set", "set_name"), ("Saison", "season_year"),
        ("Team", "team"), ("Kartennummer", "card_number"),
    ):
        value = card.get(key)
        if value:
            items.append(f"<li>{html.escape(label)}: {html.escape(str(value))}</li>")
    if items:
        lines.append("<ul>" + "".join(items) + "</ul>")
    lines.append("<p>Zustand: siehe Angebot. Versand aus Deutschland.</p>")
    lines.append(_DESCRIPTION_FOOTER_HTML)
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
