"""DCardLabs Web PoC - proves the core scan workflow works over HTTP before
any DB/auth/frontend investment.

POST /api/scan takes a front and back 9-up scan image (the same kind of
files you currently drag into the desktop app) and returns the same crop +
Claude-vision recognition result as a JSON list of 9 cards - and persists
every scanned card to Supabase (Postgres for the data, Storage for the
compressed images); GET /api/cards and GET /api/cards/{id} read them back
with freshly signed image URLs. There is still no auth and no eBay
integration - this PoC only answers "does upload -> crop -> AI recognition
-> persistence work cleanly as a web request?", building on the validated
scan workflow before a real frontend is built around it.

Reuses scanner/scanner_v0_8_dynamic.py (the OpenCV 9-up crop) and
integrations/ai_card_recognition.py (Claude vision) unchanged from the
desktop app - both are already plain functions with no Tkinter dependency
in their actual logic, so they run headless on a server as-is.

Run:
    pip install -r webapp-poc/requirements.txt
    export ANTHROPIC_API_KEY=...   # same key as the desktop app
    uvicorn main:app --host 0.0.0.0 --port 8000 --app-dir webapp-poc

Then open http://<nas-tailscale-name>:8000 from any device on your tailnet.
"""
import asyncio
import base64
import sys
import tempfile
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from fastapi import Body, FastAPI, File, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

# scanner_v0_8_dynamic.py imports tkinter at module level for its own
# standalone CLI/GUI harness - process() (the only part we call) never
# touches it. A real Tk install pulled in via a system package tends to
# mismatch the container's Python build (confirmed while testing this PoC),
# so stub it out instead - same approach this repo's test suite already
# uses to import the desktop app headlessly.
for _name in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scanner"))
sys.path.insert(0, str(REPO_ROOT / "integrations"))

import scanner_v0_8_dynamic as scanner  # noqa: E402
from ai_card_recognition import recognize_card, EMPTY_FIELDS  # noqa: E402

import db  # noqa: E402
import ebay_client  # noqa: E402
import ebay_listing  # noqa: E402
import ebay_scheduler  # noqa: E402
import storage  # noqa: E402

app = FastAPI(title="DCardLabs Web PoC")


@app.on_event("startup")
async def _start_ebay_scheduler():
    # publish_fn is _publish_listing() itself, defined further down in this
    # module - a plain lambda closure avoids importing main.py from
    # ebay_scheduler.py (which already imports db/ebay_client, not main).
    asyncio.create_task(ebay_scheduler.run_forever(lambda listing: _publish_listing(listing)))

# Matches the desktop app's defaults (start_dcardlabs.bat / dcardlabs_manager.py).
JPEG_QUALITY = 97
ROTATE = True


def _crop_side(upload_path, out_dir):
    """Same contract as dcardlabs_manager.scan_one(), minus the Tkinter
    status-label updates - crop one 9-up scan into 9 individual card images,
    numbered 001..009 by grid position."""
    files = scanner.process(upload_path, out_dir, JPEG_QUALITY, ROTATE)
    if len(files) != 9:
        raise ValueError(f"Dynamic Grid hat {len(files)} statt 9 Karten erkannt.")
    return [Path(p) for p in files]


