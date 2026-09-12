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


def insert_card(batch_id, position_in_batch, fields, front_image_path, back_image_path, front_image_hash=None):
    row = {name: fields.get(name, "") for name in CARD_FIELDS}
    row.update({
        "batch_id": batch_id,
        "position_in_batch": position_in_batch,
        "is_numbered": bool(fields.get("is_numbered")),
        "confidence": fields.get("confidence"),
        "recognition_status": fields.get("status", ""),
        "front_image_path": front_image_path,
        "back_image_path": back_image_path,
        # Perzeptueller Bild-Hash (dHash, siehe image_hash.py) des
        # Vorderseitenfotos - fuer die Foto-basierte Duplikat-Erkennung
        # (find_duplicate_card_by_image_hash()) zusaetzlich zum bestehenden
        # Text-Abgleich (find_duplicate_card()).
        "front_image_hash": front_image_hash,
        # Direkt beim Scannen/manuellen Anlegen fuer die private Sammlung
        # markierbar (index.html/card-new.html) - main.py setzt zusaetzlich
        # die Inventar-Menge auf 0 statt 1 fuer solche Karten.
        "private_collection": bool(fields.get("private_collection")),
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


def list_private_collection_cards(q=None):
    # Fuer die neue Seite private-collection.html - Karten, die per
    # Privatentnahme aus dem Verkaufsbestand genommen wurden (siehe
    # set_card_private_collection()). Gleiche Suchspalten wie list_cards().
    query = get_client().table("cards").select("*").eq("private_collection", True)
    if q:
        query = query.or_(_ilike_search_filter(q, ["title", "team", "set_name", "card_number", "season_year", "tags"]))
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


def find_duplicate_card_by_image_hash(image_hash_value, exclude_card_id=None, max_distance=8):
    # Foto-basierte Ergaenzung zu find_duplicate_card() oben - faengt
    # Duplikate ab, bei denen Titel/Set/Kartennummer durch einen OCR-/KI-
    # Erkennungsfehler nicht exakt uebereinstimmen, das Foto aber (nahezu)
    # dasselbe ist. Client-seitiger Distanzvergleich statt SQL, da Supabase/
    # PostgREST keinen Hamming-Distanz-Operator anbietet - fuer eine
    # ueberschaubare Sammlung (kein Massen-Retail-Bestand) unproblematisch.
    if not image_hash_value:
        return None
    from image_hash import hamming_distance

    query = get_client().table("cards").select("id,title,set_name,card_number,card_no,front_image_hash")
    if exclude_card_id:
        query = query.neq("id", exclude_card_id)
    response = query.execute()

    best = None
    best_distance = None
    for row in response.data:
        row_hash = row.get("front_image_hash")
        if not row_hash:
            continue
        distance = hamming_distance(image_hash_value, row_hash)
        if distance <= max_distance and (best_distance is None or distance < best_distance):
            best, best_distance = row, distance
    return best


def get_card(card_id):
    response = get_client().table("cards").select("*").eq("id", card_id).execute()
    return response.data[0] if response.data else None


def update_card(card_id, fields):
    row = {
        name: value for name, value in fields.items()
        if name in CARD_FIELDS or name in ("recognition_status", "shipped", "picked_up", "tags")
    }
    if not row:
        return get_card(card_id)
    response = get_client().table("cards").update(row).eq("id", card_id).execute()
    return response.data[0] if response.data else None


def set_card_private_collection(card_id, value):
    # Privatentnahme: Karte wird aus dem Verkaufsbestand entnommen bzw.
    # zurueckgeholt. Passt den Inventar-Bestand entsprechend an - gleiches
    # Bestandsmuster wie beim Anlegen/Loeschen eines manuellen Verkaufs
    # (zero_inventory_for_card()/restore_inventory_for_card()). Ein
    # eventuell noch aktives eBay-Angebot bleibt bewusst unangetastet -
    # main.py/ebay.html zeigen dafuer nur einen Hinweis (siehe
    # _expand_ebay_listings()), gleich wie bei einem anderweitigen Verkauf.
    response = get_client().table("cards").update({"private_collection": value}).eq("id", card_id).execute()
    if not response.data:
        return None
    if value:
        zero_inventory_for_card(card_id)
    else:
        restore_inventory_for_card(card_id)
    return response.data[0]


def set_card_image_path(card_id, side, object_path):
    """Writes front_image_path/back_image_path directly, bypassing
    update_card()'s CARD_FIELDS whitelist - these are server-managed
    Storage-object references (like purchases.receipt_path), not
    user-editable recognition fields."""
    column = "front_image_path" if side == "front" else "back_image_path"
    response = get_client().table("cards").update({column: object_path}).eq("id", card_id).execute()
    return response.data[0] if response.data else None


def delete_card(card_id):
    card = get_card(card_id)
    if card is None:
        return None
    get_client().table("cards").delete().eq("id", card_id).execute()
    return card


def _split_extra_image_paths(card):
    return [p for p in (card.get("extra_image_paths") or "").split(",") if p]


def add_card_extra_image(card_id, object_path):
    """Appends one more photo beyond front/back (e.g. a close-up of a
    defect) - same comma-separated freetext storage as cards.tags rather
    than a dedicated table, see schema.sql."""
    card = get_card(card_id)
    if card is None:
        return None
    paths = _split_extra_image_paths(card) + [object_path]
    response = (
        get_client().table("cards").update({"extra_image_paths": ",".join(paths)})
        .eq("id", card_id).execute()
    )
    return response.data[0] if response.data else None


def remove_card_extra_image(card_id, index):
    """Removes the extra photo at `index` (0-based, in upload order).
    Returns (updated_card, removed_object_path) so the caller (see main.py)
    can also delete the object from Storage; (None, None) if the card or
    index doesn't exist."""
    card = get_card(card_id)
    if card is None:
        return None, None
    paths = _split_extra_image_paths(card)
    if index < 0 or index >= len(paths):
        return None, None
    removed_path = paths.pop(index)
    response = (
        get_client().table("cards").update({"extra_image_paths": ",".join(paths)})
        .eq("id", card_id).execute()
    )
    updated = response.data[0] if response.data else None
    return updated, removed_path


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


def set_purchase_receipt(purchase_id, receipt_path):
    """Writes receipt_path directly, bypassing PURCHASE_FIELDS - that
    whitelist is for the user-editable form fields on purchase.html, not
    this server-managed Storage-object reference."""
    response = (
        get_client().table("purchases").update({"receipt_path": receipt_path}).eq("id", purchase_id).execute()
    )
    if not response.data:
        return None
    purchase = response.data[0]
    purchase["items"] = _list_purchase_items(purchase_id)
    return purchase


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
        get_client().table("ebay_listings")
        .select("card_id,status,sku,price,ebay_listing_id,ebay_offer_id,last_auto_relisted_at,listing_since")
        .in_("card_id", card_ids).execute()
    )
    return {
        row["card_id"]: {
            "status": row["status"], "sku": row["sku"], "price": row["price"],
            "ebay_listing_id": row.get("ebay_listing_id"), "ebay_offer_id": row.get("ebay_offer_id"),
            "last_auto_relisted_at": row.get("last_auto_relisted_at"), "listing_since": row.get("listing_since"),
        }
        for row in response.data
    }


def manual_sale_info_by_card_id(card_ids):
    # Bulk-Companion zu ebay_info_by_card_id() - inventory.html/cards.html
    # brauchen den Verkaufskanal pro Karte, um Verkaeufe ausserhalb von eBay
    # genauso als "verkauft" zu erkennen wie einen eBay-Verkauf.
    if not card_ids:
        return {}
    response = (
        get_client().table("manual_sales").select("card_id,channel")
        .in_("card_id", card_ids).execute()
    )
    return {row["card_id"]: {"channel": row["channel"]} for row in response.data}


def sale_flags_by_card_id(card_ids):
    # Bulk-Companion zu ebay_info_by_card_id()/manual_sale_info_by_card_id() -
    # "Zugestellt"/"Retoure" leben auf ebay_sales bzw. manual_sales, nicht auf
    # cards, werden aber in der Kartenuebersicht (cards.html) als Badges
    # gebraucht. Eine Karte hat hoechstens einen der beiden Verkaufstypen.
    if not card_ids:
        return {}
    result = {}
    ebay_response = (
        get_client().table("ebay_sales").select("card_id,delivered,refunded")
        .in_("card_id", card_ids).execute()
    )
    for row in ebay_response.data:
        result[row["card_id"]] = {"delivered": bool(row.get("delivered")), "refunded": bool(row.get("refunded"))}
    manual_response = (
        get_client().table("manual_sales").select("card_id,delivered,refunded")
        .in_("card_id", card_ids).execute()
    )
    for row in manual_response.data:
        result.setdefault(
            row["card_id"], {"delivered": bool(row.get("delivered")), "refunded": bool(row.get("refunded"))},
        )
    return result


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
    response = (
        get_client().table("cards").select("id,title,front_image_path,private_collection")
        .in_("id", card_ids).execute()
    )
    return response.data


EBAY_LISTING_FIELDS = [
    "title", "description", "condition", "condition_id",
    "listing_type", "category_id", "aspects", "price", "quantity",
    "grader", "grade", "auto_relist_after_days",
]
EBAY_LISTING_WRITABLE_STATUS_FIELDS = {
    "status", "scheduled_at", "scheduling_mode",
    "ebay_offer_id", "ebay_listing_id", "last_error", "published_at",
    "last_auto_relisted_at", "listing_since", "last_known_views",
}
EBAY_LISTING_NUMERIC_FIELDS = {"price", "quantity", "auto_relist_after_days"}
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


def list_listings_due_for_price_research(limit=3, max_age_days=7):
    # Client-seitig gefiltert, gleiches Muster wie list_due_scheduled_listings() -
    # ein nie geprueftes Angebot (last_price_research_at leer) hat Vorrang vor
    # laengst faelligen; begrenzt auf `limit` pro Aufruf, damit
    # ebay_scheduler.run_price_research_once() eBays Application-Token-
    # Tageslimit fuer die Buy/Browse-Suche nicht sprengt.
    from datetime import datetime, timedelta, timezone

    response = (
        get_client().table("ebay_listings").select("*")
        .eq("status", "Veroeffentlicht").execute()
    )
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    due = [
        row for row in response.data
        if not row.get("last_price_research_at") or row["last_price_research_at"] <= cutoff_iso
    ]
    due.sort(key=lambda row: row.get("last_price_research_at") or "")
    return due[:limit]


def mark_price_research_checked(listing_id, when_iso):
    get_client().table("ebay_listings").update({"last_price_research_at": when_iso}).eq("id", listing_id).execute()


def list_listings_due_for_auto_relist():
    # Client-seitig gefiltert, gleiches Muster wie list_listings_due_for_price_research()
    # oben. Nur Angebote mit gesetztem per-Angebot-Schalter (auto_relist_after_days,
    # siehe Klaerung mit dem Nutzer: "Schalter + Markierung" statt eines globalen
    # Alles-oder-nichts-Schalters) UND einem echten ebay_offer_id (importierte,
    # extern verwaltete Angebote koennen nicht per Inventory API beendet/neu
    # veroeffentlicht werden, siehe _is_externally_managed() in main.py).
    # last_auto_relisted_at faellt auf published_at zurueck, wenn das Angebot
    # noch nie automatisch neu eingestellt wurde.
    from datetime import datetime, timedelta, timezone

    response = (
        get_client().table("ebay_listings").select("*")
        .eq("status", "Veroeffentlicht").execute()
    )
    now = datetime.now(timezone.utc)
    due = []
    for row in response.data:
        days = row.get("auto_relist_after_days")
        if not days or days <= 0 or not row.get("ebay_offer_id"):
            continue
        reference = row.get("last_auto_relisted_at") or row.get("published_at")
        if not reference:
            continue
        try:
            reference_dt = datetime.fromisoformat(str(reference).replace("Z", "+00:00"))
        except ValueError:
            continue
        if reference_dt + timedelta(days=days) <= now:
            due.append(row)
    return due


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


EBAY_SALE_WRITABLE_FIELDS = {
    "shipping_cost", "ebay_fees", "refunded", "delivered", "tracking_number", "shipping_carrier",
}
EBAY_SALE_MONEY_FIELDS = {"shipping_cost", "ebay_fees"}


def update_ebay_sale(sale_id, fields):
    # shipping_cost, ebay_fees and refunded are the only user-editable
    # fields on ebay_sales - everything else comes from the eBay order sync
    # (sync_ebay_sales()). eBay's Order API (used for that sync) doesn't
    # report the marketplace fee itself (that lives in the separate
    # Finances API, not integrated here), so it's manually entered for now.
    # refunded is a plain boolean, not numeric - passing it through
    # _blank_numeric_to_none()/_round_money() below is a no-op for it
    # (neither ever matches a bool value), so no special-casing needed.
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


def get_ebay_sale(sale_id):
    response = get_client().table("ebay_sales").select("*").eq("id", sale_id).execute()
    return response.data[0] if response.data else None


def set_ebay_sale_buyer_username(sale_id, username):
    # Eigene Funktion statt update_ebay_sale() (dessen EBAY_SALE_WRITABLE_FIELDS
    # bewusst nur die manuell editierbaren Felder erlaubt) - buyer_username
    # kommt sonst nur aus sync_ebay_sales(), hier zusaetzlich als Backfill
    # fuer bereits vor diesem Feld synchronisierte Verkaeufe (siehe
    # GET /api/ebay/sales/{id}/shipping-address).
    get_client().table("ebay_sales").update({"buyer_username": username}).eq("id", sale_id).execute()


def get_ebay_sale_by_order_id(order_id):
    # Fuer den automatischen Retouren-Sync (ebay_scheduler.run_returns_sync_once())
    # - eBay meldet eine Rueckerstattung je Bestellung, nicht je Angebot; der
    # bereits synchronisierte Verkauf wird darueber gefunden, um refunded
    # automatisch zu setzen.
    response = get_client().table("ebay_sales").select("*").eq("ebay_order_id", order_id).execute()
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
        get_client().table("ebay_sales")
        .select("id,listing_id,sale_date,gross_price,tracking_number,shipping_carrier,delivered,refunded")
        .in_("listing_id", listing_ids).order("sale_date", desc=True).execute()
    )
    result = {}
    for row in response.data:
        result.setdefault(row["listing_id"], {
            "id": row["id"], "sale_date": row["sale_date"], "gross_price": row["gross_price"],
            "tracking_number": row.get("tracking_number"), "shipping_carrier": row.get("shipping_carrier"),
            "delivered": bool(row.get("delivered")), "refunded": bool(row.get("refunded")),
        })
    return result


