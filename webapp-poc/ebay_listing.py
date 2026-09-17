"""Pure logic for eBay listings: title/description generation, listing-type
(Sport/Non-Sport) derivation, item specifics (aspects), price-research
links, and sales-sync matching. No HTTP, no DB - easy to unit test.
"""
import csv
import html
import re
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


def sku_for_card(card_no):
    return f"webapp-{card_no:06d}"


def _region_label(card):
    """Set-Name und Kategorie sagen bei Nicht-Sport-Karten oft dasselbe aus
    (z.B. Kategorie "One Piece" vs. Set "One Piece Official Trading Card
    Collection") - im Titel nur EINEN der beiden zeigen statt einer
    Dopplung, und zwar den laengeren/spezifischeren (deckt sowohl den
    Ueberschneidungsfall als auch "nur einer der beiden ist gesetzt" ab).
    Bei Sport-Karten faellt das meist auf den (spezifischeren) Set-Namen,
    da Team separat dazukommt."""
    category = (card.get("category") or "").strip()
    set_name = (card.get("set_name") or "").strip()
    if category and set_name:
        return set_name if len(set_name) >= len(category) else category
    return set_name or category


# Kartentyp/Variante nennen Autogramm/Rookie oft ausgeschrieben ("Autograph",
# "Rookie Card") statt in der im Hobby ueblichen Kurzform - im (Platz-
# begrenzten) Titel wird trotzdem einheitlich abgekuerzt. Laengere Phrase
# zuerst, damit "Rookie Card" nicht schon durch die "Rookie"-Regel verstuemmelt
# wird, bevor "Card" separat stehen bleibt.
_TITLE_ABBREVIATIONS = [
    (re.compile(r"\brookie\s*card\b", re.IGNORECASE), "RC"),
    (re.compile(r"\brookie\b", re.IGNORECASE), "RC"),
    (re.compile(r"\bautographs?\b", re.IGNORECASE), "Auto"),
]


def _abbreviate_title_term(value):
    for pattern, replacement in _TITLE_ABBREVIATIONS:
        value = pattern.sub(replacement, value)
    return value


def generate_title(card, max_len=80):
    # Reihenfolge (mit dem Nutzer abgestimmt): Jahr, Hersteller, Set/
    # Kategorie, Spieler/Charakter, Team, Typ, Variante, dann RC/Auto/
    # Auflage. Jahr/Titel sind Pflichtbestandteile (garantiert im Titel,
    # notfalls ueber die Kuerzung am Ende); alles andere wird nur angehaengt,
    # soweit noch Platz bis max_len (eBays 80-Zeichen-Limit) ist, damit ein
    # zu langer Titel nicht die wichtigsten Angaben verdraengt.
    manufacturer = (card.get("manufacturer") or "").strip()
    region_label = _region_label(card)
    # Der Hersteller steht oft schon selbst im Set-Namen (z.B. "Topps" in
    # "Topps Finest UEFA Club Competitions") - dann nicht zusaetzlich separat
    # zeigen, um "Topps Topps Finest..." zu vermeiden.
    if manufacturer and region_label and manufacturer.lower() in region_label.lower():
        manufacturer = ""
    parts = [
        (card.get("season_year", ""), True),
        (manufacturer, False),
        (region_label, False),
        (card.get("title", ""), True),
        (card.get("team", ""), False),
        (_abbreviate_title_term(card.get("card_type") or ""), False),
        (_abbreviate_title_term(card.get("variant") or ""), False),
        ("RC" if card.get("is_rookie") else "", False),
        ("Auto" if card.get("is_autograph") else "", False),
        (f"/{card['print_run']}" if card.get("print_run") else "", False),
    ]
    title = ""
    for value, mandatory in parts:
        value = (value or "").strip()
        if not value:
            continue
        # Ueberspringt einen optionalen Teil, der (z.B. nach der RC/Auto-
        # Abkuerzung oben) schon woanders im Titel steckt - etwa wenn die
        # Variante schon "Auto" abgekuerzt wurde und zusaetzlich das
        # Autogramm-Kaestchen gesetzt ist.
        if not mandatory and value.lower() in title.lower():
            continue
        candidate = f"{title} {value}".strip()
        if mandatory or len(candidate) <= max_len:
            title = candidate
    return title[:max_len]


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


def generate_description(card, extra_note=""):
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
    # Optionaler, wiederverwendbarer Textbaustein (siehe description_templates-
    # Tabelle/settings.html) - z.B. ein Hinweis fuer eine ganze Set-Serie,
    # damit er nicht bei jeder aehnlichen Karte neu getippt werden muss. Vor
    # dem festen Versand-/Rechtstext-Footer, da er sich inhaltlich noch auf
    # die Karte selbst bezieht.
    note = (extra_note or "").strip()
    if note:
        lines.append(f'<p class="extra-note">{html.escape(note)}</p>')
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
    # DCardsLab has no per-card autograph field - "Nein" is the correct
    # default for the ordinary (non-autographed) card and saves the
    # seller from having to fill it in by hand on every listing.
    aspects["Mit Autogramm"] = ["Nein"]
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


def match_sale_line_item(line_item, listings_by_sku, listings_by_item_id=None):
    # SKU-Match zuerst (der Normalfall: jedes ueber dieses Tool erstellte
    # Angebot hat eine, siehe put_inventory_item()). Fallback auf eBays
    # eigene Artikelnummer (legacyItemId) fuer importierte Angebote (siehe
    # main.py's import_ebay_listing()) - die wurden nie ueber eBays
    # Inventory API angelegt, tragen auf eBay selbst also nie unsere
    # generierte SKU (sku_for_card()); eBays Bestell-Zeilenposten liefert
    # dafuer aber weiterhin die echte Artikelnummer, die wir beim Import als
    # ebay_listing_id gespeichert haben.
    listing = listings_by_sku.get(line_item.get("sku"))
    if listing is not None:
        return listing
    if listings_by_item_id:
        return listings_by_item_id.get(line_item.get("legacyItemId"))
    return None