@app.post("/api/scan")
async def scan(front: UploadFile = File(...), back: UploadFile = File(...)):
    with tempfile.TemporaryDirectory(prefix="dcardslab_poc_") as tmp_str:
        tmp = Path(tmp_str)
        front_path = tmp / f"front{Path(front.filename or 'front.jpg').suffix}"
        back_path = tmp / f"back{Path(back.filename or 'back.jpg').suffix}"
        front_path.write_bytes(await front.read())
        back_path.write_bytes(await back.read())

        front_dir, back_dir = tmp / "front_cards", tmp / "back_cards"
        front_dir.mkdir()
        back_dir.mkdir()

        try:
            front_files = _crop_side(front_path, front_dir)
            back_files = _crop_side(back_path, back_dir)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        back_map = {int(p.stem): p for p in back_files}
        batch_id = db.create_batch(card_count=len(front_files))

        def process_one(fp):
            # Every branch below must return a result dict and must never let
            # an exception escape - a single card's Supabase hiccup (upload,
            # insert, or signed-URL lookup) may not abort the whole batch
            # (see design spec's Fehlerbehandlung section). Failures are
            # reported via the "image_error" field instead, using an explicit
            # None sentinel (never gated on message truthiness, since
            # str(exc) can be "") and naming which step/side failed.
            number = int(fp.stem)
            bp = back_map.get(number)
            if bp is None:
                fields = dict(EMPTY_FIELDS, status=f"Rückseite für Karte {number:03d} fehlt.")
                try:
                    card_row = db.insert_card(batch_id, number, fields, None, None)
                    return {"number": number, **fields, "id": card_row["id"]}
                except Exception as exc:
                    return {
                        "number": number,
                        **fields,
                        "image_error": f"Datenbank-Insert fehlgeschlagen: {type(exc).__name__}: {exc}",
                    }

            fields = recognize_card(front_path=fp, back_path=bp)

            front_image_path = back_image_path = None
            image_error = None
            try:
                front_image_path = storage.upload_image(batch_id, number, "front", fp)
                back_image_path = storage.upload_image(batch_id, number, "back", bp)
            except Exception as exc:
                stage = "front-Bild-Upload" if front_image_path is None else "Rückseiten-Bild-Upload"
                image_error = f"{stage} fehlgeschlagen: {type(exc).__name__}: {exc}"

            result = {"number": number, **fields}
            try:
                card_row = db.insert_card(batch_id, number, fields, front_image_path, back_image_path)
                result["id"] = card_row["id"]
            except Exception as exc:
                if image_error is None:
                    image_error = f"Datenbank-Insert fehlgeschlagen: {type(exc).__name__}: {exc}"
                result["image_error"] = image_error
                return result

            if front_image_path:
                try:
                    result["front_image_url"] = storage.signed_url(front_image_path)
                except Exception as exc:
                    if image_error is None:
                        image_error = f"Signed-URL (Vorderseite) fehlgeschlagen: {type(exc).__name__}: {exc}"
            if back_image_path:
                try:
                    result["back_image_url"] = storage.signed_url(back_image_path)
                except Exception as exc:
                    if image_error is None:
                        image_error = f"Signed-URL (Rückseite) fehlgeschlagen: {type(exc).__name__}: {exc}"

            if image_error is not None:
                result["image_error"] = image_error
            return result

        # Same pattern as pair_and_ocr() in the desktop app: recognize_card()
        # is a network round-trip, so a handful of cards run concurrently
        # instead of 9 sequential API calls.
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(process_one, front_files))

    # The scan_batches row was created above with status="pending" - it must
    # never be left stuck there. Default to "failed" so that even if the
    # status computation itself blows up unexpectedly, update_batch_status
    # still runs (in finally) with a safe, non-pending value before the
    # exception is allowed to propagate.
    batch_status = "failed"
    try:
        results.sort(key=lambda r: r["number"])
        success_count = sum(
            1 for r in results if r.get("status") == "ok" and "image_error" not in r
        )
        if success_count == len(results):
            batch_status = "ok"
        elif success_count == 0:
            batch_status = "failed"
        else:
            batch_status = "partial"
    finally:
        db.update_batch_status(batch_id, batch_status)

    return JSONResponse({"batch_id": batch_id, "cards": results})