def search_sales(q):
    """Fuer die globale Suche (search.html): sucht Verkaeufe nach eBay-
    Kaeufername (ebay_sales.buyer_username) sowie Kanal (manual_sales.
    channel) und reichert die Treffer mit dem Kartentitel an. Kein
    gemeinsamer Index noetig bei der Groessenordnung dieses Tools - ein
    einfacher ilike-Abgleich pro Tabelle reicht."""
    safe_q = q.replace(",", " ").replace("(", " ").replace(")", " ")
    pattern = f"%{safe_q}%"
    ebay_response = (
        get_client().table("ebay_sales").select("card_id,sale_date,gross_price,buyer_username")
        .ilike("buyer_username", pattern).execute()
    )
    manual_response = (
        get_client().table("manual_sales").select("card_id,sale_date,gross_price,channel")
        .ilike("channel", pattern).execute()
    )
    card_ids = {row["card_id"] for row in ebay_response.data} | {row["card_id"] for row in manual_response.data}
    titles = {}
    if card_ids:
        cards_response = get_client().table("cards").select("id,title").in_("id", list(card_ids)).execute()
        titles = {row["id"]: row["title"] for row in cards_response.data}
    results = []
    for row in ebay_response.data:
        results.append({
            "card_id": row["card_id"], "title": titles.get(row["card_id"], ""),
            "channel": "eBay", "sale_date": row.get("sale_date"),
            "gross_price": row.get("gross_price"), "buyer_username": row.get("buyer_username"),
        })
    for row in manual_response.data:
        results.append({
            "card_id": row["card_id"], "title": titles.get(row["card_id"], ""),
            "channel": row.get("channel"), "sale_date": row.get("sale_date"),
            "gross_price": row.get("gross_price"), "buyer_username": None,
        })
    return results


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


