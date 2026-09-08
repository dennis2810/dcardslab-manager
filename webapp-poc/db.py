"""scan_batches/cards persistence via the Supabase Postgres client.
Field names mirror integrations/ai_card_recognition.py's recognize_card()
output 1:1 - duplicated here rather than imported, so this module has no
import-order dependency on integrations/ being on sys.path first."""
from collections import Counter

from supabase_client import get_client

CARD_FIELDS = [
    "title", "category", "theme", "manufacturer", "set_name",
    "season_year", "card_type", "variant", "team", "position",
    "squad_number", "club_debut_season", "card_number",
    "serial_number", "print_run",
]


def create_batch(card_count):
    response = get_client().table("scan_batches").insert(
        {"card_count": card_count, "status": "pending"}
    ).execute()
    return response.data[0]["id"]


def update_batch_status(batch_id, status):
    get_client().table("scan_batches").update({"status": status}).eq("id", batch_id).execute()


def insert_card(batch_id, position_in_batch, fields, front_image_path, back_image_path):
    row = {name: fields.get(name, "") for name in CARD_FIELDS}
    row.update({
        "batch_id": batch_id,
        "position_in_batch": position_in_batch,
        "is_numbered": bool(fields.get("is_numbered")),
        "confidence": fields.get("confidence"),
        "recognition_status": fields.get("status", ""),
        "front_image_path": front_image_path,
        "back_image_path": back_image_path,
    })
    response = get_client().table("cards").insert(row).execute()
    return response.data[0]


def _ilike_search_filter(q, columns):
    """Builds a PostgREST or_()-Filterstring 'col.ilike.%q%,...' fuer
    mehrere Spalten. Entfernt Komma/Klammern aus q, da diese in der
    or_()-Filtersyntax als Trenner/Gruppierung gelten wuerden."""
    safe_q = q.replace(",", " ").replace("(", " ").replace(")", " ")
    pattern = f"%{safe_q}%"
    return ",".join(f"{col}.ilike.{pattern}" for col in columns)


def list_cards(q=None, status=None):
    query = get_client().table("cards").select("*")
    if q:
        query = query.or_(_ilike_search_filter(q, ["title", "team", "set_name", "card_number", "season_year", "tags"]))
    if status:
        query = query.eq("recognition_status", status)
    response = query.order("created_at", desc=True).execute()
    return response.data


def list_cards_by_sku(q):
    """Karten, deren verknuepftes eBay-Angebot eine passende SKU hat - die
    SKU lebt in ebay_listings, nicht in cards, daher kein Teil von
    list_cards()/_ilike_search_filter() selbst; main.py's /api/cards
    mischt das Ergebnis in die normale Suche."""
    safe_q = q.replace(",", " ").replace("(", " ").replace(")", " ")
    listing_response = (
        get_client().table("ebay_listings").select("card_id")
        .ilike("sku", f"%{safe_q}%").execute()
    )
    card_ids = [row["card_id"] for row in listing_response.data]
    if not card_ids:
        return []
    response = get_client().table("cards").select("*").in_("id", card_ids).execute()
    return response.data


def find_duplicate_card(title, set_name, card_number):
    """Warnt beim Scannen, wenn eine Karte mit demselben Titel/Set/
    Kartennummer bereits im Bestand ist - rein informativ (blockiert das
    Anlegen nicht), da Karten mit identischem Titel+Set+Nummer sehr
    wahrscheinlich echte Duplikate (zweimal gescannt) statt Zufall sind."""
    if not (title and set_name and card_number):
        return None
    response = (
        get_client().table("cards").select("id,title,set_name,card_number,card_no")
        .ilike("title", title).ilike("set_name", set_name).ilike("card_number", card_number)
        .limit(1).execute()
    )
    return response.data[0] if response.data else None


def get_card(card_id):
    response = get_client().table("cards").select("*").eq("id", card_id).execute()
    return response.data[0] if response.data else None


