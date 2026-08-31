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
        query = query.or_(_ilike_search_filter(q, ["title", "team", "set_name", "card_number"]))
    if status:
        query = query.eq("recognition_status", status)
    response = query.order("created_at", desc=True).execute()
    return response.data


def get_card(card_id):
    response = get_client().table("cards").select("*").eq("id", card_id).execute()
    return response.data[0] if response.data else None


def update_card(card_id, fields):
    row = {
        name: value for name, value in fields.items()
        if name in CARD_FIELDS or name == "recognition_status"
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
PURCHASE_ITEM_DEFAULTS = {"allocated_cost": 0, "quantity": 1, "notes": ""}
PURCHASE_ITEM_NUMERIC_FIELDS = {"allocated_cost", "quantity"}


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


def create_purchase(fields, items=None):
    row = _blank_numeric_to_none(
        {name: fields[name] for name in PURCHASE_FIELDS if name in fields},
        PURCHASE_NUMERIC_FIELDS,
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
    row = _blank_numeric_to_none(
        {name: value for name, value in fields.items() if name in PURCHASE_FIELDS},
        PURCHASE_NUMERIC_FIELDS,
    )
    if not row:
        return get_purchase(purchase_id)
    response = get_client().table("purchases").update(row).eq("id", purchase_id).execute()
    if not response.data:
        return None
    purchase = response.data[0]
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
    row = _blank_numeric_to_none(
        {name: fields.get(name, default) for name, default in PURCHASE_ITEM_DEFAULTS.items()},
        PURCHASE_ITEM_NUMERIC_FIELDS,
    )
    row.update({"purchase_id": purchase_id, "card_id": card_id})
    response = get_client().table("purchase_items").insert(row).execute()
    return response.data[0] if response.data else None


def update_purchase_item(purchase_id, item_id, fields):
    row = _blank_numeric_to_none(
        {name: value for name, value in fields.items() if name in PURCHASE_ITEM_DEFAULTS},
        PURCHASE_ITEM_NUMERIC_FIELDS,
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


def create_ebay_listing(card_id, sku, fields):
    row = _blank_numeric_to_none(
        {name: fields[name] for name in EBAY_LISTING_FIELDS if name in fields},
        EBAY_LISTING_NUMERIC_FIELDS,
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
        query = query.or_(_ilike_search_filter(q, ["title"]))
    response = query.order("updated_at", desc=True).execute()
    return response.data


def update_ebay_listing(listing_id, fields):
    allowed = set(EBAY_LISTING_FIELDS) | EBAY_LISTING_WRITABLE_STATUS_FIELDS
    row = _blank_numeric_to_none(
        {name: value for name, value in fields.items() if name in allowed},
        EBAY_LISTING_NUMERIC_FIELDS,
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