def get_app_status():
    response = get_client().table("app_status").select("*").execute()
    return response.data[0] if response.data else None


def record_backup_downloaded(downloaded_at):
    # Gleiches Singleton-Row-Muster wie save_google_sheets_settings().
    response = get_client().table("app_status").upsert({
        "id": True, "last_backup_at": downloaded_at,
    }).execute()
    return response.data[0]


def set_low_stock_threshold(threshold):
    # Gleiches Singleton-Row-Muster wie record_backup_downloaded().
    response = get_client().table("app_status").upsert({
        "id": True, "low_stock_threshold": threshold,
    }).execute()
    return response.data[0]


def set_price_alert_threshold(pct):
    # Gleiches Singleton-Row-Muster wie set_low_stock_threshold() - ersetzt
    # die zuvor fest verdrahteten 20% in card.html/dashboard.html/ebay.html.
    response = get_client().table("app_status").upsert({
        "id": True, "price_alert_threshold_pct": pct,
    }).execute()
    return response.data[0]


def set_page_size(size):
    # Gleiches Singleton-Row-Muster wie set_low_stock_threshold() - ersetzt
    # die zuvor pro Seite fest verdrahteten 40 Zeilen.
    response = get_client().table("app_status").upsert({
        "id": True, "page_size": size,
    }).execute()
    return response.data[0]