def update_card(card_id, fields):
    row = {
        name: value for name, value in fields.items()
        if name in CARD_FIELDS or name in ("recognition_status", "shipped", "tags")
    }
    if not row:
        return get_card(card_id)
    response = get_client().table("cards").update(row).eq("id", card_id).execute()
    return response.data[0] if response.data else None


def delete_card(card_id):
    card = get_card(card_id)
    if card is None:
        return None
    get_client().table("cards").delete().eq("id", card_id).execute()
    return card


PURCHASE_FIELDS = ["purchase_date", "platform", "seller", "shipping", "total_price", "notes"]
PURCHASE_NUMERIC_FIELDS = {"shipping", "total_price"}
PURCHASE_MONEY_FIELDS = {"shipping", "total_price"}
PURCHASE_ITEM_DEFAULTS = {"allocated_cost": 0, "quantity": 1, "notes": ""}
PURCHASE_ITEM_NUMERIC_FIELDS = {"allocated_cost", "quantity"}
PURCHASE_ITEM_MONEY_FIELDS = {"allocated_cost"}


class CardAlreadyLinkedError(Exception):
    """Raised by add_purchase_item() when card_id bereits eine
    purchase_items-Zeile hat (unique(card_id) im Schema). Die Karten-ID
    steht in exc.args[0]."""


def _blank_numeric_to_none(row, numeric_fields):
    # HTML number-Inputs, die leer gelassen werden, schicken "" statt gar
    # keinen Wert - ein leerer String auf einer numeric-Spalte laesst
    # Postgres/PostgREST den Insert/Update mit einem rohen 500 ablehnen.
    # None (-> NULL) ist erlaubt, da keine der Spalten NOT NULL ist.
    for name in numeric_fields:
        if row.get(name) == "":
            row[name] = None
    return row


def _round_money(row, money_fields):
    # Rundet Geldbetraege konsequent auf 2 Nachkommastellen - unabhaengig
    # davon, was das Frontend an Praezision durchlaesst (z.B. ein manuell
    # eingetragener Anteil-Preis mit mehr Nachkommastellen). Greift nach
    # _blank_numeric_to_none(), das leere Strings bereits zu None gemacht hat.
    for name in money_fields:
        value = row.get(name)
        if value not in (None, ""):
            try:
                row[name] = round(float(value), 2)
            except (TypeError, ValueError):
                pass
    return row


def create_purchase(fields, items=None):
    row = _round_money(
        _blank_numeric_to_none(
            {name: fields[name] for name in PURCHASE_FIELDS if name in fields},
            PURCHASE_NUMERIC_FIELDS,
        ),
        PURCHASE_MONEY_FIELDS,
    )
    response = get_client().table("purchases").insert(row).execute()
    purchase = response.data[0]
    inserted_items = []
    try:
        for item_fields in items or []:
            inserted_items.append(add_purchase_item(purchase["id"], item_fields))
    except Exception:
        # Alles-oder-nichts: bereits eingefuegte Items und den Kauf selbst
        # wieder entfernen, statt einen halb verknuepften Kauf zurueckzulassen -
        # nicht nur bei CardAlreadyLinkedError, sondern bei jedem Fehler
        # waehrend der Item-Verknuepfung (z.B. ein nicht existierender
        # card_id, der die FK-Constraint auf purchase_items verletzt).
        for inserted in inserted_items:
            get_client().table("purchase_items").delete().eq("id", inserted["id"]).execute()
        get_client().table("purchases").delete().eq("id", purchase["id"]).execute()
        raise
    if inserted_items:
        _recompute_allocated_costs(purchase["id"])
        inserted_items = _list_purchase_items(purchase["id"])
    purchase["items"] = inserted_items
    return purchase


def list_purchases(q=None):
    query = get_client().table("purchases").select("*")
    if q:
        query = query.or_(_ilike_search_filter(q, ["platform", "seller", "notes"]))
    response = query.order("purchase_date", desc=True).execute()
    purchases = response.data
    if not purchases:
        return purchases
    ids = [p["id"] for p in purchases]
    items_response = get_client().table("purchase_items").select("purchase_id").in_("purchase_id", ids).execute()
    counts = Counter(item["purchase_id"] for item in items_response.data)
    for p in purchases:
        p["item_count"] = counts.get(p["id"], 0)
    return purchases