def _expand_purchase_items(items):
    # Reichert jedes purchase_items-Row um eine schlanke Karten-Kurzinfo an
    # (id/title/front_image_url), damit purchase.html/card.html nicht pro
    # Karte einen eigenen Request an /api/cards/{id} schicken muessen.
    if not items:
        return []
    card_ids = [item["card_id"] for item in items]
    cards_by_id = {c["id"]: c for c in db.get_cards_by_ids(card_ids)}
    expanded = []
    for item in items:
        item = dict(item)
        card = cards_by_id.get(item["card_id"], {})
        card_summary = {"id": item["card_id"], "title": card.get("title", "")}
        front_path = card.get("front_image_path")
        if front_path:
            try:
                card_summary["front_image_url"] = storage.signed_url(front_path)
            except Exception:
                pass
        item["card"] = card_summary
        expanded.append(item)
    return expanded


def _attach_purchase_items(purchase):
    purchase = dict(purchase)
    purchase["items"] = _expand_purchase_items(purchase.get("items", []))
    return purchase


def _attach_signed_urls(card):
    # Mirrors process_one()'s pattern in POST /api/scan: each signed_url()
    # call is guarded individually, so a transient Supabase Storage hiccup
    # (or a stored path that no longer resolves) drops only that one URL
    # key instead of raising out of the list comprehension in list_cards()
    # and taking down the whole /api/cards response with a 500.
    card = dict(card)
    front_path = card.get("front_image_path")
    back_path = card.get("back_image_path")
    if front_path:
        try:
            card["front_image_url"] = storage.signed_url(front_path)
        except Exception:
            pass
    if back_path:
        try:
            card["back_image_url"] = storage.signed_url(back_path)
        except Exception:
            pass
    return card


@app.get("/api/cards")
async def list_cards(q: str | None = None, status: str | None = None):
    cards = [_attach_signed_urls(c) for c in db.list_cards(q=q, status=status)]
    linked_ids = db.cards_with_purchase([c["id"] for c in cards])
    for c in cards:
        c["has_purchase"] = c["id"] in linked_ids
    return JSONResponse({"cards": cards})


@app.get("/api/cards/{card_id}")
async def get_card(card_id: str):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    card = _attach_signed_urls(card)
    card["purchase"] = db.get_purchase_for_card(card_id)
    return JSONResponse(card)