def set_view_density(density):
    # Gleiches Singleton-Row-Muster wie set_page_size() - "comfort" (Standard)
    # oder "compact" fuer dichtere Tabellenzeilen.
    response = get_client().table("app_status").upsert({
        "id": True, "view_density": density,
    }).execute()
    return response.data[0]


def set_csv_delimiter(delimiter):
    # Gleiches Singleton-Row-Muster wie set_view_density().
    response = get_client().table("app_status").upsert({
        "id": True, "csv_delimiter": delimiter,
    }).execute()
    return response.data[0]


def set_reminder_thresholds(stale_listing_min_days, stale_listing_check_days, stale_wishlist_min_days):
    # Gleiches Singleton-Row-Muster wie set_view_density() - alle drei
    # Schwellwerte werden gemeinsam im selben Formular bearbeitet/gespeichert.
    response = get_client().table("app_status").upsert({
        "id": True,
        "stale_listing_min_days": stale_listing_min_days,
        "stale_listing_check_days": stale_listing_check_days,
        "stale_wishlist_min_days": stale_wishlist_min_days,
    }).execute()
    return response.data[0]


def set_kleinunternehmer_thresholds(prev_year_threshold, current_year_threshold):
    # Gleiches Singleton-Row-Muster wie set_reminder_thresholds().
    response = get_client().table("app_status").upsert({
        "id": True,
        "kleinunternehmer_prev_year_threshold": prev_year_threshold,
        "kleinunternehmer_current_year_threshold": current_year_threshold,
    }).execute()
    return response.data[0]


def set_kleinunternehmer_hint_enabled(enabled):
    # Gleiches Singleton-Row-Muster wie set_view_density().
    response = get_client().table("app_status").upsert({
        "id": True, "kleinunternehmer_hint_enabled": enabled,
    }).execute()
    return response.data[0]


def record_failed_login(ip, at):
    # Rein informativ fuer settings.html ("Login"-Abschnitt) - die eigentliche
    # Bruteforce-Drossel in main.py's /api/login laeuft komplett in-memory
    # und ist davon unabhaengig. Liste auf die letzten 20 Eintraege gedeckelt,
    # neueste zuerst, gleiches Singleton-Row-Muster wie set_low_stock_threshold().
    status = get_app_status() or {}
    log = [{"at": at, "ip": ip}] + list(status.get("failed_login_log") or [])[:19]
    response = get_client().table("app_status").upsert({
        "id": True, "failed_login_log": log,
    }).execute()
    return response.data[0]


def set_auto_relist_enabled(enabled):
    # Globaler An/Aus-Schalter fuer das automatische Re-Listing - zusaetzlich
    # zum per-Angebot-Schalter (ebay_listings.auto_relist_after_days, siehe
    # list_listings_due_for_auto_relist()). Beides muss zutreffen, damit ein
    # Angebot tatsaechlich automatisch neu eingestellt wird (Klaerung mit dem
    # Nutzer: "Schalter + Markierung").
    response = get_client().table("app_status").upsert({
        "id": True, "auto_relist_enabled": enabled,
    }).execute()
    return response.data[0]


def set_sender_address(address):
    # Gleiches Singleton-Row-Muster wie set_low_stock_threshold().
    response = get_client().table("app_status").upsert({
        "id": True, "sender_address": address,
    }).execute()
    return response.data[0]


def record_auto_backup(uploaded_at):
    # Eigenes Feld statt last_backup_at (manueller Download) - ein
    # automatischer Hintergrund-Upload und ein bewusster manueller Download
    # sind unterschiedliche Ereignisse, die getrennt sichtbar bleiben sollen.
    response = get_client().table("app_status").upsert({
        "id": True, "last_auto_backup_at": uploaded_at,
    }).execute()
    return response.data[0]


NOTIFICATION_SETTINGS_FIELDS = {
    "smtp_host", "smtp_port", "smtp_username", "smtp_password",
    "smtp_from", "smtp_to", "smtp_use_tls", "notify_on_sale",
    "notify_on_reminders",
}


def save_notification_settings(fields):
    # Gleiches Singleton-Row-Muster + Feld-Whitelist wie
    # save_google_sheets_settings() - ein Frontend-Tippfehler im Feldnamen
    # legt so keine falsche Spalte an/ueberschreibt keine falsche. smtp_password
    # fehlt im Payload, wenn das Formular es leer laesst (siehe main.py's
    # update_notification_settings()) - Postgres' upsert aendert dann nur die
    # tatsaechlich mitgeschickten Spalten, das gespeicherte Passwort bleibt
    # also erhalten statt versehentlich geloescht zu werden.
    row = {name: value for name, value in fields.items() if name in NOTIFICATION_SETTINGS_FIELDS}
    row["id"] = True
    response = get_client().table("app_status").upsert(row).execute()
    return response.data[0]