def _list_purchase_items(purchase_id):
    response = get_client().table("purchase_items").select("*").eq("purchase_id", purchase_id).execute()
    return response.data


def get_purchase(purchase_id):
    response = get_client().table("purchases").select("*").eq("id", purchase_id).execute()
    if not response.data:
        return None
    purchase = response.data[0]
    purchase["items"] = _list_purchase_items(purchase_id)
    return purchase


def update_purchase(purchase_id, fields):
    row = _round_money(
        _blank_numeric_to_none(
            {name: value for name, value in fields.items() if name in PURCHASE_FIELDS},
            PURCHASE_NUMERIC_FIELDS,
        ),
        PURCHASE_MONEY_FIELDS,
    )
    if not row:
        return get_purchase(purchase_id)
    response = get_client().table("purchases").update(row).eq("id", purchase_id).execute()
    if not response.data:
        return None
    purchase = response.data[0]
    # Kaufpreis/Versand geaendert -> die gleichmaessige Aufteilung auf die
    # verknuepften Karten muss neu gerechnet werden, sonst bliebe sie auf
    # dem alten Gesamtpreis stehen.
    if "total_price" in row or "shipping" in row:
        _recompute_allocated_costs(purchase_id)
    purchase["items"] = _list_purchase_items(purchase_id)
    return purchase


def delete_purchase(purchase_id):
    response = get_client().table("purchases").select("id").eq("id", purchase_id).execute()
    if not response.data:
        return None
    get_client().table("purchases").delete().eq("id", purchase_id).execute()
    return response.data[0]


def add_purchase_item(purchase_id, fields):
    card_id = fields.get("card_id")
    existing = get_client().table("purchase_items").select("id").eq("card_id", card_id).execute()
    if existing.data:
        raise CardAlreadyLinkedError(card_id)
    row = _round_money(
        _blank_numeric_to_none(
            {name: fields.get(name, default) for name, default in PURCHASE_ITEM_DEFAULTS.items()},
            PURCHASE_ITEM_NUMERIC_FIELDS,
        ),
        PURCHASE_ITEM_MONEY_FIELDS,
    )
    row.update({"purchase_id": purchase_id, "card_id": card_id})
    response = get_client().table("purchase_items").insert(row).execute()
    return response.data[0] if response.data else None


def update_purchase_item(purchase_id, item_id, fields):
    row = _round_money(
        _blank_numeric_to_none(
            {name: value for name, value in fields.items() if name in PURCHASE_ITEM_DEFAULTS},
            PURCHASE_ITEM_NUMERIC_FIELDS,
        ),
        PURCHASE_ITEM_MONEY_FIELDS,
    )
    query = get_client().table("purchase_items")
    if not row:
        response = query.select("*").eq("id", item_id).eq("purchase_id", purchase_id).execute()
    else:
        response = query.update(row).eq("id", item_id).eq("purchase_id", purchase_id).execute()
    return response.data[0] if response.data else None


def delete_purchase_item(purchase_id, item_id):
    query = get_client().table("purchase_items")
    response = query.select("id").eq("id", item_id).eq("purchase_id", purchase_id).execute()
    if not response.data:
        return None
    get_client().table("purchase_items").delete().eq("id", item_id).execute()
    return response.data[0]


