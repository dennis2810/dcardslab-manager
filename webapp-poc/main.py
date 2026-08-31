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
import json
import logging
import secrets
import sys
import tempfile
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Response, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
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

import backup  # noqa: E402
import db  # noqa: E402
import ebay_client  # noqa: E402
import ebay_listing  # noqa: E402
import ebay_scheduler  # noqa: E402
import google_sheets_client  # noqa: E402
import storage  # noqa: E402

logger = logging.getLogger("ebay_publish")
# Explicit handler+level on this logger itself (not relying on root/uvicorn
# logging config) - uvicorn's default dictConfig only sets up the "uvicorn"
# logger namespace, not root, so a plain logger.info() call here would
# otherwise be silently dropped (Python's logging "handler of last resort"
# only handles WARNING and above).
logger.setLevel(logging.INFO)
if not logger.handlers:
    _handler = logging.StreamHandler()
    _handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(_handler)

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


@app.post("/api/cards/recognize")
async def recognize_card_images(front: UploadFile = File(...), back: UploadFile = File(...)):
    with tempfile.TemporaryDirectory(prefix="dcardslab_manual_") as tmp_str:
        tmp = Path(tmp_str)
        front_path = tmp / f"front{Path(front.filename or 'front.jpg').suffix}"
        back_path = tmp / f"back{Path(back.filename or 'back.jpg').suffix}"
        front_path.write_bytes(await front.read())
        back_path.write_bytes(await back.read())
        fields = recognize_card(front_path=front_path, back_path=back_path)
    return JSONResponse(fields)


@app.post("/api/cards")
async def create_card_manual(
    front: UploadFile = File(...), back: UploadFile = File(...), fields: str = Form("{}"),
):
    try:
        parsed_fields = json.loads(fields)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="fields muss gueltiges JSON sein.") from exc

    with tempfile.TemporaryDirectory(prefix="dcardslab_manual_") as tmp_str:
        tmp = Path(tmp_str)
        front_path = tmp / f"front{Path(front.filename or 'front.jpg').suffix}"
        back_path = tmp / f"back{Path(back.filename or 'back.jpg').suffix}"
        front_path.write_bytes(await front.read())
        back_path.write_bytes(await back.read())

        batch_id = db.create_batch(card_count=1)
        try:
            front_image_path = storage.upload_image(batch_id, 1, "front", front_path)
            back_image_path = storage.upload_image(batch_id, 1, "back", back_path)
            card_row = db.insert_card(batch_id, 1, parsed_fields, front_image_path, back_image_path)
        except Exception as exc:
            # Covers db.insert_card() too, not just the image uploads - a
            # Supabase insert failure here must not leave the batch row
            # stuck at "pending" forever (same trap /api/scan's
            # process_one() already guards against for its own
            # insert_card() call).
            db.update_batch_status(batch_id, "failed")
            raise HTTPException(
                status_code=502, detail=f"Karte anlegen fehlgeschlagen: {type(exc).__name__}: {exc}"
            ) from exc

    db.update_batch_status(batch_id, "ok")
    return JSONResponse(_attach_signed_urls(card_row))


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
    card["ebay_listing"] = db.get_ebay_listing_for_card(card_id)
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


def _expand_ebay_listings(listings):
    # Enriches each ebay_listings row with a thin card summary
    # (id/title/front_image_url), same purpose and batching approach as
    # _expand_purchase_items() above - one db.get_cards_by_ids() call for
    # the whole list instead of a db.get_card() round trip per listing.
    if not listings:
        return []
    card_ids = [l["card_id"] for l in listings]
    cards_by_id = {c["id"]: c for c in db.get_cards_by_ids(card_ids)}
    expanded = []
    for listing in listings:
        listing = dict(listing)
        card = cards_by_id.get(listing["card_id"], {})
        card_summary = {"id": listing["card_id"], "title": card.get("title", "")}
        front_path = card.get("front_image_path")
        if front_path:
            try:
                card_summary["front_image_url"] = storage.signed_url(front_path)
            except Exception:
                pass
        listing["card"] = card_summary
        expanded.append(listing)
    return expanded