def record_sales_sync(synced_at):
    # Gleiches Singleton-Row-Muster wie record_backup_downloaded() -
    # merkt sich, wann der periodische Hintergrund-Verkaufs-Sync
    # (ebay_scheduler.run_sales_sync_once()) zuletzt gelaufen ist, damit er
    # nur alle SALES_SYNC_INTERVAL_MINUTES statt bei jedem 5-Minuten-Takt
    # tatsaechlich synchronisiert.
    response = get_client().table("app_status").upsert({
        "id": True, "last_sales_sync_at": synced_at,
    }).execute()
    return response.data[0]


def record_portfolio_snapshot(snapshot_date, total_value, card_count):
    # Kein Singleton-Row-Muster wie die app_status-Setter oben - hier soll
    # bewusst ein Verlauf ueber die Zeit entstehen (Portfolio-Wertverlauf,
    # siehe statistics.html), daher insert() statt upsert().
    response = get_client().table("portfolio_value_snapshots").insert({
        "snapshot_date": snapshot_date, "total_value": total_value, "card_count": card_count,
    }).execute()
    return response.data[0]


def list_portfolio_snapshots():
    response = (
        get_client().table("portfolio_value_snapshots").select("*")
        .order("snapshot_date").execute()
    )
    return response.data


def latest_portfolio_snapshot():
    response = (
        get_client().table("portfolio_value_snapshots").select("*")
        .order("snapshot_date", desc=True).limit(1).execute()
    )
    return response.data[0] if response.data else None


def record_returns_sync(synced_at):
    # Gleiches Singleton-Row-Muster wie record_sales_sync() - eigenes Feld
    # statt last_sales_sync_at, da Verkaufs- und Retouren-Sync unabhaengig
    # voneinander laufen/fehlschlagen koennen.
    response = get_client().table("app_status").upsert({
        "id": True, "last_returns_sync_at": synced_at,
    }).execute()
    return response.data[0]


def set_activity_cleared(cleared_at):
    # Gleiches Singleton-Row-Muster wie record_auto_backup() - "Letzte
    # Aktivitaet" auf dem Dashboard wird live aus Kaeufen/Verkaeufen/Karten
    # berechnet (kein gespeichertes Protokoll), daher merkt sich dieser
    # Zeitpunkt nur, ab wann wieder Ereignisse angezeigt werden sollen.
    response = get_client().table("app_status").upsert({
        "id": True, "activity_cleared_at": cleared_at,
    }).execute()
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


def restore_inventory_for_card(card_id):
    # Gegenstueck zu zero_inventory_for_card() - wird aufgerufen, wenn ein
    # manueller Verkauf wieder geloescht wird (z.B. versehentlich erfasst).
    # Menge 1 als Rueckfall, da der urspruengliche Bestand beim Verkaufen
    # nicht mitgeschrieben wurde - deckt den ueblichen Fall (eine Karte) ab.
    response = (
        get_client().table("inventory").update({"quantity": 1})
        .eq("card_id", card_id).execute()
    )
    return response.data


MANUAL_SALE_FIELDS = [
    "channel", "sale_date", "gross_price", "shipping_charged", "shipping_cost", "fees", "refunded", "delivered",
    "notes", "tracking_number", "shipping_carrier",
]
MANUAL_SALE_NUMERIC_FIELDS = {"gross_price", "shipping_charged", "shipping_cost", "fees"}
MANUAL_SALE_MONEY_FIELDS = {"gross_price", "shipping_charged", "shipping_cost", "fees"}


def create_manual_sale(card_id, fields):
    # Ein Verkauf ausserhalb von eBay (Kleinanzeigen, Vinted, privat, ...) -
    # eigenstaendige Tabelle statt ebay_sales, da kein ebay_listings-Eintrag
    # existiert. Genau ein manueller Verkauf pro Karte (unique card_id in
    # der Migration), daher hier ein reiner Insert statt Upsert - ein
    # zweiter Versuch fuer dieselbe Karte soll sichtbar mit einem
    # Constraint-Fehler scheitern statt den ersten still zu ueberschreiben.
    row = _round_money(
        _blank_numeric_to_none(
            {name: fields[name] for name in MANUAL_SALE_FIELDS if name in fields},
            MANUAL_SALE_NUMERIC_FIELDS,
        ),
        MANUAL_SALE_MONEY_FIELDS,
    )
    row["card_id"] = card_id
    response = get_client().table("manual_sales").insert(row).execute()
    return response.data[0]


def get_manual_sale_for_card(card_id):
    response = get_client().table("manual_sales").select("*").eq("card_id", card_id).execute()
    return response.data[0] if response.data else None


def get_manual_sale(sale_id):
    response = get_client().table("manual_sales").select("*").eq("id", sale_id).execute()
    return response.data[0] if response.data else None


def all_manual_sales():
    return get_client().table("manual_sales").select("*").execute().data


def update_manual_sale(sale_id, fields):
    row = _round_money(
        _blank_numeric_to_none(
            {name: fields[name] for name in MANUAL_SALE_FIELDS if name in fields},
            MANUAL_SALE_NUMERIC_FIELDS,
        ),
        MANUAL_SALE_MONEY_FIELDS,
    )
    if not row:
        response = get_client().table("manual_sales").select("*").eq("id", sale_id).execute()
        return response.data[0] if response.data else None
    response = get_client().table("manual_sales").update(row).eq("id", sale_id).execute()
    return response.data[0] if response.data else None