def _recompute_allocated_costs(purchase_id):
    # Even split of (total_price + shipping) across every card currently
    # linked to this purchase - the default answer to "how is the price
    # split", since the UI never asks the seller for a per-card price when
    # linking. A manual edit made afterward via update_purchase_item()
    # survives until the next such recompute (a card added/removed, or the
    # purchase's own price edited) - a deliberate trade-off, not a bug.
    purchase_response = get_client().table("purchases").select("total_price,shipping").eq("id", purchase_id).execute()
    if not purchase_response.data:
        return
    purchase = purchase_response.data[0]
    total = float(purchase.get("total_price") or 0) + float(purchase.get("shipping") or 0)
    items = get_client().table("purchase_items").select("id").eq("purchase_id", purchase_id).execute().data
    if not items:
        return
    share = round(total / len(items), 2)
    for item in items:
        get_client().table("purchase_items").update({"allocated_cost": share}).eq("id", item["id"]).execute()


def recompute_purchase_item_costs(purchase_id):
    _recompute_allocated_costs(purchase_id)
    return _list_purchase_items(purchase_id)


def get_purchase_for_card(card_id):
    items_response = get_client().table("purchase_items").select("*").eq("card_id", card_id).execute()
    if not items_response.data:
        return None
    item = items_response.data[0]
    purchases_response = get_client().table("purchases").select("*").eq("id", item["purchase_id"]).execute()
    if not purchases_response.data:
        return None
    purchase = purchases_response.data[0]
    return {
        "purchase_id": purchase["id"],
        "item_id": item["id"],
        "purchase_date": purchase.get("purchase_date", ""),
        "platform": purchase.get("platform", ""),
        "seller": purchase.get("seller", ""),
        "allocated_cost": item.get("allocated_cost", 0),
        "quantity": item.get("quantity", 1),
        "notes": item.get("notes", ""),
    }


def cards_with_purchase(card_ids):
    if not card_ids:
        return set()
    response = get_client().table("purchase_items").select("card_id").in_("card_id", card_ids).execute()
    return {row["card_id"] for row in response.data}


def ebay_info_by_card_id(card_ids):
    if not card_ids:
        return {}
    response = (
        get_client().table("ebay_listings").select("card_id,status,sku,price")
        .in_("card_id", card_ids).execute()
    )
    return {
        row["card_id"]: {"status": row["status"], "sku": row["sku"], "price": row["price"]}
        for row in response.data
    }


def purchase_cost_by_card_id(card_ids):
    # Bulk-lookup companion to get_purchase_for_card() - used where the cost
    # basis of many cards is needed at once (e.g. inventory valuation)
    # without a purchase_items round trip per card.
    if not card_ids:
        return {}
    response = (
        get_client().table("purchase_items").select("card_id,allocated_cost")
        .in_("card_id", card_ids).execute()
    )
    return {row["card_id"]: row["allocated_cost"] for row in response.data}


def get_cards_by_ids(card_ids):
    if not card_ids:
        return []
    response = get_client().table("cards").select("id,title,front_image_path").in_("id", card_ids).execute()
    return response.data


EBAY_LISTING_FIELDS = [
    "title", "description", "condition", "condition_id",
    "listing_type", "category_id", "aspects", "price", "quantity",
    "grader", "grade",
]
EBAY_LISTING_WRITABLE_STATUS_FIELDS = {
    "status", "scheduled_at", "scheduling_mode",
    "ebay_offer_id", "ebay_listing_id", "last_error", "published_at",
}
EBAY_LISTING_NUMERIC_FIELDS = {"price", "quantity"}
EBAY_LISTING_MONEY_FIELDS = {"price"}


def create_ebay_listing(card_id, sku, fields):
    row = _round_money(
        _blank_numeric_to_none(
            {name: fields[name] for name in EBAY_LISTING_FIELDS if name in fields},
            EBAY_LISTING_NUMERIC_FIELDS,
        ),
        EBAY_LISTING_MONEY_FIELDS,
    )
    row.update({"card_id": card_id, "sku": sku})
    response = get_client().table("ebay_listings").insert(row).execute()
    return response.data[0]


def get_ebay_listing(listing_id):
    response = get_client().table("ebay_listings").select("*").eq("id", listing_id).execute()
    return response.data[0] if response.data else None


def get_ebay_listing_for_card(card_id):
    response = get_client().table("ebay_listings").select("*").eq("card_id", card_id).execute()
    return response.data[0] if response.data else None