@app.patch("/api/cards/{card_id}")
async def update_card(card_id: str, fields: dict = Body(...)):
    updated = db.update_card(card_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    return JSONResponse(_attach_signed_urls(updated))


@app.delete("/api/cards/{card_id}", status_code=204)
async def delete_card(card_id: str):
    deleted = db.delete_card(card_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    paths = [p for p in (deleted.get("front_image_path"), deleted.get("back_image_path")) if p]
    if paths:
        try:
            storage.delete_images(paths)
        except Exception as exc:
            print(f"Bild-Löschung fehlgeschlagen für {paths}: {type(exc).__name__}: {exc}")
    return Response(status_code=204)


@app.post("/api/cards/{card_id}/rotate")
async def rotate_card_image(card_id: str, body: dict = Body(...)):
    side = body.get("side")
    degrees = body.get("degrees")
    if side not in ("front", "back"):
        raise HTTPException(status_code=400, detail="side muss 'front' oder 'back' sein.")
    if degrees not in (90, 180, 270):
        raise HTTPException(status_code=400, detail="degrees muss 90, 180 oder 270 sein.")

    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")

    path_key = "front_image_path" if side == "front" else "back_image_path"
    object_path = card.get(path_key)
    if not object_path:
        raise HTTPException(status_code=404, detail=f"Kein Bild für {side} vorhanden.")

    rotated_bytes = storage.rotate_image(object_path, degrees)

    result = _attach_signed_urls(card)
    # Not relying on a freshly signed URL for the just-rotated side here:
    # Supabase Storage's CDN can serve a stale cached copy of the object for
    # a short time right after rotate_image()'s overwrite, even to a request
    # carrying a brand-new signed-URL token - a prior cache-busting-only
    # frontend fix wasn't reliably enough ahead of it. The rotated bytes are
    # already in memory from rotate_image()'s return value, so they're sent
    # straight back as a data URI - no read-after-write race possible.
    result["rotated_image_data_uri"] = "data:image/jpeg;base64," + base64.b64encode(rotated_bytes).decode("ascii")
    return JSONResponse(result)


@app.post("/api/purchases")
async def create_purchase(fields: dict = Body(...)):
    fields = dict(fields)
    items = fields.pop("items", None)
    for item in items or []:
        card_id = item.get("card_id")
        if not card_id or db.get_card(card_id) is None:
            raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    try:
        purchase = db.create_purchase(fields, items)
    except db.CardAlreadyLinkedError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Karte {exc.args[0]} ist bereits einem Kauf zugeordnet.",
        ) from exc
    return JSONResponse(_attach_purchase_items(purchase))


@app.get("/api/purchases")
async def list_purchases(q: str | None = None):
    return JSONResponse({"purchases": db.list_purchases(q=q)})


@app.get("/api/purchases/{purchase_id}")
async def get_purchase(purchase_id: str):
    purchase = db.get_purchase(purchase_id)
    if purchase is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    return JSONResponse(_attach_purchase_items(purchase))


@app.patch("/api/purchases/{purchase_id}")
async def update_purchase(purchase_id: str, fields: dict = Body(...)):
    updated = db.update_purchase(purchase_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    return JSONResponse(_attach_purchase_items(updated))


@app.delete("/api/purchases/{purchase_id}", status_code=204)
async def delete_purchase(purchase_id: str):
    deleted = db.delete_purchase(purchase_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    return Response(status_code=204)


@app.post("/api/purchases/{purchase_id}/items")
async def add_purchase_item(purchase_id: str, fields: dict = Body(...)):
    if db.get_purchase(purchase_id) is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    card_id = fields.get("card_id")
    if not card_id or db.get_card(card_id) is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    try:
        item = db.add_purchase_item(purchase_id, fields)
    except db.CardAlreadyLinkedError as exc:
        raise HTTPException(
            status_code=409,
            detail=f"Karte {exc.args[0]} ist bereits einem Kauf zugeordnet.",
        ) from exc
    return JSONResponse(_expand_purchase_items([item])[0])


@app.patch("/api/purchases/{purchase_id}/items/{item_id}")
async def update_purchase_item(purchase_id: str, item_id: str, fields: dict = Body(...)):
    updated = db.update_purchase_item(purchase_id, item_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Kauf-Position {item_id} nicht gefunden.")
    return JSONResponse(_expand_purchase_items([updated])[0])


@app.delete("/api/purchases/{purchase_id}/items/{item_id}", status_code=204)
async def delete_purchase_item(purchase_id: str, item_id: str):
    deleted = db.delete_purchase_item(purchase_id, item_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Kauf-Position {item_id} nicht gefunden.")
    return Response(status_code=204)


def _listing_with_card(listing):
    # Enriches an ebay_listings row with a thin card summary
    # (id/title/front_image_url), same purpose as _expand_purchase_items()
    # above - the frontend shouldn't need a second request per listing.
    listing = dict(listing)
    card = db.get_card(listing["card_id"]) or {}
    listing["card"] = {"id": listing["card_id"], "title": card.get("title", "")}
    front_path = card.get("front_image_path")
    if front_path:
        try:
            listing["card"]["front_image_url"] = storage.signed_url(front_path)
        except Exception:
            pass
    return listing


def _publish_listing(listing, scheduled_at=None):
    """Shared publish flow for POST .../publish, publish-bulk, and the
    app-side scheduler (ebay_scheduler.py) - one place for "validate
    required aspects, resolve policies, create/update inventory item +
    offer, publish" so none of the three callers can drift apart."""
    listing_type = listing["listing_type"]
    missing = ebay_listing.missing_aspects(listing.get("aspects") or {}, listing_type)
    if missing:
        raise HTTPException(
            status_code=422, detail="Pflichtfelder fehlen: " + ", ".join(missing)
        )

    if scheduled_at is not None:
        mode = "native" if ebay_client.NATIVE_SCHEDULING_SUPPORTED else "app"
        if mode == "app":
            # App-Fallback legt noch nichts bei eBay an - nur der DB-Zustand
            # aendert sich, ebay_scheduler.py holt das zum Zieltermin nach.
            return db.update_ebay_listing(listing["id"], {
                "status": "Geplant", "scheduled_at": scheduled_at, "scheduling_mode": mode,
            })
        # native: faellt durch auf den normalen Publish-Ablauf unten - das
        # Offer wird JETZT angelegt, nur der eBay-Publish-Call bekommt den
        # Scheduling-Parameter statt sofort live zu gehen (s. Spec).

    try:
        token = ebay_client.get_access_token()
        policies = ebay_client.get_listing_policies(token)
        card = db.get_card(listing["card_id"]) or {}
        image_url = None
        if card.get("front_image_path"):
            image_url = storage.public_url(card["front_image_path"])
        ebay_client.put_inventory_item(token, listing["sku"], card, image_url)

        offer_id = listing.get("ebay_offer_id")
        payload = {**listing, "policies": policies}
        if offer_id:
            ebay_client.update_offer(token, offer_id, payload)
        else:
            offer_id = ebay_client.create_offer(token, listing["sku"], payload)

        native_scheduled_at = (
            scheduled_at if scheduled_at is not None and ebay_client.NATIVE_SCHEDULING_SUPPORTED else None
        )
        ebay_listing_id = ebay_client.publish_offer(token, offer_id, scheduled_at=native_scheduled_at)
    except ebay_client.EbayNotAuthorizedError as exc:
        db.update_ebay_listing(listing["id"], {"status": "Fehler", "last_error": str(exc)})
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ebay_client.EbayApiError as exc:
        db.update_ebay_listing(listing["id"], {"status": "Fehler", "last_error": str(exc)})
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    updates = {
        "ebay_offer_id": offer_id, "ebay_listing_id": ebay_listing_id or "", "last_error": "",
    }
    if scheduled_at is not None:
        updates.update({"status": "Geplant", "scheduled_at": scheduled_at, "scheduling_mode": "native"})
    else:
        updates.update({"status": "Veroeffentlicht", "published_at": datetime.now(timezone.utc).isoformat()})
    return db.update_ebay_listing(listing["id"], updates)


@app.post("/api/cards/{card_id}/ebay-listing")
async def create_ebay_listing(card_id: str, fields: dict = Body(default={})):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    if db.get_ebay_listing_for_card(card_id) is not None:
        raise HTTPException(
            status_code=409, detail="Für diese Karte existiert bereits ein eBay-Angebot."
        )

    listing_type = fields.get("listing_type") or ebay_listing.derive_listing_type(card)
    row = {
        "title": fields.get("title") or ebay_listing.generate_title(card),
        "description": fields.get("description") or ebay_listing.generate_description(card),
        "condition": fields.get("condition", "NM"),
        "condition_id": fields.get("condition_id", "4000"),
        "listing_type": listing_type,
        "category_id": fields.get("category_id") or ebay_listing.CATEGORY_IDS[listing_type],
        "aspects": fields.get("aspects") or ebay_listing.build_aspects(card, listing_type),
        "price": fields.get("price", 0),
        "quantity": fields.get("quantity", 1),
    }
    sku = ebay_listing.sku_for_card(card_id)
    listing = db.create_ebay_listing(card_id, sku, row)
    listing["required_aspects"] = ebay_listing.required_aspects(listing_type)
    return JSONResponse(_listing_with_card(listing))


@app.get("/api/ebay/listings")
async def list_ebay_listings(status: str | None = None, q: str | None = None):
    listings = [_listing_with_card(l) for l in db.list_ebay_listings(status=status, q=q)]
    return JSONResponse({"listings": listings})


@app.get("/api/ebay/listings/{listing_id}")
async def get_ebay_listing(listing_id: str):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    return JSONResponse(_listing_with_card(listing))


@app.patch("/api/ebay/listings/{listing_id}")
async def update_ebay_listing(listing_id: str, fields: dict = Body(...)):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    updated = db.update_ebay_listing(listing_id, fields)
    if updated["status"] == "Veroeffentlicht":
        updated = _publish_listing(updated)
    return JSONResponse(_listing_with_card(updated))


@app.delete("/api/ebay/listings/{listing_id}", status_code=204)
async def delete_ebay_listing(listing_id: str):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    if listing["status"] != "Entwurf":
        raise HTTPException(status_code=409, detail="Nur Entwürfe können gelöscht werden.")
    db.delete_ebay_listing(listing_id)
    return Response(status_code=204)


@app.post("/api/ebay/listings/{listing_id}/publish")
async def publish_ebay_listing(listing_id: str, body: dict = Body(default={})):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    scheduled_at = body.get("scheduled_at")
    if scheduled_at and scheduled_at <= datetime.now(timezone.utc).isoformat():
        scheduled_at = None
    updated = _publish_listing(listing, scheduled_at=scheduled_at)
    return JSONResponse(_listing_with_card(updated))


@app.post("/api/ebay/listings/{listing_id}/unschedule")
async def unschedule_ebay_listing(listing_id: str):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    if listing["status"] != "Geplant":
        raise HTTPException(status_code=409, detail="Nur geplante Angebote können storniert werden.")
    if listing.get("scheduling_mode") == "native" and listing.get("ebay_offer_id"):
        token = ebay_client.get_access_token()
        ebay_client.withdraw_offer(token, listing["ebay_offer_id"])
    updated = db.update_ebay_listing(listing_id, {
        "status": "Entwurf", "scheduled_at": None, "scheduling_mode": "",
    })
    return JSONResponse(_listing_with_card(updated))


@app.post("/api/ebay/listings/publish-bulk")
async def publish_ebay_listings_bulk(body: dict = Body(...)):
    results = []
    for listing_id in body.get("listing_ids", []):
        listing = db.get_ebay_listing(listing_id)
        if listing is None:
            results.append({"listing_id": listing_id, "status": "Fehler", "error": "Nicht gefunden."})
            continue
        try:
            updated = _publish_listing(listing)
            results.append({"listing_id": listing_id, "status": updated["status"]})
        except HTTPException as exc:
            results.append({"listing_id": listing_id, "status": "Fehler", "error": str(exc.detail)})
    return JSONResponse({"results": results})


@app.get("/api/ebay/oauth/status")
async def ebay_oauth_status():
    response = httpx.get(f"{ebay_client.EBAY_OAUTH_SERVER_URL}/api/oauth/status", timeout=15)
    return JSONResponse(response.json(), status_code=response.status_code)


@app.post("/api/ebay/sync-sales")
async def sync_ebay_sales():
    try:
        token = ebay_client.get_access_token()
    except ebay_client.EbayNotAuthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc

    cursor = db.latest_sale_sync_cursor()
    since = cursor or (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    orders = ebay_client.get_orders(token, since)
    listings_by_sku = {l["sku"]: l for l in db.list_ebay_listings()}

    synced = skipped = 0
    for order in orders:
        for line_item in order.get("lineItems", []):
            listing = ebay_listing.match_sale_line_item(line_item, listings_by_sku)
            if listing is None:
                skipped += 1
                continue
            db.upsert_ebay_sale({
                "listing_id": listing["id"], "card_id": listing["card_id"],
                "ebay_order_id": order.get("orderId", ""),
                "ebay_line_item_id": line_item.get("lineItemId", ""),
                "sale_date": order.get("creationDate"),
                "quantity": line_item.get("quantity", 1),
                "gross_price": float((line_item.get("total") or {}).get("value", 0) or 0),
            })
            db.update_ebay_listing(listing["id"], {"status": "Verkauft"})
            synced += 1
    return JSONResponse({"synced": synced, "skipped": skipped})


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