def set_manual_sale_receipt(sale_id, receipt_path):
    """Gleiches Prinzip wie set_purchase_receipt() - schreibt receipt_path
    direkt, unter Umgehung von MANUAL_SALE_FIELDS (das ist fuer die
    Formularfelder auf card.html, nicht diese serverseitig verwaltete
    Storage-Objekt-Referenz)."""
    response = (
        get_client().table("manual_sales").update({"receipt_path": receipt_path}).eq("id", sale_id).execute()
    )
    return response.data[0] if response.data else None


def delete_manual_sale(sale_id):
    # card_id wird mitselektiert, damit der Aufrufer (siehe main.py's
    # DELETE /api/manual-sales/{id}) danach das Inventar der Karte wieder
    # herstellen kann.
    response = get_client().table("manual_sales").select("id,card_id").eq("id", sale_id).execute()
    if not response.data:
        return None
    get_client().table("manual_sales").delete().eq("id", sale_id).execute()
    return response.data[0]


WISHLIST_FIELDS = ["title", "team", "set_name", "target_price", "notes"]
WISHLIST_NUMERIC_FIELDS = {"target_price"}
WISHLIST_MONEY_FIELDS = {"target_price"}


def list_wishlist_items(q=None):
    query = get_client().table("wishlist_items").select("*")
    if q:
        query = query.or_(_ilike_search_filter(q, ["title", "team", "set_name", "notes"]))
    response = query.order("created_at", desc=True).execute()
    return response.data


def create_wishlist_item(fields):
    row = _round_money(
        _blank_numeric_to_none(
            {name: fields[name] for name in WISHLIST_FIELDS if name in fields},
            WISHLIST_NUMERIC_FIELDS,
        ),
        WISHLIST_MONEY_FIELDS,
    )
    response = get_client().table("wishlist_items").insert(row).execute()
    return response.data[0]


def update_wishlist_item(item_id, fields):
    row = _round_money(
        _blank_numeric_to_none(
            {name: fields[name] for name in WISHLIST_FIELDS if name in fields},
            WISHLIST_NUMERIC_FIELDS,
        ),
        WISHLIST_MONEY_FIELDS,
    )
    if not row:
        response = get_client().table("wishlist_items").select("*").eq("id", item_id).execute()
        return response.data[0] if response.data else None
    response = get_client().table("wishlist_items").update(row).eq("id", item_id).execute()
    return response.data[0] if response.data else None


def list_wishlist_items_due_for_price_check(limit=3, max_age_days=1):
    # Sourcing-Liste: gleiches Muster wie list_listings_due_for_price_research()
    # fuer bereits veroeffentlichte eigene Angebote - ein nie geprueftes
    # Eintrag hat Vorrang vor laengst faelligen. Kuerzeres Zeitfenster als
    # dort (1 statt 7 Tage), da ein guenstiges Kaufangebot schnell weg sein
    # kann - Kauf-Entscheidungen sind zeitkritischer als reine
    # Marktpreis-Beobachtung.
    from datetime import datetime, timedelta, timezone

    response = get_client().table("wishlist_items").select("*").execute()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
    due = [
        row for row in response.data
        if not row.get("last_price_check_at") or row["last_price_check_at"] <= cutoff_iso
    ]
    due.sort(key=lambda row: row.get("last_price_check_at") or "")
    return due[:limit]


def update_wishlist_price_check(item_id, fields):
    get_client().table("wishlist_items").update(fields).eq("id", item_id).execute()


def record_wishlist_price_check(item_id, price, checked_at):
    # Verlauf fuer die Sparkline auf wishlist.html (analog zu price_research
    # bei Karten) - wird nur bei einem tatsaechlichen Treffer aufgerufen
    # (siehe ebay_scheduler.run_wishlist_price_check_once()), damit die Linie
    # nicht durch erfolglose Pruefungen verrauscht wird.
    get_client().table("wishlist_price_checks").insert({
        "item_id": item_id, "price": price, "checked_at": checked_at,
    }).execute()


def wishlist_price_history_by_item_id(item_ids):
    # Bulk-Companion analog zu sale_flags_by_card_id() - eine Abfrage fuer
    # die ganze Wunschliste statt einer pro Eintrag beim Laden von
    # GET /api/wishlist.
    if not item_ids:
        return {}
    response = (
        get_client().table("wishlist_price_checks").select("item_id,price,checked_at")
        .in_("item_id", item_ids).order("checked_at").execute()
    )
    result = {}
    for row in response.data:
        result.setdefault(row["item_id"], []).append({"price": row["price"], "checked_at": row["checked_at"]})
    return result


def delete_wishlist_item(item_id):
    response = get_client().table("wishlist_items").select("id").eq("id", item_id).execute()
    if not response.data:
        return None
    get_client().table("wishlist_items").delete().eq("id", item_id).execute()
    return response.data[0]


DESCRIPTION_TEMPLATE_FIELDS = ["name", "body"]


def list_description_templates():
    response = get_client().table("description_templates").select("*").order("created_at", desc=True).execute()
    return response.data


def create_description_template(fields):
    row = {name: fields[name] for name in DESCRIPTION_TEMPLATE_FIELDS if name in fields}
    response = get_client().table("description_templates").insert(row).execute()
    return response.data[0]