def list_ebay_listings(status=None, q=None):
    query = get_client().table("ebay_listings").select("*")
    if status:
        query = query.eq("status", status)
    if q:
        query = query.or_(_ilike_search_filter(q, ["title", "sku"]))
    response = query.order("updated_at", desc=True).execute()
    return response.data


def update_ebay_listing(listing_id, fields):
    allowed = set(EBAY_LISTING_FIELDS) | EBAY_LISTING_WRITABLE_STATUS_FIELDS
    row = _round_money(
        _blank_numeric_to_none(
            {name: value for name, value in fields.items() if name in allowed},
            EBAY_LISTING_NUMERIC_FIELDS,
        ),
        EBAY_LISTING_MONEY_FIELDS,
    )
    if not row:
        return get_ebay_listing(listing_id)
    response = get_client().table("ebay_listings").update(row).eq("id", listing_id).execute()
    return response.data[0] if response.data else None


def delete_ebay_listing(listing_id):
    response = get_client().table("ebay_listings").select("id").eq("id", listing_id).execute()
    if not response.data:
        return None
    get_client().table("ebay_listings").delete().eq("id", listing_id).execute()
    return response.data[0]


def list_due_scheduled_listings(scheduling_mode):
    # Client-seitig statt per PostgREST-Zeitvergleich gefiltert - gleiches
    # Muster wie der Rest des Projekts, das komplexe PostgREST-Filter meidet.
    from datetime import datetime, timezone

    response = (
        get_client().table("ebay_listings").select("*")
        .eq("status", "Geplant").eq("scheduling_mode", scheduling_mode).execute()
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    return [row for row in response.data if row.get("scheduled_at") and row["scheduled_at"] <= now_iso]


def list_native_scheduled_listings():
    response = (
        get_client().table("ebay_listings").select("*")
        .eq("status", "Geplant").eq("scheduling_mode", "native").execute()
    )
    return response.data


def latest_sale_sync_cursor():
    response = (
        get_client().table("ebay_sales").select("created_at")
        .order("created_at", desc=True).limit(1).execute()
    )
    return response.data[0]["created_at"] if response.data else None


def upsert_ebay_sale(fields):
    response = (
        get_client().table("ebay_sales")
        .upsert(fields, on_conflict="ebay_order_id,ebay_line_item_id")
        .execute()
    )
    return response.data[0]


EBAY_SALE_WRITABLE_FIELDS = {"shipping_cost", "ebay_fees"}
EBAY_SALE_MONEY_FIELDS = {"shipping_cost", "ebay_fees"}


def update_ebay_sale(sale_id, fields):
    # shipping_cost and ebay_fees are the only user-editable fields on
    # ebay_sales - everything else comes from the eBay order sync
    # (sync_ebay_sales()). eBay's Order API (used for that sync) doesn't
    # report the marketplace fee itself (that lives in the separate
    # Finances API, not integrated here), so it's manually entered for now.
    row = _round_money(
        _blank_numeric_to_none(
            {name: value for name, value in fields.items() if name in EBAY_SALE_WRITABLE_FIELDS},
            EBAY_SALE_WRITABLE_FIELDS,
        ),
        EBAY_SALE_MONEY_FIELDS,
    )
    if not row:
        response = get_client().table("ebay_sales").select("*").eq("id", sale_id).execute()
        return response.data[0] if response.data else None
    response = get_client().table("ebay_sales").update(row).eq("id", sale_id).execute()
    return response.data[0] if response.data else None


def get_sale_for_card(card_id):
    response = (
        get_client().table("ebay_sales").select("*")
        .eq("card_id", card_id).order("sale_date", desc=True).limit(1).execute()
    )
    return response.data[0] if response.data else None


def sales_by_listing_id(listing_ids):
    # Bulk companion to get_sale_for_card() - one query for a whole table
    # page (e.g. ebay.html's listing overview) instead of one per row.
    # Keeps only the most recent sale per listing_id (order by sale_date
    # desc + first-write-wins via setdefault), same pattern as
    # ebay_info_by_card_id()/cards_with_purchase() above.
    if not listing_ids:
        return {}
    response = (
        get_client().table("ebay_sales").select("listing_id,sale_date,gross_price")
        .in_("listing_id", listing_ids).order("sale_date", desc=True).execute()
    )
    result = {}
    for row in response.data:
        result.setdefault(row["listing_id"], {"sale_date": row["sale_date"], "gross_price": row["gross_price"]})
    return result


GOOGLE_SHEETS_SETTINGS_FIELDS = {"refresh_token", "spreadsheet_id", "connected_at", "last_synced_at"}


def get_google_sheets_settings():
    response = get_client().table("google_sheets_settings").select("*").execute()
    return response.data[0] if response.data else None


def save_google_sheets_settings(fields):
    # Singleton-row pattern (id=true, enforced by the check(id) column
    # constraint) - upsert always targets the same row instead of ever
    # needing a lookup-then-update.
    row = {name: value for name, value in fields.items() if name in GOOGLE_SHEETS_SETTINGS_FIELDS}
    row["id"] = True
    response = get_client().table("google_sheets_settings").upsert(row).execute()
    return response.data[0]


DASHBOARD_GOAL_FIELDS = {"metric", "amount"}


def get_dashboard_goal(year):
    response = get_client().table("dashboard_goals").select("*").eq("year", year).execute()
    return response.data[0] if response.data else None


def set_dashboard_goal(year, fields):
    # Upsert auf year (Primary Key) - ein Ziel pro Jahr, siehe schema.sql.
    row = {name: value for name, value in fields.items() if name in DASHBOARD_GOAL_FIELDS}
    row["year"] = year
    response = get_client().table("dashboard_goals").upsert(row, on_conflict="year").execute()
    return response.data[0]


def all_scan_batches():
    return get_client().table("scan_batches").select("*").execute().data


def all_cards():
    return get_client().table("cards").select("*").execute().data


def all_purchases():
    return get_client().table("purchases").select("*").execute().data


def all_purchase_items():
    return get_client().table("purchase_items").select("*").execute().data


def all_ebay_listings():
    return get_client().table("ebay_listings").select("*").execute().data


def all_ebay_sales():
    return get_client().table("ebay_sales").select("*").execute().data


def all_inventory():
    return get_client().table("inventory").select("*").execute().data


INVENTORY_FIELDS = ["quantity", "condition", "location", "notes"]
INVENTORY_NUMERIC_FIELDS = {"quantity"}


def create_inventory_item(card_id, fields):
    row = _blank_numeric_to_none(
        {name: fields[name] for name in INVENTORY_FIELDS if name in fields},
        INVENTORY_NUMERIC_FIELDS,
    )
    row["card_id"] = card_id
    response = get_client().table("inventory").insert(row).execute()
    return response.data[0]


def list_inventory():
    response = get_client().table("inventory").select("*").order("created_at").execute()
    return response.data


def get_inventory_for_card(card_id):
    response = (
        get_client().table("inventory").select("*")
        .eq("card_id", card_id).order("created_at").execute()
    )
    return response.data


def get_inventory_item(item_id):
    response = get_client().table("inventory").select("*").eq("id", item_id).execute()
    return response.data[0] if response.data else None


def update_inventory_item(item_id, fields):
    row = _blank_numeric_to_none(
        {name: value for name, value in fields.items() if name in INVENTORY_FIELDS},
        INVENTORY_NUMERIC_FIELDS,
    )
    if not row:
        return get_inventory_item(item_id)
    response = get_client().table("inventory").update(row).eq("id", item_id).execute()
    return response.data[0] if response.data else None


def delete_inventory_item(item_id):
    response = get_client().table("inventory").select("id").eq("id", item_id).execute()
    if not response.data:
        return None
    get_client().table("inventory").delete().eq("id", item_id).execute()
    return response.data[0]


def zero_inventory_for_card(card_id):
    # Called when a card sells on eBay - the physical stock for it drops to
    # 0 across all of its inventory rows (a card can have more than one,
    # e.g. tracked per location) rather than being decremented, since the
    # webapp only ever lists one eBay quantity per card today.
    response = (
        get_client().table("inventory").update({"quantity": 0})
        .eq("card_id", card_id).execute()
    )
    return response.data


def all_price_research():
    return get_client().table("price_research").select("*").execute().data


PRICE_RESEARCH_FIELDS = ["price", "note", "checked_at"]
PRICE_RESEARCH_NUMERIC_FIELDS = {"price"}
PRICE_RESEARCH_MONEY_FIELDS = {"price"}


def create_price_research_entry(card_id, fields):
    row = _round_money(
        _blank_numeric_to_none(
            {name: fields[name] for name in PRICE_RESEARCH_FIELDS if name in fields},
            PRICE_RESEARCH_NUMERIC_FIELDS,
        ),
        PRICE_RESEARCH_MONEY_FIELDS,
    )
    row["card_id"] = card_id
    response = get_client().table("price_research").insert(row).execute()
    return response.data[0]


def list_price_research_for_card(card_id):
    response = (
        get_client().table("price_research").select("*")
        .eq("card_id", card_id).order("checked_at").execute()
    )
    return response.data


def delete_price_research_entry(entry_id):
    response = get_client().table("price_research").select("id").eq("id", entry_id).execute()
    if not response.data:
        return None
    get_client().table("price_research").delete().eq("id", entry_id).execute()
    return response.data[0]


def statistics_rows():
    # One row per card, joining in its purchase cost (if any) and its most
    # recent eBay sale (if any) - python-side joins across three tables,
    # same style as _expand_purchase_items()/_expand_inventory_items() in
    # main.py, kept here since it's pure data assembly with no business
    # logic (profit/margin/holding-days math lives in the /api/statistics
    # endpoint instead).
    cards = get_client().table("cards").select("id,title,card_no,team,set_name").execute().data
    if not cards:
        return []
    card_ids = [c["id"] for c in cards]

    items_response = (
        get_client().table("purchase_items").select("card_id,purchase_id,allocated_cost")
        .in_("card_id", card_ids).execute()
    )
    items_by_card = {row["card_id"]: row for row in items_response.data}
    purchase_ids = [row["purchase_id"] for row in items_response.data]
    purchases_by_id = {}
    if purchase_ids:
        purchases_response = (
            get_client().table("purchases").select("id,purchase_date")
            .in_("id", purchase_ids).execute()
        )
        purchases_by_id = {row["id"]: row for row in purchases_response.data}

    sales_response = (
        get_client().table("ebay_sales")
        .select("card_id,sale_date,gross_price,shipping_charged,shipping_cost,ebay_fees")
        .in_("card_id", card_ids).order("sale_date", desc=True).execute()
    )
    sales_by_card = {}
    for row in sales_response.data:
        sales_by_card.setdefault(row["card_id"], row)

    ebay_info = ebay_info_by_card_id(card_ids)

    rows = []
    for card in cards:
        item = items_by_card.get(card["id"])
        purchase = purchases_by_id.get(item["purchase_id"]) if item else None
        sale = sales_by_card.get(card["id"])
        rows.append({
            "card_id": card["id"],
            "title": card.get("title", ""),
            "card_no": card.get("card_no"),
            "team": card.get("team", ""),
            "set_name": card.get("set_name", ""),
            "sku": (ebay_info.get(card["id"]) or {}).get("sku"),
            "purchase_date": purchase.get("purchase_date") if purchase else None,
            "cost": item.get("allocated_cost") if item else None,
            "sale_date": sale.get("sale_date") if sale else None,
            "sale_price": sale.get("gross_price") if sale else None,
            "shipping_charged": sale.get("shipping_charged") if sale else None,
            "shipping_cost": sale.get("shipping_cost") if sale else None,
            "ebay_fees": sale.get("ebay_fees") if sale else None,
        })
    return rows