def _listing_with_card(listing):
    return _expand_ebay_listings([listing])[0]


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

    # Each step is logged individually (start + ok) so that a failure's
    # traceback - logged via logger.exception() in the except blocks below -
    # lands right after the last "-> ok" line, making the failing step
    # obvious from the container logs alone. Needed because eBay's sandbox
    # reuses one generic errorId (25002) for many distinct causes, and the
    # existing except-blocks below already catch EbayApiError cleanly, so
    # nothing about *which* of the ~5 sequential eBay calls failed was
    # visible in the logs before this.
    step = "start"
    try:
        step = "get_access_token"
        logger.info("Publish %s: %s ...", listing["id"], step)
        token = ebay_client.get_access_token()
        logger.info("Publish %s: %s -> ok", listing["id"], step)

        step = "ensure_merchant_location"
        logger.info("Publish %s: %s ...", listing["id"], step)
        merchant_location_key = ebay_client.ensure_merchant_location(token)
        logger.info("Publish %s: %s -> ok (%s)", listing["id"], step, merchant_location_key)

        step = "get_listing_policies"
        logger.info("Publish %s: %s ...", listing["id"], step)
        policies = ebay_client.get_listing_policies(token)
        logger.info("Publish %s: %s -> ok", listing["id"], step)

        card = db.get_card(listing["card_id"]) or {}
        image_urls = [
            storage.public_url(path)
            for path in (card.get("front_image_path"), card.get("back_image_path"))
            if path
        ]

        step = "put_inventory_item"
        logger.info("Publish %s: %s ...", listing["id"], step)
        ebay_client.put_inventory_item(token, listing["sku"], listing, image_urls)
        logger.info("Publish %s: %s -> ok", listing["id"], step)

        offer_id = listing.get("ebay_offer_id")
        payload = {**listing, "policies": policies, "merchant_location_key": merchant_location_key}
        if offer_id:
            step = "update_offer"
            logger.info("Publish %s: %s (%s) ...", listing["id"], step, offer_id)
            ebay_client.update_offer(token, offer_id, payload)
            logger.info("Publish %s: %s -> ok", listing["id"], step)
        else:
            step = "create_offer"
            logger.info("Publish %s: %s ...", listing["id"], step)
            offer_id = ebay_client.create_offer(token, listing["sku"], payload)
            logger.info("Publish %s: %s -> ok (%s)", listing["id"], step, offer_id)
            # Persisted immediately, not only on full success below - if a
            # later step (e.g. publish_offer) fails, the next retry must
            # call update_offer() with this ID instead of create_offer()
            # again, which eBay rejects with errorId 25002 "Offer entity
            # already exists" once an Offer for this SKU exists there.
            db.update_ebay_listing(listing["id"], {"ebay_offer_id": offer_id})

        native_scheduled_at = (
            scheduled_at if scheduled_at is not None and ebay_client.NATIVE_SCHEDULING_SUPPORTED else None
        )
        step = "publish_offer"
        logger.info("Publish %s: %s (%s) ...", listing["id"], step, offer_id)
        ebay_listing_id = ebay_client.publish_offer(token, offer_id, scheduled_at=native_scheduled_at)
        logger.info("Publish %s: %s -> ok (%s)", listing["id"], step, ebay_listing_id)
    except ebay_client.EbayNotAuthorizedError as exc:
        logger.exception("Publish %s: Schritt '%s' fehlgeschlagen (nicht autorisiert)", listing["id"], step)
        db.update_ebay_listing(listing["id"], {"status": "Fehler", "last_error": str(exc)})
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ebay_client.EbayApiError as exc:
        logger.exception("Publish %s: Schritt '%s' fehlgeschlagen", listing["id"], step)
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
        "grader": fields.get("grader", ""),
        "grade": fields.get("grade", ""),
        "listing_type": listing_type,
        "category_id": fields.get("category_id") or ebay_listing.CATEGORY_IDS[listing_type],
        "aspects": fields.get("aspects") or ebay_listing.build_aspects(card, listing_type),
        "price": fields.get("price", 0),
        "quantity": fields.get("quantity", 1),
    }
    sku = ebay_listing.sku_for_card(card["card_no"])
    listing = db.create_ebay_listing(card_id, sku, row)
    listing["required_aspects"] = ebay_listing.required_aspects(listing_type)
    return JSONResponse(_listing_with_card(listing))