def update_description_template(template_id, fields):
    row = {name: fields[name] for name in DESCRIPTION_TEMPLATE_FIELDS if name in fields}
    if not row:
        response = get_client().table("description_templates").select("*").eq("id", template_id).execute()
        return response.data[0] if response.data else None
    response = get_client().table("description_templates").update(row).eq("id", template_id).execute()
    return response.data[0] if response.data else None


def delete_description_template(template_id):
    response = get_client().table("description_templates").select("id").eq("id", template_id).execute()
    if not response.data:
        return None
    get_client().table("description_templates").delete().eq("id", template_id).execute()
    return response.data[0]


def all_price_research():
    return get_client().table("price_research").select("*").execute().data


# Reine Backup-Helfer (siehe backup.py's _TABLE_NAMES/build_backup_zip(), das
# ueber getattr(db, f"all_{table}")() dynamisch aufruft) - bewusst keine
# Zugangsdaten-Tabellen wie google_sheets_settings (refresh_token) oder
# app_status (u.a. smtp_password), damit ein heruntergeladenes/in Supabase
# Storage abgelegtes Backup keine Secrets im Klartext enthaelt.
def all_wishlist_items():
    return get_client().table("wishlist_items").select("*").execute().data


def all_wishlist_price_checks():
    return get_client().table("wishlist_price_checks").select("*").execute().data


def all_description_templates():
    return get_client().table("description_templates").select("*").execute().data


def all_portfolio_value_snapshots():
    return get_client().table("portfolio_value_snapshots").select("*").execute().data


def all_dashboard_goals():
    return get_client().table("dashboard_goals").select("*").execute().data


def bulk_upsert_rows(table_name, rows):
    """Fuer backup.py's restore_backup_zip(): schreibt eine Liste bereits
    vollstaendiger Zeilen (aus einem frueheren build_backup_zip()-Export,
    jede mit eigener id) in einer PostgREST-Anfrage zurueck - vorhandene
    IDs werden aktualisiert, neue eingefuegt, nichts wird geloescht
    (bewusster Merge statt Wipe-and-Replace, siehe Klaerung mit dem Nutzer).
    table_name kommt ausschliesslich aus backup.py's fester _TABLE_NAMES-
    Liste, nie aus Nutzereingabe."""
    if not rows:
        return
    get_client().table(table_name).upsert(rows).execute()


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


def price_research_by_card_ids(card_ids):
    # Bulk-Companion zu list_price_research_for_card() - ebay.html braucht
    # Durchschnitt/Anzahl je Karte fuer die Preisrecherche-Statusspalte, ohne
    # eine Abfrage pro Zeile abzusetzen (gleiches Muster wie
    # manual_sale_info_by_card_id()).
    if not card_ids:
        return {}
    response = (
        get_client().table("price_research").select("card_id,price")
        .in_("card_id", card_ids).execute()
    )
    prices_by_card = {}
    for row in response.data:
        prices_by_card.setdefault(row["card_id"], []).append(row["price"])
    return {
        card_id: {"avg_price": sum(prices) / len(prices), "count": len(prices)}
        for card_id, prices in prices_by_card.items()
    }


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
    # Karten in der privaten Sammlung (Privatentnahme) zaehlen nicht mehr
    # zum Verkaufsbestand - siehe set_card_private_collection() - und
    # bleiben deshalb aus Statistiken/Dashboard-Kennzahlen aussen vor.
    cards = (
        get_client().table("cards").select("id,title,card_no,team,set_name")
        .eq("private_collection", False).execute().data
    )
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
            get_client().table("purchases").select("id,purchase_date,platform")
            .in_("id", purchase_ids).execute()
        )
        purchases_by_id = {row["id"]: row for row in purchases_response.data}

    sales_response = (
        get_client().table("ebay_sales")
        .select("card_id,sale_date,gross_price,shipping_charged,shipping_cost,ebay_fees,refunded,buyer_username")
        .in_("card_id", card_ids).order("sale_date", desc=True).execute()
    )
    sales_by_card = {}
    for row in sales_response.data:
        sales_by_card.setdefault(row["card_id"], row)

    # Verkaeufe ausserhalb von eBay (Kleinanzeigen, Vinted, privat, ...) -
    # eine Karte hat entweder einen eBay-Verkauf ODER einen manuellen
    # Verkauf, nie beide (siehe manual_sales' unique card_id) - eBay wird
    # bevorzugt, falls trotzdem mal beide vorlaegen (der eingerichtete Weg).
    manual_sales_response = (
        get_client().table("manual_sales")
        .select("card_id,channel,sale_date,gross_price,shipping_charged,shipping_cost,fees,refunded")
        .in_("card_id", card_ids).execute()
    )
    manual_sales_by_card = {row["card_id"]: row for row in manual_sales_response.data}

    ebay_info = ebay_info_by_card_id(card_ids)

    rows = []
    for card in cards:
        item = items_by_card.get(card["id"])
        purchase = purchases_by_id.get(item["purchase_id"]) if item else None
        sale = sales_by_card.get(card["id"])
        manual_sale = manual_sales_by_card.get(card["id"])
        channel = None
        if sale:
            channel = "eBay"
        elif manual_sale:
            channel = manual_sale.get("channel") or "Sonstiges"
        rows.append({
            "card_id": card["id"],
            "title": card.get("title", ""),
            "card_no": card.get("card_no"),
            "team": card.get("team", ""),
            "set_name": card.get("set_name", ""),
            "platform": purchase.get("platform", "") if purchase else "",
            "sku": (ebay_info.get(card["id"]) or {}).get("sku"),
            # Fuer den "Auf eBay ansehen"-Link in der Kaeufer-Historie
            # (statistics-sales.html) - nur bei veroeffentlichten/verkauften
            # eBay-Angeboten gesetzt.
            "ebay_listing_id": (ebay_info.get(card["id"]) or {}).get("ebay_listing_id"),
            "purchase_date": purchase.get("purchase_date") if purchase else None,
            "cost": item.get("allocated_cost") if item else None,
            "channel": channel,
            "sale_date": (sale or manual_sale or {}).get("sale_date") if (sale or manual_sale) else None,
            "sale_price": sale.get("gross_price") if sale else (manual_sale.get("gross_price") if manual_sale else None),
            "shipping_charged": sale.get("shipping_charged") if sale else (manual_sale.get("shipping_charged") if manual_sale else None),
            "shipping_cost": sale.get("shipping_cost") if sale else (manual_sale.get("shipping_cost") if manual_sale else None),
            "ebay_fees": sale.get("ebay_fees") if sale else (manual_sale.get("fees") if manual_sale else None),
            "refunded": bool((sale or manual_sale or {}).get("refunded")) if (sale or manual_sale) else False,
            # Nur bei eBay-Verkaeufen vorhanden (pseudonymer Handle, siehe
            # sync_ebay_sales()) - fuer die Kaeufer-Historie auf
            # statistics-sales.html.
            "buyer_username": sale.get("buyer_username") if sale else None,
        })
    return rows


