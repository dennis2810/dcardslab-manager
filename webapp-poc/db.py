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


def list_cards(q=None, status=None):
    query = get_client().table("cards").select("*")
    if q:
        safe_q = q.replace(",", " ").replace("(", " ").replace(")", " ")
        pattern = f"%{safe_q}%"
        query = query.or_(
            f"title.ilike.{pattern},team.ilike.{pattern},"
            f"set_name.ilike.{pattern},card_number.ilike.{pattern}"
        )
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
PURCHASE_ITEM_DEFAULTS = {"allocated_cost": 0, "quantity": 1, "notes": ""}


class CardAlreadyLinkedError(Exception):
    """Raised by add_purchase_item() when card_id bereits eine
    purchase_items-Zeile hat (unique(card_id) im Schema). Die Karten-ID
    steht in exc.args[0]."""


def create_purchase(fields, items=None):
    row = {name: fields[name] for name in PURCHASE_FIELDS if name in fields}
    response = get_client().table("purchases").insert(row).execute()
    purchase = response.data[0]
    inserted_items = []
    try:
        for item_fields in items or []:
            inserted_items.append(add_purchase_item(purchase["id"], item_fields))
    except CardAlreadyLinkedError:
        # Alles-oder-nichts: bereits eingefuegte Items und den Kauf selbst
        # wieder entfernen, statt einen halb verknuepften Kauf zurueckzulassen.
        for inserted in inserted_items:
            get_client().table("purchase_items").delete().eq("id", inserted["id"]).execute()
        get_client().table("purchases").delete().eq("id", purchase["id"]).execute()
        raise
    purchase["items"] = inserted_items
    return purchase


def list_purchases(q=None):
    query = get_client().table("purchases").select("*")
    if q:
        safe_q = q.replace(",", " ").replace("(", " ").replace(")", " ")
        pattern = f"%{safe_q}%"
        query = query.or_(
            f"platform.ilike.{pattern},seller.ilike.{pattern},notes.ilike.{pattern}"
        )
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
    row = {name: value for name, value in fields.items() if name in PURCHASE_FIELDS}
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
    row = {name: fields.get(name, default) for name, default in PURCHASE_ITEM_DEFAULTS.items()}
    row.update({"purchase_id": purchase_id, "card_id": card_id})
    response = get_client().table("purchase_items").insert(row).execute()
    return response.data[0] if response.data else None


def update_purchase_item(purchase_id, item_id, fields):
    row = {name: value for name, value in fields.items() if name in PURCHASE_ITEM_DEFAULTS}
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