@app.get("/api/ebay/listings")
async def list_ebay_listings(status: str | None = None, q: str | None = None):
    listings = _expand_ebay_listings(db.list_ebay_listings(status=status, q=q))
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
    if listing["status"] not in ("Entwurf", "Fehler"):
        raise HTTPException(
            status_code=409, detail="Nur Entwürfe oder fehlgeschlagene Angebote können gelöscht werden."
        )
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
        except Exception as exc:
            # Any other exception (a Supabase hiccup fetching the card,
            # a public_url() failure, ...) must stay scoped to this one
            # listing - the whole point of publish-bulk is that one bad
            # card doesn't take the rest of the batch down with it.
            results.append({"listing_id": listing_id, "status": "Fehler", "error": str(exc)})
    return JSONResponse({"results": results})


@app.get("/api/ebay/oauth/status")
async def ebay_oauth_status():
    response = httpx.get(f"{ebay_client.EBAY_OAUTH_SERVER_URL}/api/oauth/status", timeout=15)
    return JSONResponse(response.json(), status_code=response.status_code)


@app.post("/api/ebay/sync-sales")
async def sync_ebay_sales():
    try:
        token = ebay_client.get_access_token()
        cursor = db.latest_sale_sync_cursor()
        since = cursor or (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
        orders = ebay_client.get_orders(token, since)
    except ebay_client.EbayNotAuthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ebay_client.EbayApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

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


# In-memory CSRF state for the Google OAuth redirect flow - same pattern
# as ebay-oauth-server/app.py's _states. A single webapp-poc process, no
# multi-worker deployment, so a module dict is enough (no shared cache
# needed).
_sheets_oauth_states = {}
_SHEETS_STATE_TTL_SECONDS = 600


def _new_sheets_oauth_state():
    now = time.time()
    for key, created in list(_sheets_oauth_states.items()):
        if created < now - _SHEETS_STATE_TTL_SECONDS:
            _sheets_oauth_states.pop(key, None)
    state = secrets.token_urlsafe(32)
    _sheets_oauth_states[state] = now
    return state


def _consume_sheets_oauth_state(state):
    created = _sheets_oauth_states.pop(state, None)
    return created is not None and created >= time.time() - _SHEETS_STATE_TTL_SECONDS


@app.get("/api/sheets/status")
async def sheets_status():
    settings = db.get_google_sheets_settings() or {}
    return JSONResponse({
        "connected": bool(settings.get("refresh_token")),
        "spreadsheet_id": settings.get("spreadsheet_id", ""),
        "connected_at": settings.get("connected_at"),
        "last_synced_at": settings.get("last_synced_at"),
    })


@app.get("/api/sheets/oauth/start")
async def sheets_oauth_start():
    state = _new_sheets_oauth_state()
    return RedirectResponse(google_sheets_client.authorization_url(state))


def _sheets_error_redirect(message):
    # message can be external input (Google's own error text) - urlencode()
    # it into the query string instead of raw f-string interpolation, which
    # would let a "&"/"#" corrupt the query string or CRLF get rejected by
    # uvicorn as an invalid header value.
    return RedirectResponse(f"/settings.html?{urlencode({'sheets_error': message})}")


@app.get("/api/sheets/oauth/callback")
async def sheets_oauth_callback(code: str | None = None, state: str | None = None, error: str | None = None):
    if error:
        return _sheets_error_redirect(error)
    if not code or not state or not _consume_sheets_oauth_state(state):
        return _sheets_error_redirect("ungueltiger_oauth_state")
    try:
        token = google_sheets_client.exchange_code(code)
    except google_sheets_client.GoogleApiError as exc:
        return _sheets_error_redirect(str(exc))
    db.save_google_sheets_settings({
        "refresh_token": token.get("refresh_token", ""),
        "connected_at": datetime.now(timezone.utc).isoformat(),
    })
    return RedirectResponse("/settings.html")


@app.post("/api/sheets/settings")
async def update_sheets_settings(body: dict = Body(...)):
    spreadsheet_id = (body.get("spreadsheet_id") or "").strip()
    if not spreadsheet_id:
        raise HTTPException(status_code=400, detail="spreadsheet_id darf nicht leer sein.")
    updated = db.save_google_sheets_settings({"spreadsheet_id": spreadsheet_id})
    return JSONResponse(updated)


def _sheets_tabs():
    cards = db.all_cards()
    purchases = db.all_purchases()
    items_by_purchase = {}
    for item in db.all_purchase_items():
        items_by_purchase[item["purchase_id"]] = items_by_purchase.get(item["purchase_id"], 0) + 1
    listings = db.all_ebay_listings()
    sales_by_listing = {s["listing_id"]: s for s in db.all_ebay_sales() if s.get("listing_id")}

    card_headers = [
        "id", "title", "category", "team", "manufacturer", "set_name",
        "season_year", "card_number", "recognition_status", "created_at",
    ]
    card_rows = [[str(c.get(h, "") or "") for h in card_headers] for c in cards]

    purchase_headers = ["id", "purchase_date", "platform", "seller", "total_price", "notes", "Anzahl Karten"]
    purchase_rows = [
        [str(p.get(h, "") or "") for h in purchase_headers[:-1]] + [str(items_by_purchase.get(p["id"], 0))]
        for p in purchases
    ]

    ebay_headers = ["id", "title", "price", "status", "scheduled_at", "sale_date", "gross_price"]
    ebay_rows = []
    for listing in listings:
        sale = sales_by_listing.get(listing["id"], {})
        ebay_rows.append([
            str(listing.get("id", "") or ""), str(listing.get("title", "") or ""),
            str(listing.get("price", "") or ""), str(listing.get("status", "") or ""),
            str(listing.get("scheduled_at") or ""),
            str(sale.get("sale_date") or ""), str(sale.get("gross_price") or ""),
        ])

    sync_info = (["Information", "Wert"], [
        ["Quelle", "DCardsLab Supabase"],
        ["Synchronisiert", datetime.now(timezone.utc).isoformat(timespec="seconds")],
        ["Richtung", "Supabase -> Google Sheets"],
        ["Hinweis", "Supabase ist die Master-Datenbank; Sheets ist die externe Auswertungsansicht."],
    ])

    return {
        "Karten": (card_headers, card_rows), "Käufe": (purchase_headers, purchase_rows),
        "eBay": (ebay_headers, ebay_rows), "Sync_Info": sync_info,
    }


@app.post("/api/sheets/sync")
async def sync_to_sheets():
    settings = db.get_google_sheets_settings() or {}
    if not settings.get("refresh_token"):
        raise HTTPException(
            status_code=401,
            detail="Google Sheets ist nicht verbunden — bitte zuerst auf der Einstellungen-Seite verbinden.",
        )
    if not settings.get("spreadsheet_id"):
        raise HTTPException(status_code=400, detail="Bitte zuerst eine Google-Sheets-Tabellen-ID hinterlegen.")

    try:
        access_token = google_sheets_client.refresh_access_token(settings["refresh_token"])
        google_sheets_client.sync_to_sheets(access_token, settings["spreadsheet_id"], _sheets_tabs())
    except google_sheets_client.GoogleNotConnectedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except google_sheets_client.GoogleApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    synced_at = datetime.now(timezone.utc).isoformat()
    db.save_google_sheets_settings({"last_synced_at": synced_at})
    return JSONResponse({"synced_at": synced_at})


@app.get("/api/backup")
async def download_backup():
    data = backup.build_backup_zip()
    filename = f"dcardslab-backup-{datetime.now(timezone.utc).date().isoformat()}.zip"
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