def save_push_subscription(endpoint, p256dh, auth):
    # Upsert-by-endpoint (unique(endpoint) im Schema) - ein wiederholtes
    # Abo desselben Geraets/Browsers (z. B. nach Cache-Loeschen liefert der
    # Browser denselben Endpoint erneut) ueberschreibt nur die Schluessel,
    # statt einen Duplikat-Eintrag anzulegen.
    response = get_client().table("push_subscriptions").upsert({
        "endpoint": endpoint, "p256dh": p256dh, "auth": auth,
    }, on_conflict="endpoint").execute()
    return response.data[0]


def all_push_subscriptions():
    return get_client().table("push_subscriptions").select("*").execute().data


def delete_push_subscription(endpoint):
    get_client().table("push_subscriptions").delete().eq("endpoint", endpoint).execute()


def create_reminder(card_id, note, due_date):
    response = get_client().table("reminders").insert({
        "card_id": card_id, "note": note, "due_date": due_date,
    }).execute()
    return response.data[0]


def list_reminders_for_card(card_id):
    response = (
        get_client().table("reminders").select("*")
        .eq("card_id", card_id).order("due_date").execute()
    )
    return response.data


def list_due_reminders():
    # Faellige, noch offene Erinnerungen (Dashboard "Was jetzt tun?" +
    # E-Mail-Digest, siehe main.py's _send_reminder_digest_if_due()) -
    # due_date <= heute (reine Datums-, keine Zeitstempel-Spalte).
    from datetime import date

    today_iso = date.today().isoformat()
    response = (
        get_client().table("reminders").select("*")
        .is_("resolved_at", "null").lte("due_date", today_iso)
        .order("due_date").execute()
    )
    return response.data


def resolve_reminder(reminder_id):
    from datetime import datetime, timezone

    response = (
        get_client().table("reminders")
        .update({"resolved_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", reminder_id).execute()
    )
    return response.data[0] if response.data else None


def delete_reminder(reminder_id):
    get_client().table("reminders").delete().eq("id", reminder_id).execute()


def list_stale_unsold_listings(min_days_listed=90, stale_check_days=30):
    # Wiedervorlage-Regel 1 (Klaerung mit dem Nutzer): "Karte seit N Tagen
    # unverkauft, aber keine aktuelle Preisrecherche vorhanden oder
    # veraltet" - client-seitig gefiltert, gleiches Prinzip wie
    # list_listings_due_for_price_research().
    from datetime import datetime, timedelta, timezone

    response = (
        get_client().table("ebay_listings").select("*")
        .eq("status", "Veroeffentlicht").execute()
    )
    now_iso = datetime.now(timezone.utc).isoformat()
    listed_cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=min_days_listed)).isoformat()
    check_cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=stale_check_days)).isoformat()
    stale = []
    for row in response.data:
        since = row.get("listing_since")
        if not since or since > listed_cutoff_iso:
            continue
        last_check = row.get("last_price_research_at")
        if last_check and last_check > check_cutoff_iso:
            continue
        stale.append(row)
    return stale


def list_stale_wishlist_items(min_days=60):
    # Wiedervorlage-Regel 2: Wunschlisten-Eintrag lange angelegt, ohne dass
    # der Zielpreis je erreicht wurde. Kein voller Preisverlauf gespeichert -
    # Annaeherung ueber den zuletzt gefundenen Treffer (last_match_price):
    # noch nie einer gefunden, oder der zuletzt gefundene liegt ueber dem
    # Zielpreis.
    from datetime import datetime, timedelta, timezone

    response = get_client().table("wishlist_items").select("*").execute()
    cutoff_iso = (datetime.now(timezone.utc) - timedelta(days=min_days)).isoformat()
    stale = []
    for row in response.data:
        if row.get("created_at", "") > cutoff_iso:
            continue
        target = row.get("target_price")
        if target is None:
            continue
        match = row.get("last_match_price")
        if match is not None and match <= target:
            continue
        stale.append(row)
    return stale


def record_reminder_email_sent(sent_at):
    # Gleiches Singleton-Row-Muster wie record_auto_backup() - drosselt den
    # E-Mail-Digest auf einmal pro Tag (main.py's _is_reminder_digest_due()).
    response = get_client().table("app_status").upsert({
        "id": True, "last_reminder_email_sent_at": sent_at,
    }).execute()
    return response.data[0]
