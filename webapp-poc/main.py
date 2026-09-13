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
import csv
import hmac
import io
import json
import logging
import os
import re
import secrets
import shutil
import sys
import tempfile
import time
import types
import zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlencode

import httpx
from fastapi import Body, FastAPI, File, Form, HTTPException, Request, Response, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware

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
import email_notify  # noqa: E402
import google_sheets_client  # noqa: E402
import image_hash  # noqa: E402
import portfolio  # noqa: E402
import push_notify  # noqa: E402
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

# Einfacher Passwort-Login (ein gemeinsames Passwort statt Nutzerkonten,
# genuegt fuer ein internes Ein-Team-Tool) - wie SUPABASE_SERVICE_KEY & Co.
# bewusst eine Umgebungsvariable statt eine Datenbank-Tabelle, da es ein
# Server-Zugangsdatum ist, kein Nutzer-Datensatz. Bleibt leer = kein Login
# noetig (Default, damit bestehende Deployments ohne APP_PASSWORD unveraendert
# weiterlaufen) - siehe README.md, "Login einrichten".
APP_PASSWORD = os.environ.get("APP_PASSWORD", "").strip()
SESSION_SECRET_KEY = os.environ.get("SESSION_SECRET_KEY", "").strip()
if APP_PASSWORD and not SESSION_SECRET_KEY:
    # Ohne einen stabilen Schluessel signiert jeder Neustart mit einem neuen
    # Zufallswert (secrets.token_hex() unten) - alle Sitzungen werden dann bei
    # jedem Deploy/Neustart ungueltig. Kein hartes Fehlschlagen (der Login
    # funktioniert trotzdem, nur eben mit dieser Einschraenkung), aber ein
    # deutlicher Hinweis im Log.
    logger.warning(
        "APP_PASSWORD gesetzt, aber SESSION_SECRET_KEY fehlt - Logins werden "
        "bei jedem Neustart ungueltig. SESSION_SECRET_KEY setzen (fester, "
        "geheimer Zufallswert), damit Sitzungen einen Neustart ueberleben."
    )
# Standard False, damit bestehende Deployments ohne HTTPS (z.B. reines LAN
# ohne Reverse-Proxy-TLS) nicht ploetzlich ausgesperrt werden - ein Browser
# sendet ein "secure"-Cookie sonst nie ueber eine unverschluesselte
# Verbindung. Auf true setzen, sobald das Tool nur noch ueber HTTPS erreichbar
# ist (siehe README.md, "Login einrichten").
SESSION_COOKIE_SECURE = os.environ.get("SESSION_COOKIE_SECURE", "").strip().lower() in ("1", "true", "yes")
try:
    # 20160 Minuten = 14 Tage, bisheriger SessionMiddleware-Default - jetzt
    # explizit statt implizit, und als gleitendes Inaktivitaets-Fenster
    # (siehe _require_login() unten) statt einer festen Ablaufzeit ab dem
    # Login: jede Nutzung verlaengert die Sitzung um denselben Zeitraum.
    SESSION_TIMEOUT_MINUTES = int(os.environ.get("SESSION_TIMEOUT_MINUTES", "").strip() or 20160)
except ValueError:
    SESSION_TIMEOUT_MINUTES = 20160

def _read_build_info_file(name):
    # Alle drei Dateien (GIT_COMMIT, LAST_COMMIT_SUBJECT, BUILD_TIME) werden
    # vom Dockerfile automatisch erzeugt (git-info-Build-Stage bzw. RUN date)
    # - kein --build-arg noetig, funktioniert daher auch bei einem Rebuild
    # ueber eine NAS-Docker-App ohne eigene Kommandozeile. "unbekannt"
    # ausserhalb eines Docker-Builds (z.B. lokaler Testlauf via uvicorn
    # direkt). Ueber GET /api/version abrufbar, damit sich per simplem
    # HTTP-Request pruefen laesst, ob ein erwarteter PR/Commit tatsaechlich
    # im laufenden Container steckt, ohne Shell-Zugriff auf den
    # Deployment-Host zu brauchen - gleiches Prinzip wie ebay-oauth-server's
    # /health mit configured_scopes.
    try:
        return (Path(__file__).parent / name).read_text().strip() or "unbekannt"
    except FileNotFoundError:
        return "unbekannt"


def _extract_pr_number(commit_subject):
    # Ein Merge-Commit von GitHub traegt als Betreffzeile "Merge pull
    # request #NN from ..." - daraus die PR-Nummer herausziehen, statt den
    # Nutzer den ganzen (oft langen) Commit-Text lesen zu lassen. None fuer
    # jeden anderen Commit (z.B. ein Squash-Merge oder ein direkter Commit
    # auf main ohne PR).
    match = re.search(r"pull request #(\d+)", commit_subject, re.IGNORECASE)
    return int(match.group(1)) if match else None


GIT_COMMIT = _read_build_info_file("GIT_COMMIT")
LAST_COMMIT_SUBJECT = _read_build_info_file("LAST_COMMIT_SUBJECT")
BUILD_TIME = _read_build_info_file("BUILD_TIME")
LAST_PR = _extract_pr_number(LAST_COMMIT_SUBJECT)


@app.on_event("startup")
async def _start_ebay_scheduler():
    # publish_fn/sync_sales_fn/on_new_sales are plain lambda/function
    # closures onto functions defined further down in this module - avoids
    # importing main.py from ebay_scheduler.py (which already imports
    # db/ebay_client, not main).
    asyncio.create_task(ebay_scheduler.run_forever(
        lambda listing: _publish_listing(listing),
        sync_sales_fn=_sync_ebay_sales_once,
        on_new_sales=_notify_new_sales,
        sync_returns_fn=_sync_ebay_returns_once,
        auto_relist_fn=_run_auto_relist_once,
    ))


@app.on_event("startup")
async def _start_backup_scheduler():
    asyncio.create_task(backup.run_forever())


@app.on_event("startup")
async def _start_portfolio_scheduler():
    asyncio.create_task(portfolio.run_forever(_compute_portfolio_value))


@app.on_event("startup")
async def _start_reminder_digest_scheduler():
    asyncio.create_task(_run_reminder_digest_forever())


# Eigener, taeglicher Takt statt jeden 5-Minuten-Durchlauf von
# ebay_scheduler.py mitzunutzen - eine Wiedervorlage/Erinnerung ist per
# Definition kein Echtzeit-Ereignis wie ein neuer Verkauf.
_REMINDER_DIGEST_CHECK_INTERVAL_SECONDS = 3600
_REMINDER_DIGEST_INTERVAL_HOURS = 24


def _is_reminder_digest_due():
    status = db.get_app_status() or {}
    last = status.get("last_reminder_email_sent_at")
    if not last:
        return True
    try:
        last_dt = datetime.fromisoformat(last)
    except ValueError:
        return True
    return datetime.now(timezone.utc) - last_dt >= timedelta(hours=_REMINDER_DIGEST_INTERVAL_HOURS)


def _send_reminder_digest_if_due():
    # Zeitstempel wird unabhaengig vom Schalter/Ergebnis gesetzt (gleiches
    # Prinzip wie backup.py's run_scheduled_backup_once()), damit ein
    # deaktivierter Schalter oder ein leerer Digest nicht bei jedem
    # stuendlichen Takt erneut geprueft wird.
    if not _is_reminder_digest_due():
        return
    try:
        db.record_reminder_email_sent(datetime.now(timezone.utc).isoformat())
    except Exception:
        logger.exception("Zeitstempel des Erinnerungs-Digests konnte nicht gespeichert werden")
        return
    try:
        settings = db.get_app_status() or {}
        if not settings.get("notify_on_reminders"):
            return
        due_reminders = _due_reminders_with_titles()
        stale_listings = _stale_listing_reminders(settings)
        stale_wishlist = _stale_wishlist_reminders(settings)
        if not (due_reminders or stale_listings or stale_wishlist):
            return
        subject, body = email_notify.format_reminder_digest(due_reminders, stale_listings, stale_wishlist)
        email_notify.send_email(settings, subject, body)
    except Exception:
        logger.exception("Wiedervorlage-E-Mail-Digest fehlgeschlagen")


async def _run_reminder_digest_forever():
    while True:
        _send_reminder_digest_if_due()
        await asyncio.sleep(_REMINDER_DIGEST_CHECK_INTERVAL_SECONDS)

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


def _find_duplicate_card_safe(fields):
    # Isolated from process_one()'s own error handling, same reasoning as
    # _create_default_inventory_item(): a failed duplicate check (Supabase
    # hiccup) must not make an otherwise-successful scan look failed.
    try:
        return db.find_duplicate_card(fields.get("title"), fields.get("set_name"), fields.get("card_number"))
    except Exception:
        logger.exception("Duplikat-Pruefung fehlgeschlagen")
        return None


# Handyscan (card-new.html?mode=handyscan): zusaetzlich zum Upload in den
# Supabase-Storage-Bucket wird eine Rohkopie von Vorder-/Rueckseite hier
# abgelegt - fester Container-Pfad statt Env-Var (siehe README), muss beim
# Deploy per Docker-Volume auf einen NAS-Ordner gemountet werden. Ohne
# Mount landen die Dateien einfach im (ephemeren) Container-Dateisystem.
HANDYSCAN_ARCHIVE_DIR = Path("/data/handyscan")


def _archive_handyscan_photos_safe(front_path, back_path):
    # Isoliert wie _find_duplicate_card_safe() - ein fehlendes/schreibge-
    # schuetztes Mount-Verzeichnis darf das eigentliche Kartenanlegen nicht
    # scheitern lassen, das Archivieren ist ein reiner Zusatznutzen.
    try:
        HANDYSCAN_ARCHIVE_DIR.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        shutil.copyfile(front_path, HANDYSCAN_ARCHIVE_DIR / f"{stamp}_front{front_path.suffix}")
        shutil.copyfile(back_path, HANDYSCAN_ARCHIVE_DIR / f"{stamp}_back{back_path.suffix}")
    except Exception:
        logger.exception("Handyscan-Archivierung fehlgeschlagen")


def _compute_image_hash_safe(image_path):
    # Perzeptueller Hash (dHash, siehe image_hash.py) des Vorderseitenfotos -
    # faengt Duplikate ab, bei denen Titel/Set/Kartennummer durch einen OCR-/
    # KI-Erkennungsfehler nicht exakt uebereinstimmen, das Foto aber (nahezu)
    # dasselbe ist. Isoliert wie _find_duplicate_card_safe() oben, damit ein
    # kaputtes/unlesbares Bild den restlichen Scan nicht scheitern laesst.
    try:
        return image_hash.compute_hash(image_path)
    except Exception:
        logger.exception("Bild-Hash-Berechnung fehlgeschlagen")
        return None


def _find_duplicate_card_by_hash_safe(hash_value, exclude_card_id=None):
    try:
        return db.find_duplicate_card_by_image_hash(hash_value, exclude_card_id=exclude_card_id)
    except Exception:
        logger.exception("Foto-basierte Duplikat-Pruefung fehlgeschlagen")
        return None


def _create_default_inventory_item(card_id, location="", notes="", private=False):
    # Every scanned card gets a quantity-1 "NM" inventory row automatically -
    # the seller physically has the card in hand at scan time, so requiring
    # a separate manual step for the common case would just be busywork.
    # location/notes come from the same scan/manual-add form (one shared
    # storage spot for a whole scan batch, or per-card for a manual add) so
    # they don't need a second trip through card.html's Inventar section
    # just to record where the card physically ended up. A card marked for
    # the private Sammlung right at creation (Privatentnahme) never counts
    # as sellable stock, so it starts at quantity 0 instead of 1.
    # Isolated from the card-insert's own error handling on purpose: this
    # failing must not make an otherwise-successful card insert look failed
    # to the caller (see process_one() below) - it's a soft warning only.
    try:
        db.create_inventory_item(
            card_id, {"quantity": 0 if private else 1, "location": location, "notes": notes}
        )
    except Exception:
        logger.exception("Automatischer Inventareintrag fuer Karte %s fehlgeschlagen", card_id)


@app.post("/api/scan")
async def scan(
    front: UploadFile = File(...), back: UploadFile = File(...),
    location: str = Form(""), notes: str = Form(""),
    private_collection: bool = Form(False),
):
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
                if private_collection:
                    fields["private_collection"] = True
                try:
                    card_row = db.insert_card(batch_id, number, fields, None, None)
                except Exception as exc:
                    return {
                        "number": number,
                        **fields,
                        "image_error": f"Datenbank-Insert fehlgeschlagen: {type(exc).__name__}: {exc}",
                    }
                _create_default_inventory_item(card_row["id"], location, notes, private=private_collection)
                return {"number": number, **fields, "id": card_row["id"]}

            fields = recognize_card(front_path=fp, back_path=bp)
            if private_collection:
                fields["private_collection"] = True
            duplicate = _find_duplicate_card_safe(fields)
            front_hash = _compute_image_hash_safe(fp)
            if duplicate is None and front_hash:
                # Foto-basierte Ergaenzung: faengt Duplikate ab, bei denen
                # Titel/Set/Kartennummer durch einen Erkennungsfehler nicht
                # exakt uebereinstimmen (siehe find_duplicate_card_by_image_hash()).
                photo_duplicate = _find_duplicate_card_by_hash_safe(front_hash)
                if photo_duplicate:
                    duplicate = {**photo_duplicate, "matched_by": "photo"}

            front_image_path = back_image_path = None
            image_error = None
            try:
                front_image_path = storage.upload_image(batch_id, number, "front", fp)
                back_image_path = storage.upload_image(batch_id, number, "back", bp)
            except Exception as exc:
                stage = "front-Bild-Upload" if front_image_path is None else "Rückseiten-Bild-Upload"
                image_error = f"{stage} fehlgeschlagen: {type(exc).__name__}: {exc}"

            result = {"number": number, **fields}
            if duplicate:
                result["possible_duplicate"] = duplicate
            try:
                card_row = db.insert_card(
                    batch_id, number, fields, front_image_path, back_image_path, front_image_hash=front_hash,
                )
                result["id"] = card_row["id"]
            except Exception as exc:
                if image_error is None:
                    image_error = f"Datenbank-Insert fehlgeschlagen: {type(exc).__name__}: {exc}"
                result["image_error"] = image_error
                return result
            _create_default_inventory_item(card_row["id"], location, notes, private=private_collection)

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
    location: str = Form(""), notes: str = Form(""), archive_photos: bool = Form(False),
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

        if archive_photos:
            _archive_handyscan_photos_safe(front_path, back_path)

        duplicate = _find_duplicate_card_safe(parsed_fields)
        front_hash = _compute_image_hash_safe(front_path)
        if duplicate is None and front_hash:
            photo_duplicate = _find_duplicate_card_by_hash_safe(front_hash)
            if photo_duplicate:
                duplicate = {**photo_duplicate, "matched_by": "photo"}

        batch_id = db.create_batch(card_count=1)
        try:
            front_image_path = storage.upload_image(batch_id, 1, "front", front_path)
            back_image_path = storage.upload_image(batch_id, 1, "back", back_path)
            card_row = db.insert_card(
                batch_id, 1, parsed_fields, front_image_path, back_image_path, front_image_hash=front_hash,
            )
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
    _create_default_inventory_item(
        card_row["id"], location, notes, private=bool(parsed_fields.get("private_collection"))
    )
    result = _attach_signed_urls(card_row)
    if duplicate:
        # Gleiche Warnung wie beim Scannen (siehe process_one()) - bisher
        # gab es diese Pruefung nur fuer gescannte Karten, obwohl eine
        # manuell angelegte Karte genauso ein Duplikat sein kann.
        result["possible_duplicate"] = duplicate
    return JSONResponse(result)


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


def _expand_inventory_items(items):
    # Same enrichment as _expand_purchase_items() - a lean card summary
    # (id/title/front_image_url) so inventory.html doesn't need a
    # per-row /api/cards/{id} request. Also attaches the linked eBay
    # listing's SKU (if any), for inventory.html's SKU column.
    if not items:
        return []
    card_ids = [item["card_id"] for item in items]
    cards_by_id = {c["id"]: c for c in db.get_cards_by_ids(card_ids)}
    ebay_info = db.ebay_info_by_card_id(card_ids)
    manual_sale_info = db.manual_sale_info_by_card_id(card_ids)
    purchase_costs = db.purchase_cost_by_card_id(card_ids)
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
        info = ebay_info.get(item["card_id"]) or {}
        item["sku"] = info.get("sku")
        item["ebay_status"] = info.get("status")
        item["manual_sale_channel"] = (manual_sale_info.get(item["card_id"]) or {}).get("channel") or None
        item["private_collection"] = bool(card.get("private_collection"))
        item["card_type"] = ebay_listing.derive_listing_type(card)
        # Inventarwert je Zeile: bevorzugt der aktuelle eBay-Angebotspreis
        # (was die Karte JETZT wert sein soll), sonst ersatzweise der
        # Einstandspreis aus einem verknuepften Kauf - fehlen beide, bleibt
        # der Wert unbekannt (None) statt mit 0 zu rechnen.
        unit_value = info.get("price") or purchase_costs.get(item["card_id"])
        item["value"] = unit_value * item.get("quantity", 0) if unit_value is not None else None
        expanded.append(item)
    return expanded


def _attach_purchase_items(purchase):
    purchase = dict(purchase)
    purchase["items"] = _expand_purchase_items(purchase.get("items", []))
    receipt_path = purchase.get("receipt_path")
    if receipt_path:
        # Gleiche Guard-Logik wie bei _attach_signed_urls(): ein Storage-
        # Hiccup oder ein Pfad, der nicht mehr aufloest, darf die restliche
        # Kaufauskunft nicht mit einem 500 blockieren.
        try:
            purchase["receipt_url"] = storage.receipt_signed_url(receipt_path)
        except Exception:
            pass
    return purchase


def _attach_manual_sale_receipt(sale):
    if sale is None:
        return sale
    sale = dict(sale)
    receipt_path = sale.get("receipt_path")
    if receipt_path:
        # Gleiche Guard-Logik wie _attach_purchase_items(): ein Storage-
        # Hiccup darf die restliche Verkaufsauskunft nicht mit einem 500
        # blockieren.
        try:
            sale["receipt_url"] = storage.sale_receipt_signed_url(receipt_path)
        except Exception:
            pass
    return sale


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
    extra_urls = []
    for path in _split_extra_image_paths(card):
        try:
            extra_urls.append(storage.signed_url(path))
        except Exception:
            pass
    card["extra_image_urls"] = extra_urls
    return card


def _split_extra_image_paths(card):
    return [p for p in (card.get("extra_image_paths") or "").split(",") if p]


def _compute_portfolio_value():
    """Summe des geschaetzten Bestandswerts ueber alle vorraetigen Inventar-
    Eintraege - dieselbe Bewertung wie inventory.html's "Wert"-Spalte
    (_expand_inventory_items(): bevorzugt der aktuelle eBay-Angebotspreis,
    sonst ersatzweise der Einstandspreis), nur hier aufsummiert statt pro
    Zeile angezeigt. Verwendet vom woechentlichen Portfolio-Wertverlauf-
    Schnappschuss (portfolio.py) und vom manuellen "Jetzt Snapshot
    erstellen"-Button. Gibt (total_value, card_count) zurueck."""
    items = _expand_inventory_items(db.list_inventory())
    in_stock = [item for item in items if (item.get("quantity") or 0) > 0]
    total_value = sum(item["value"] for item in in_stock if item.get("value") is not None)
    return round(total_value, 2), len(in_stock)


@app.get("/api/cards")
async def list_cards(q: str | None = None, status: str | None = None):
    matched = db.list_cards(q=q, status=status)
    if q:
        # SKU lebt in ebay_listings, nicht in cards - per SKU gefundene
        # Karten (z.B. Suche nach der Bestandsnummer) zusaetzlich mischen.
        existing_ids = {c["id"] for c in matched}
        matched += [c for c in db.list_cards_by_sku(q) if c["id"] not in existing_ids]
    cards = [_attach_signed_urls(c) for c in matched]
    card_ids = [c["id"] for c in cards]
    linked_ids = db.cards_with_purchase(card_ids)
    ebay_info = db.ebay_info_by_card_id(card_ids)
    manual_sale_info = db.manual_sale_info_by_card_id(card_ids)
    sale_flags = db.sale_flags_by_card_id(card_ids)
    for c in cards:
        c["has_purchase"] = c["id"] in linked_ids
        info = ebay_info.get(c["id"]) or {}
        c["ebay_status"] = info.get("status")
        c["ebay_sku"] = info.get("sku")
        # Importiertes, extern verwaltetes Angebot (siehe _is_externally_managed()
        # weiter unten) - eigenes Feld statt das Frontend die Kombination aus
        # ebay_status/ebay_offer_id nachrechnen zu lassen.
        c["ebay_imported"] = info.get("status") == "Veroeffentlicht" and not info.get("ebay_offer_id")
        # Fuer das 🔄-/⏳-Badge in der Kartenuebersicht (siehe cards.html) -
        # gleiche Felder wie auf card.html/ebay.html, siehe _relist_listing()
        # bzw. _publish_listing().
        c["last_auto_relisted_at"] = info.get("last_auto_relisted_at")
        c["listing_since"] = info.get("listing_since")
        # Gleiche Sport/Non-Sport-Heuristik wie beim Anlegen eines eBay-
        # Angebots (ebay_listing.derive_listing_type() - card.category gegen
        # die bekannte Sportarten-Liste) - hier als Filter-Grundlage, nicht
        # nur fuers eBay-Kategorie-Mapping.
        c["card_type"] = ebay_listing.derive_listing_type(c)
        c["manual_sale_channel"] = (manual_sale_info.get(c["id"]) or {}).get("channel") or None
        flags = sale_flags.get(c["id"]) or {}
        c["delivered"] = flags.get("delivered", False)
        c["refunded"] = flags.get("refunded", False)
    return JSONResponse({"cards": cards})


@app.get("/api/search")
async def global_search(q: str | None = None):
    # Globale Suche (search.html) ueber Karten, Kaeufe und Verkaeufe - bei
    # der Groessenordnung dieses Tools reicht ein einfacher ilike-Abgleich
    # pro Tabelle statt eines Such-Index; jede Liste auf 20 Treffer gedeckelt,
    # da das Ergebnis nur eine grobe Uebersicht mit Direktlinks sein soll.
    q = (q or "").strip()
    if not q:
        return JSONResponse({"cards": [], "purchases": [], "sales": []})
    matched_cards = db.list_cards(q=q)
    existing_ids = {c["id"] for c in matched_cards}
    matched_cards += [c for c in db.list_cards_by_sku(q) if c["id"] not in existing_ids]
    return JSONResponse({
        "cards": matched_cards[:20],
        "purchases": db.list_purchases(q=q)[:20],
        "sales": db.search_sales(q)[:20],
    })


@app.get("/api/cards/{card_id}")
async def get_card(card_id: str):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    card = _attach_signed_urls(card)
    card["purchase"] = db.get_purchase_for_card(card_id)
    card["ebay_listing"] = db.get_ebay_listing_for_card(card_id)
    card["inventory"] = db.get_inventory_for_card(card_id)
    card["ebay_sale"] = db.get_sale_for_card(card_id)
    card["manual_sale"] = _attach_manual_sale_receipt(db.get_manual_sale_for_card(card_id))
    try:
        card["price_research"] = db.list_price_research_for_card(card_id)
    except Exception:
        # Faellt zurueck auf eine leere Liste statt die ganze Kartenseite mit
        # einem 500 zu blockieren, z.B. wenn die price_research-Migration in
        # dieser Supabase-Instanz noch nicht eingespielt wurde.
        logger.exception("Preisrecherche-Verlauf konnte nicht geladen werden fuer Karte %s", card_id)
        card["price_research"] = []
    try:
        card["reminders"] = db.list_reminders_for_card(card_id)
    except Exception:
        logger.exception("Erinnerungen konnten nicht geladen werden fuer Karte %s", card_id)
        card["reminders"] = []
    return JSONResponse(card)


@app.patch("/api/cards/{card_id}")
async def update_card(card_id: str, fields: dict = Body(...)):
    updated = db.update_card(card_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    return JSONResponse(_attach_signed_urls(updated))


@app.post("/api/cards/{card_id}/private-collection")
async def mark_card_private_collection(card_id: str):
    updated = db.set_card_private_collection(card_id, True)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    return JSONResponse(_attach_signed_urls(updated))


@app.delete("/api/cards/{card_id}/private-collection")
async def unmark_card_private_collection(card_id: str):
    updated = db.set_card_private_collection(card_id, False)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    return JSONResponse(_attach_signed_urls(updated))


@app.get("/api/private-collection")
async def list_private_collection(q: str | None = None):
    cards = [_attach_signed_urls(c) for c in db.list_private_collection_cards(q=q)]
    return JSONResponse({"cards": cards})


# Wiedervorlage/Erinnerungen (Klaerung mit dem Nutzer) - freie, selbst
# angelegte Erinnerungen an eine Karte, ergaenzend zu den beiden
# automatischen Regeln (siehe _stale_listing_reminders()/
# _stale_wishlist_reminders() unten, genutzt vom Dashboard und dem
# E-Mail-Digest).
@app.post("/api/cards/{card_id}/reminders")
async def create_reminder(card_id: str, fields: dict = Body(...)):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    due_date = fields.get("due_date")
    if not due_date:
        raise HTTPException(status_code=400, detail="due_date wird benötigt.")
    reminder = db.create_reminder(card_id, fields.get("note", ""), due_date)
    return JSONResponse(reminder)


@app.post("/api/reminders/{reminder_id}/resolve")
async def resolve_reminder(reminder_id: str):
    reminder = db.resolve_reminder(reminder_id)
    if reminder is None:
        raise HTTPException(status_code=404, detail=f"Erinnerung {reminder_id} nicht gefunden.")
    return JSONResponse(reminder)


@app.delete("/api/reminders/{reminder_id}", status_code=204)
async def delete_reminder(reminder_id: str):
    db.delete_reminder(reminder_id)
    return Response(status_code=204)


# Default-Schwellwerte fuer die beiden automatischen Wiedervorlage-Regeln
# (Klaerung mit dem Nutzer, Beispiel "seit 90 Tagen unverkauft") - greifen nur,
# solange in app_status noch keine eigenen Werte gespeichert sind (Einstellungen,
# Abschnitt E-Mail-Benachrichtigungen/Wiedervorlage).
STALE_LISTING_MIN_DAYS = 90
STALE_LISTING_CHECK_MAX_AGE_DAYS = 30
STALE_WISHLIST_MIN_DAYS = 60

# Default-Umsatzgrenzen der Kleinunternehmerregelung (Paragraph 19 UStG,
# Stand 2025) - greifen nur, solange in app_status noch keine eigenen Werte
# gespeichert sind (Einstellungen, Abschnitt Kleinunternehmer-Schwellenwerte).
KLEINUNTERNEHMER_PREV_YEAR_THRESHOLD = 22000
KLEINUNTERNEHMER_CURRENT_YEAR_THRESHOLD = 50000


def _stale_listing_reminders(status=None):
    if status is None:
        status = db.get_app_status() or {}
    min_days = status.get("stale_listing_min_days") or STALE_LISTING_MIN_DAYS
    check_days = status.get("stale_listing_check_days") or STALE_LISTING_CHECK_MAX_AGE_DAYS
    listings = db.list_stale_unsold_listings(min_days, check_days)
    card_ids = [l["card_id"] for l in listings]
    cards_by_id = {c["id"]: c for c in db.get_cards_by_ids(card_ids)}
    return [
        {**l, "title": cards_by_id.get(l["card_id"], {}).get("title")}
        for l in listings
    ]


def _stale_wishlist_reminders(status=None):
    if status is None:
        status = db.get_app_status() or {}
    min_days = status.get("stale_wishlist_min_days") or STALE_WISHLIST_MIN_DAYS
    return db.list_stale_wishlist_items(min_days)


def _due_reminders_with_titles():
    reminders = db.list_due_reminders()
    card_ids = [r["card_id"] for r in reminders]
    cards_by_id = {c["id"]: c for c in db.get_cards_by_ids(card_ids)}
    return [
        {**r, "title": cards_by_id.get(r["card_id"], {}).get("title")}
        for r in reminders
    ]


@app.get("/api/dashboard/reminders")
async def dashboard_reminders():
    status = db.get_app_status() or {}
    return JSONResponse({
        "due_reminders": _due_reminders_with_titles(),
        "stale_listings": _stale_listing_reminders(status),
        "stale_wishlist": _stale_wishlist_reminders(status),
    })


def _delete_card_and_images(card_id):
    deleted = db.delete_card(card_id)
    if deleted is None:
        return None
    paths = [p for p in (deleted.get("front_image_path"), deleted.get("back_image_path")) if p]
    paths += _split_extra_image_paths(deleted)
    if paths:
        try:
            storage.delete_images(paths)
        except Exception as exc:
            print(f"Bild-Löschung fehlgeschlagen für {paths}: {type(exc).__name__}: {exc}")
    return deleted


@app.delete("/api/cards/{card_id}", status_code=204)
async def delete_card(card_id: str):
    deleted = _delete_card_and_images(card_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    return Response(status_code=204)


@app.post("/api/cards/{card_id}/merge-into/{target_id}")
async def merge_duplicate_card(card_id: str, target_id: str):
    # Beim Scannen erkannte moegliche Duplikate (siehe _find_duplicate_card_safe)
    # sollen sich zusammenfuehren lassen, statt als zweite eigenstaendige Karte
    # stehen zu bleiben: die neu gescannte Karte wird verworfen, ihre
    # Inventar-Menge wandert auf die bestehende Karte.
    if card_id == target_id:
        raise HTTPException(status_code=400, detail="Eine Karte kann nicht mit sich selbst zusammengeführt werden.")
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    target = db.get_card(target_id)
    if target is None:
        raise HTTPException(status_code=404, detail=f"Karte {target_id} nicht gefunden.")

    added_quantity = sum(item.get("quantity") or 0 for item in db.get_inventory_for_card(card_id)) or 1
    target_items = db.get_inventory_for_card(target_id)
    if target_items:
        item = target_items[0]
        db.update_inventory_item(item["id"], {"quantity": (item.get("quantity") or 0) + added_quantity})
    else:
        db.create_inventory_item(target_id, {"quantity": added_quantity})

    _delete_card_and_images(card_id)
    return JSONResponse(_attach_signed_urls(db.get_card(target_id)))


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


@app.post("/api/cards/{card_id}/image")
async def replace_card_image(card_id: str, side: str = Form(...), file: UploadFile = File(...)):
    if side not in ("front", "back"):
        raise HTTPException(status_code=400, detail="side muss 'front' oder 'back' sein.")

    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")

    with tempfile.TemporaryDirectory(prefix="dcardslab_replace_") as tmp_str:
        tmp_path = Path(tmp_str) / f"{side}{Path(file.filename or f'{side}.jpg').suffix}"
        tmp_path.write_bytes(await file.read())
        try:
            # batch_id/position_in_batch sind fix pro Karte -> ergibt denselben
            # Objekt-Pfad wie beim urspruenglichen Upload und ueberschreibt ihn
            # per upsert (siehe storage.upload_image), egal ob vorher schon ein
            # Bild da war oder nicht.
            compressed = storage.compress_image(tmp_path)
            object_path = storage.upload_image(card["batch_id"], card["position_in_batch"], side, tmp_path)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Bild-Upload fehlgeschlagen: {type(exc).__name__}: {exc}"
            ) from exc

    updated = db.set_card_image_path(card_id, side, object_path)
    result = _attach_signed_urls(updated)
    # Gleiche Begruendung wie beim Dreh-Endpunkt: Supabase Storage's CDN kann
    # kurz nach dem Ueberschreiben noch eine veraltete Kopie ausliefern, selbst
    # bei frisch signierter URL - die Data-URI kommt direkt aus der gerade
    # hochgeladenen Kompression und kann nicht veraltet sein.
    result["replaced_image_data_uri"] = "data:image/jpeg;base64," + base64.b64encode(compressed).decode("ascii")
    return JSONResponse(result)


@app.post("/api/cards/{card_id}/images")
async def add_card_extra_image(card_id: str, file: UploadFile = File(...)):
    # Zusaetzliches Foto neben Vorder-/Rueckseite (z.B. Nahaufnahme eines
    # Schadens) - anders als replace_card_image() ein neuer, eigener
    # Storage-Pfad statt eines Upserts auf einen festen Pfad, da hier
    # mehrere Fotos nebeneinander bestehen sollen.
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")

    with tempfile.TemporaryDirectory(prefix="dcardslab_extra_") as tmp_str:
        tmp_path = Path(tmp_str) / f"extra{Path(file.filename or 'extra.jpg').suffix}"
        tmp_path.write_bytes(await file.read())
        try:
            object_path = storage.upload_extra_image(card["batch_id"], card["position_in_batch"], tmp_path)
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"Bild-Upload fehlgeschlagen: {type(exc).__name__}: {exc}"
            ) from exc

    updated = db.add_card_extra_image(card_id, object_path)
    return JSONResponse(_attach_signed_urls(updated))


@app.delete("/api/cards/{card_id}/images/{index}", status_code=204)
async def delete_card_extra_image(card_id: str, index: int):
    updated, removed_path = db.remove_card_extra_image(card_id, index)
    if updated is None:
        raise HTTPException(status_code=404, detail="Foto nicht gefunden.")
    if removed_path:
        try:
            storage.delete_images([removed_path])
        except Exception:
            logger.exception("Bild-Löschung fehlgeschlagen für Karte %s, Pfad %s", card_id, removed_path)
    return Response(status_code=204)


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


_PURCHASE_IMPORT_TEXT_COLUMNS = (("Plattform", "platform"), ("Verkäufer", "seller"), ("Notizen", "notes"))
_PURCHASE_IMPORT_MONEY_COLUMNS = (("Gesamt", "total_price"), ("Versand", "shipping"))


def _parse_import_date(value):
    # Spiegelt formatDate() im Frontend (DD.MM.YYYY) - der CSV-Export der
    # Kaeufe-Seite schreibt genau dieses Format, der Import soll also ohne
    # Umformatierung wieder eingelesen werden koennen. "YYYY-MM-DD" wird
    # zusaetzlich akzeptiert, falls jemand die Rohdaten direkt bearbeitet.
    value = (value or "").strip()
    if not value:
        return ""
    match = re.match(r"^(\d{2})\.(\d{2})\.(\d{4})$", value)
    if match:
        d, m, y = match.groups()
        return f"{y}-{m}-{d}"
    if re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return value
    return None


@app.post("/api/purchases/import")
async def import_purchases_csv(file: UploadFile = File(...)):
    # Bulk-Import von Kaeufen ueber eine CSV-Datei im selben Format wie der
    # bestehende CSV-Export auf purchases.html (Semikolon-getrennt, Spalten
    # per Name statt Position erkannt, damit Reihenfolge/Zusatzspalten wie
    # "Karten" nicht stoeren). Kein Karten-Import - Karten entstehen ueber
    # den Foto-Scan/KI-Workflow, nicht aus reinen Textdaten.
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV muss UTF-8-kodiert sein.") from exc

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    imported = []
    errors = []
    for line_no, row in enumerate(reader, start=2):
        fields = {}
        date_raw = (row.get("Datum") or "").strip()
        purchase_date = _parse_import_date(date_raw)
        if purchase_date is None:
            errors.append(f"Zeile {line_no}: Ungültiges Datum „{date_raw}“.")
            continue
        if purchase_date:
            fields["purchase_date"] = purchase_date
        for header, field_name in _PURCHASE_IMPORT_TEXT_COLUMNS:
            value = (row.get(header) or "").strip()
            if value:
                fields[field_name] = value
        invalid_amount = False
        for header, field_name in _PURCHASE_IMPORT_MONEY_COLUMNS:
            value = (row.get(header) or "").strip()
            if not value:
                continue
            try:
                fields[field_name] = float(value.replace(",", "."))
            except ValueError:
                errors.append(f"Zeile {line_no}: Ungültiger Betrag bei „{header}“: „{value}“.")
                invalid_amount = True
                break
        if invalid_amount:
            continue
        if not fields:
            continue
        imported.append(db.create_purchase(fields))
    return JSONResponse({"imported": len(imported), "errors": errors, "purchases": imported})


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
    # Neue Verknuepfung aendert die Anzahl Karten im Kauf -> Kaufpreis wird
    # gleichmaessig neu auf alle Karten (inkl. der gerade hinzugefuegten)
    # aufgeteilt, statt bei 0 zu bleiben.
    updated_items = db.recompute_purchase_item_costs(purchase_id)
    item = next((i for i in updated_items if i["id"] == item["id"]), item)
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
    # Verbleibende Karten im Kauf bekommen einen neuen, groesseren Anteil.
    db.recompute_purchase_item_costs(purchase_id)
    return Response(status_code=204)


_RECEIPT_MAX_BYTES = 10 * 1024 * 1024


@app.post("/api/purchases/{purchase_id}/receipt")
async def upload_purchase_receipt(purchase_id: str, file: UploadFile = File(...)):
    purchase = db.get_purchase(purchase_id)
    if purchase is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    if file.content_type not in storage.RECEIPT_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Beleg muss PDF, JPEG, PNG oder WebP sein.",
        )
    data = await file.read()
    if len(data) > _RECEIPT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Beleg darf höchstens 10 MB groß sein.")

    old_path = purchase.get("receipt_path")
    if old_path:
        try:
            storage.delete_receipt(old_path)
        except Exception:
            logger.exception("Alten Beleg konnte nicht gelöscht werden für Kauf %s", purchase_id)

    try:
        object_path = storage.upload_receipt(purchase_id, file.content_type, data)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Beleg-Upload fehlgeschlagen: {type(exc).__name__}: {exc}"
        ) from exc

    updated = db.set_purchase_receipt(purchase_id, object_path)
    return JSONResponse(_attach_purchase_items(updated))


@app.delete("/api/purchases/{purchase_id}/receipt")
async def delete_purchase_receipt(purchase_id: str):
    purchase = db.get_purchase(purchase_id)
    if purchase is None:
        raise HTTPException(status_code=404, detail=f"Kauf {purchase_id} nicht gefunden.")
    old_path = purchase.get("receipt_path")
    if old_path:
        try:
            storage.delete_receipt(old_path)
        except Exception:
            logger.exception("Beleg konnte nicht gelöscht werden für Kauf %s", purchase_id)
    updated = db.set_purchase_receipt(purchase_id, "")
    return JSONResponse(_attach_purchase_items(updated))


@app.get("/api/inventory")
async def list_inventory():
    items = _expand_inventory_items(db.list_inventory())
    total_value = sum(item["value"] for item in items if item["value"] is not None)
    return JSONResponse({"inventory": items, "total_value": round(total_value, 2)})


@app.post("/api/cards/{card_id}/inventory")
async def create_inventory_item(card_id: str, fields: dict = Body(default={})):
    if db.get_card(card_id) is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    item = db.create_inventory_item(card_id, fields)
    return JSONResponse(_expand_inventory_items([item])[0])


@app.patch("/api/inventory/{item_id}")
async def update_inventory_item(item_id: str, fields: dict = Body(...)):
    updated = db.update_inventory_item(item_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Inventareintrag {item_id} nicht gefunden.")
    return JSONResponse(_expand_inventory_items([updated])[0])


@app.delete("/api/inventory/{item_id}", status_code=204)
async def delete_inventory_item(item_id: str):
    deleted = db.delete_inventory_item(item_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Inventareintrag {item_id} nicht gefunden.")
    return Response(status_code=204)


@app.get("/api/wishlist")
async def list_wishlist_items(q: str | None = None):
    items = db.list_wishlist_items(q=q)
    history = db.wishlist_price_history_by_item_id([item["id"] for item in items])
    for item in items:
        item["price_history"] = history.get(item["id"], [])
    return JSONResponse({"items": items})


@app.post("/api/wishlist")
async def create_wishlist_item(fields: dict = Body(default={})):
    created = db.create_wishlist_item(fields)
    return JSONResponse(created)


@app.patch("/api/wishlist/{item_id}")
async def update_wishlist_item(item_id: str, fields: dict = Body(...)):
    updated = db.update_wishlist_item(item_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Wunschlisten-Eintrag {item_id} nicht gefunden.")
    return JSONResponse(updated)


@app.delete("/api/wishlist/{item_id}", status_code=204)
async def delete_wishlist_item(item_id: str):
    deleted = db.delete_wishlist_item(item_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Wunschlisten-Eintrag {item_id} nicht gefunden.")
    return Response(status_code=204)


_WISHLIST_IMPORT_TEXT_COLUMNS = (("Titel", "title"), ("Team", "team"), ("Set", "set_name"), ("Notiz", "notes"))
_WISHLIST_IMPORT_MONEY_COLUMNS = (("Wunschpreis", "target_price"),)


@app.post("/api/wishlist/import")
async def import_wishlist_csv(file: UploadFile = File(...)):
    # Bulk-Import fuer die Wunschliste ueber eine CSV-Datei, gleiches Format
    # wie der bestehende CSV-Export (Semikolon-getrennt, Spalten per Name
    # erkannt) - anders als bei Karten (siehe import_purchases_csv()) ist das
    # hier unproblematisch, da Wunschlisten-Eintraege nie echte Karten sind.
    raw = await file.read()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise HTTPException(status_code=400, detail="CSV muss UTF-8-kodiert sein.") from exc

    reader = csv.DictReader(io.StringIO(text), delimiter=";")
    imported = []
    errors = []
    for line_no, row in enumerate(reader, start=2):
        fields = {}
        for header, field_name in _WISHLIST_IMPORT_TEXT_COLUMNS:
            value = (row.get(header) or "").strip()
            if value:
                fields[field_name] = value
        invalid_amount = False
        for header, field_name in _WISHLIST_IMPORT_MONEY_COLUMNS:
            value = (row.get(header) or "").strip()
            if not value:
                continue
            try:
                fields[field_name] = float(value.replace(",", "."))
            except ValueError:
                errors.append(f"Zeile {line_no}: Ungültiger Betrag bei „{header}“: „{value}“.")
                invalid_amount = True
                break
        if invalid_amount:
            continue
        if not fields:
            continue
        if not fields.get("title"):
            errors.append(f"Zeile {line_no}: „Titel“ darf nicht leer sein.")
            continue
        imported.append(db.create_wishlist_item(fields))
    return JSONResponse({"imported": len(imported), "errors": errors, "items": imported})


@app.get("/api/description-templates")
async def list_description_templates():
    return JSONResponse({"templates": db.list_description_templates()})


@app.post("/api/description-templates")
async def create_description_template(fields: dict = Body(default={})):
    created = db.create_description_template(fields)
    return JSONResponse(created)


@app.patch("/api/description-templates/{template_id}")
async def update_description_template(template_id: str, fields: dict = Body(...)):
    updated = db.update_description_template(template_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Textbaustein {template_id} nicht gefunden.")
    return JSONResponse(updated)


@app.delete("/api/description-templates/{template_id}", status_code=204)
async def delete_description_template(template_id: str):
    deleted = db.delete_description_template(template_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Textbaustein {template_id} nicht gefunden.")
    return Response(status_code=204)


@app.get("/api/business-expenses")
async def list_business_expenses():
    return JSONResponse({"expenses": db.list_business_expenses()})


@app.post("/api/business-expenses")
async def create_business_expense(fields: dict = Body(default={})):
    created = db.create_business_expense(fields)
    return JSONResponse(created)


@app.patch("/api/business-expenses/{expense_id}")
async def update_business_expense(expense_id: str, fields: dict = Body(...)):
    updated = db.update_business_expense(expense_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Ausgabe {expense_id} nicht gefunden.")
    return JSONResponse(updated)


@app.delete("/api/business-expenses/{expense_id}", status_code=204)
async def delete_business_expense(expense_id: str):
    deleted = db.delete_business_expense(expense_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Ausgabe {expense_id} nicht gefunden.")
    return Response(status_code=204)


@app.post("/api/cards/{card_id}/price-research")
async def create_price_research_entry(card_id: str, fields: dict = Body(default={})):
    if db.get_card(card_id) is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    entry = db.create_price_research_entry(card_id, fields)
    return JSONResponse(entry)


@app.delete("/api/price-research/{entry_id}", status_code=204)
async def delete_price_research_entry(entry_id: str):
    deleted = db.delete_price_research_entry(entry_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Preisrecherche-Eintrag {entry_id} nicht gefunden.")
    return Response(status_code=204)


@app.post("/api/cards/{card_id}/manual-sale")
async def create_manual_sale(card_id: str, fields: dict = Body(default={})):
    if db.get_card(card_id) is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    created = db.create_manual_sale(card_id, fields)
    try:
        db.zero_inventory_for_card(card_id)
    except Exception:
        # Gleiches Isolationsprinzip wie sync_ebay_sales()/
        # _create_default_inventory_item(): der Verkauf ist schon angelegt,
        # ein Supabase-Hiccup bei der Inventar-Nullung darf das nicht zu
        # einem 500 machen.
        logger.exception("Inventar-Nullung fuer Karte %s (manueller Verkauf) fehlgeschlagen", card_id)
    return JSONResponse(created)


@app.get("/api/manual-sales")
async def list_manual_sales():
    # Fuer die Versand-Checkliste (shipping.html): Sendungsverfolgungs-Felder
    # pro manuellem Verkauf, verknuepft client-seitig ueber card_id mit den
    # bereits ueber /api/cards geladenen "verkauft, nicht versendet"-Karten.
    return JSONResponse({"manual_sales": db.all_manual_sales()})


@app.patch("/api/manual-sales/{sale_id}")
async def update_manual_sale(sale_id: str, fields: dict = Body(...)):
    updated = db.update_manual_sale(sale_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Verkauf {sale_id} nicht gefunden.")
    return JSONResponse(updated)


@app.post("/api/manual-sales/{sale_id}/invoice")
async def issue_manual_sale_invoice(sale_id: str):
    # Zusaetzlich zum bestehenden Kleinbetragsrechnung-Beleg (siehe
    # upload_manual_sale_receipt()), nicht als Ersatz - vergibt eine
    # fortlaufende Rechnungsnummer (Paragraph 14 UStG). Idempotent: ein
    # bereits verrechneter Verkauf behaelt seine Nummer (db.issue_invoice()).
    issued = db.issue_invoice(sale_id)
    if issued is None:
        raise HTTPException(status_code=404, detail=f"Verkauf {sale_id} nicht gefunden.")
    return JSONResponse(issued)


@app.delete("/api/manual-sales/{sale_id}", status_code=204)
async def delete_manual_sale(sale_id: str):
    deleted = db.delete_manual_sale(sale_id)
    if deleted is None:
        raise HTTPException(status_code=404, detail=f"Verkauf {sale_id} nicht gefunden.")
    try:
        db.restore_inventory_for_card(deleted["card_id"])
    except Exception:
        # Gleiches Isolationsprinzip wie beim Anlegen (main.py's
        # create_manual_sale) - der Verkauf ist schon geloescht, ein
        # Supabase-Hiccup bei der Inventar-Wiederherstellung darf das
        # nicht mehr zu einem 500 machen.
        logger.exception("Inventar-Wiederherstellung fuer Karte %s fehlgeschlagen", deleted["card_id"])
    return Response(status_code=204)


@app.post("/api/manual-sales/{sale_id}/receipt")
async def upload_manual_sale_receipt(sale_id: str, file: UploadFile = File(...)):
    # Gleiches Muster wie upload_purchase_receipt() - Aufbewahrungspflicht
    # betrifft auch Verkaufsbelege bei manuellen Verkaeufen (Kleinanzeigen,
    # Vinted, ...), nicht nur Kaufbelege.
    sale = db.get_manual_sale(sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail=f"Verkauf {sale_id} nicht gefunden.")
    if file.content_type not in storage.RECEIPT_CONTENT_TYPES:
        raise HTTPException(
            status_code=400,
            detail="Beleg muss PDF, JPEG, PNG oder WebP sein.",
        )
    data = await file.read()
    if len(data) > _RECEIPT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="Beleg darf höchstens 10 MB groß sein.")

    old_path = sale.get("receipt_path")
    if old_path:
        try:
            storage.delete_sale_receipt(old_path)
        except Exception:
            logger.exception("Alten Beleg konnte nicht gelöscht werden für Verkauf %s", sale_id)

    try:
        object_path = storage.upload_sale_receipt(sale_id, file.content_type, data)
    except Exception as exc:
        raise HTTPException(
            status_code=502, detail=f"Beleg-Upload fehlgeschlagen: {type(exc).__name__}: {exc}"
        ) from exc

    updated = db.set_manual_sale_receipt(sale_id, object_path)
    return JSONResponse(_attach_manual_sale_receipt(updated))


@app.delete("/api/manual-sales/{sale_id}/receipt")
async def delete_manual_sale_receipt(sale_id: str):
    sale = db.get_manual_sale(sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail=f"Verkauf {sale_id} nicht gefunden.")
    old_path = sale.get("receipt_path")
    if old_path:
        try:
            storage.delete_sale_receipt(old_path)
        except Exception:
            logger.exception("Beleg konnte nicht gelöscht werden für Verkauf %s", sale_id)
    updated = db.set_manual_sale_receipt(sale_id, "")
    return JSONResponse(_attach_manual_sale_receipt(updated))


def _parse_date_like(value):
    # purchase_date is a plain "YYYY-MM-DD" date, sale_date a timestamptz
    # ISO string - both parse fine via fromisoformat() on Python 3.11+
    # (it accepts a trailing "Z" too), so one helper covers both instead
    # of two separate parsing paths.
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


# Schwellwert fuer die "seit X Tagen geplant, aber nicht veroeffentlicht"-
# Erinnerung auf ebay.html/im Dashboard - ein eBay-Angebot im nativen/App-
# Scheduling sollte planmaessig binnen weniger Tage veroeffentlicht werden;
# laenger "Geplant" deutet meist auf einen haengengebliebenen Job hin.
STALE_GEPLANT_DAYS = 3


def _days_pending(listing):
    reference = listing.get("scheduled_at") or listing.get("created_at")
    dt = _parse_date_like(reference)
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days


def _compute_statistics():
    # Factored out of GET /api/statistics so _sheets_tabs() (Google-Sheets-
    # Export) can reuse the exact same summary/monthly/rows computation
    # instead of re-implementing the profit/margin formula a second time.
    rows = db.statistics_rows()

    total_cost = total_revenue = realized_profit = 0.0
    total_shipping_charged = total_shipping_cost = total_ebay_fees = 0.0
    sold_cost = 0.0
    open_count = 0
    margins = []
    sale_prices = []
    holding_days_list = []
    monthly = {}
    enriched = []

    refunded_count = 0
    for row in rows:
        cost = row.get("cost")
        sale_price = row.get("sale_price")
        shipping_charged = row.get("shipping_charged") or 0
        shipping_cost = row.get("shipping_cost") or 0
        # Manuell erfassbar (s. db.EBAY_SALE_WRITABLE_FIELDS) - eBays Order-
        # API liefert die Verkaufsgebuehr nicht mit, daher 0 solange nichts
        # eingetragen ist; wirkt sich dann einfach nicht auf den Gewinn aus.
        ebay_fees = row.get("ebay_fees") or 0
        refunded = bool(row.get("refunded"))
        profit = margin_pct = holding_days = None

        # Retoure: Verkaufspreis und erhaltener Versand gingen an den Kaeufer
        # zurueck, zaehlen also nicht mehr als Umsatz - Einstandspreis,
        # gezahltes Porto und eBay-Gebuehren bleiben aber ein Verlust, da
        # eBay diese in der Regel nicht erstattet.
        revenue_contribution = 0.0 if refunded else float(sale_price or 0)
        shipping_charged_contribution = 0.0 if refunded else float(shipping_charged)

        if cost is not None:
            total_cost += float(cost)
        if sale_price is not None:
            total_revenue += revenue_contribution
            total_shipping_charged += shipping_charged_contribution
            total_shipping_cost += float(shipping_cost)
            total_ebay_fees += float(ebay_fees)
            if refunded:
                refunded_count += 1
            else:
                sale_prices.append(float(sale_price))
        if cost is not None and sale_price is not None:
            # Versand als durchlaufender Posten: eingenommener Versand zaehlt
            # als Einnahme, gezahltes Porto als Ausgabe - decken sie sich,
            # heben sie sich im Gewinn gegenseitig auf; nur eine Differenz
            # wirkt sich aus. eBay-Gebuehren mindern den Gewinn direkt.
            profit = revenue_contribution + shipping_charged_contribution - float(cost) - float(shipping_cost) - float(ebay_fees)
            realized_profit += profit
            sold_cost += float(cost)
            if cost:
                margin_pct = round((profit / float(cost)) * 100, 1)
                margins.append(margin_pct)
        elif cost is not None and sale_price is None:
            open_count += 1

        purchase_dt = _parse_date_like(row.get("purchase_date"))
        sale_dt = _parse_date_like(row.get("sale_date"))
        if purchase_dt and sale_dt:
            holding_days = (sale_dt.date() - purchase_dt.date()).days
            holding_days_list.append(holding_days)

        if row.get("sale_date"):
            month_key = str(row["sale_date"])[:7]
            bucket = monthly.setdefault(
                month_key, {"month": month_key, "count": 0, "revenue": 0.0, "profit": 0.0, "cost": 0.0}
            )
            bucket["count"] += 1
            bucket["revenue"] += revenue_contribution
            if profit is not None:
                bucket["profit"] += profit
                # cost ist nur bekannt, wenn profit berechnet werden konnte
                # (siehe oben) - fuer die Jahresuebersicht/Steuer-Export
                # neben Umsatz/Gewinn auch der Einstandspreis, monatsweise
                # nach Verkaufsdatum (Realisationsprinzip).
                bucket["cost"] += float(cost)

        enriched.append({**row, "profit": profit, "margin_pct": margin_pct, "holding_days": holding_days})

    monthly_list = sorted(monthly.values(), key=lambda m: m["month"])
    for bucket in monthly_list:
        bucket["revenue"] = round(bucket["revenue"], 2)
        bucket["profit"] = round(bucket["profit"], 2)
        bucket["cost"] = round(bucket["cost"], 2)

    summary = {
        "total_cost": round(total_cost, 2),
        "total_revenue": round(total_revenue, 2),
        "total_shipping_charged": round(total_shipping_charged, 2),
        "total_shipping_cost": round(total_shipping_cost, 2),
        "total_ebay_fees": round(total_ebay_fees, 2),
        "realized_profit": round(realized_profit, 2),
        "sold_count": len([r for r in enriched if r["profit"] is not None]),
        "open_count": open_count,
        "refunded_count": refunded_count,
        "avg_sale_price": round(sum(sale_prices) / len(sale_prices), 2) if sale_prices else None,
        "avg_margin_pct": round(sum(margins) / len(margins), 1) if margins else None,
        "avg_holding_days": round(sum(holding_days_list) / len(holding_days_list), 1) if holding_days_list else None,
        "roi_pct": round((realized_profit / sold_cost) * 100, 1) if sold_cost else None,
    }
    return {
        "summary": summary, "monthly": monthly_list, "rows": enriched,
        "team_ranking": _rank_statistics_by(enriched, "team"),
        "set_ranking": _rank_statistics_by(enriched, "set_name"),
        "platform_ranking": _rank_platforms(enriched),
        "channel_ranking": _rank_channels(enriched),
    }


def _rank_statistics_by(enriched_rows, field):
    # Team-/Set-Ranking auf der Statistik-Uebersichtsseite: gruppiert nur
    # tatsaechlich verkaufte Karten (profit bekannt) nach Team bzw. Set und
    # sortiert nach Gesamtgewinn absteigend - Karten ohne Team/Set-Angabe
    # (leerer String) fliessen nicht mit ein, da sie keine sinnvolle Gruppe
    # bilden wuerden.
    groups = {}
    for row in enriched_rows:
        if row["profit"] is None:
            continue
        key = (row.get(field) or "").strip()
        if not key:
            continue
        group = groups.setdefault(key, {"name": key, "count": 0, "revenue": 0.0, "profit": 0.0})
        group["count"] += 1
        group["revenue"] += float(row.get("sale_price") or 0)
        group["profit"] += row["profit"]
    ranked = sorted(groups.values(), key=lambda g: g["profit"], reverse=True)
    for group in ranked:
        group["revenue"] = round(group["revenue"], 2)
        group["profit"] = round(group["profit"], 2)
    return ranked


def _rank_by_avg(enriched_rows, field):
    # Gemeinsame Gruppierungslogik fuer Plattform- (Einkauf) und Kanal-
    # Auswertung (Verkauf): zeigt (anders als das Team-/Set-Ranking oben)
    # bewusst Durchschnittswerte statt Summen, weil die Frage hier "lohnt
    # sich X im Schnitt mehr als Y" ist, nicht "wo kam am meisten Gewinn
    # her" - eine Gruppe mit nur 2 Karten soll nicht allein durch mehr
    # Volumen vor einer mit 20 Karten liegen.
    groups = {}
    for row in enriched_rows:
        if row["profit"] is None:
            continue
        key = (row.get(field) or "").strip()
        if not key:
            continue
        group = groups.setdefault(key, {"name": key, "count": 0, "profit_sum": 0.0, "margin_sum": 0.0, "margin_count": 0})
        group["count"] += 1
        group["profit_sum"] += row["profit"]
        if row["margin_pct"] is not None:
            group["margin_sum"] += row["margin_pct"]
            group["margin_count"] += 1

    ranked = []
    for group in groups.values():
        avg_margin = group["margin_sum"] / group["margin_count"] if group["margin_count"] else None
        ranked.append({
            "name": group["name"],
            "count": group["count"],
            "avg_profit": round(group["profit_sum"] / group["count"], 2),
            "avg_margin_pct": round(avg_margin, 1) if avg_margin is not None else None,
        })
    ranked.sort(key=lambda g: g["avg_profit"], reverse=True)
    return ranked


def _rank_platforms(enriched_rows):
    # Einkaufsplattform (wo die Karte gekauft wurde) - nicht zu verwechseln
    # mit dem Verkaufskanal (siehe _rank_channels): eine ueber eBay
    # gekaufte, aber manuell weiterverkaufte Karte zaehlt hier bewusst
    # weiter unter "eBay", weil die Frage "lohnt sich Einkauf ueber
    # Plattform X" ist.
    return _rank_by_avg(enriched_rows, "platform")


def _rank_channels(enriched_rows):
    # Verkaufskanal (eBay oder manueller Kanal wie Kleinanzeigen/Vinted) -
    # Gegenstueck zu _rank_platforms() auf der Verkaufsseite.
    return _rank_by_avg(enriched_rows, "channel")


@app.get("/api/statistics")
async def get_statistics():
    return JSONResponse(_compute_statistics())


def _compute_euer(year):
    # EUER-Vorbereitung (Einnahmen-Ueberschuss-Rechnung, Paragraph 4 Abs. 3
    # EStG): Cash-Basis (Zufluss-Abfluss-Prinzip) statt der Realisations-
    # Zuordnung aus _compute_statistics()'s monthly-Bucket - jede Position
    # zaehlt im Jahr ihres eigenen Datums, genau wie beim bestehenden DATEV/
    # Lexoffice-Export auf statistics-sales.html (Wareneinkauf nach
    # purchase_date, Verkaufserloese/Versand/Gebuehren nach sale_date).
    # Versand/Gebuehren zaehlen unabhaengig von einer Retoure (siehe
    # _compute_statistics(), gleiche Begruendung: einmal gezahltes Porto/
    # eine einmal abgefuehrte Verkaufsgebuehr bekommt man idR nicht zurueck).
    year_str = str(year)
    sales_revenue = shipping_received = 0.0
    purchases_cost = shipping_paid = fees = 0.0
    for row in db.statistics_rows():
        purchase_date = row.get("purchase_date")
        cost = row.get("cost")
        if purchase_date and str(purchase_date)[:4] == year_str and cost is not None:
            purchases_cost += float(cost)

        sale_date = row.get("sale_date")
        sale_price = row.get("sale_price")
        if sale_date and str(sale_date)[:4] == year_str and sale_price is not None:
            if not row.get("refunded"):
                sales_revenue += float(sale_price)
                shipping_received += float(row.get("shipping_charged") or 0)
            shipping_paid += float(row.get("shipping_cost") or 0)
            fees += float(row.get("ebay_fees") or 0)

    other_expenses = sum(
        float(expense.get("amount") or 0)
        for expense in db.list_business_expenses()
        if expense.get("expense_date") and str(expense["expense_date"])[:4] == year_str
    )

    income = [
        {"label": "Verkaufserlöse", "amount": round(sales_revenue, 2)},
        {"label": "Erhaltene Versandkosten", "amount": round(shipping_received, 2)},
    ]
    expenses = [
        {"label": "Wareneinkauf", "amount": round(purchases_cost, 2)},
        {"label": "Versandkosten", "amount": round(shipping_paid, 2)},
        {"label": "Verkaufsgebühren", "amount": round(fees, 2)},
        {"label": "Sonstige Betriebsausgaben", "amount": round(other_expenses, 2)},
    ]
    income_total = round(sum(item["amount"] for item in income), 2)
    expense_total = round(sum(item["amount"] for item in expenses), 2)
    return {
        "year": year,
        "income": income,
        "income_total": income_total,
        "expenses": expenses,
        "expense_total": expense_total,
        "profit": round(income_total - expense_total, 2),
    }


@app.get("/api/euer")
async def get_euer(year: int | None = None):
    year = year or datetime.now(timezone.utc).year
    return JSONResponse(_compute_euer(year))


DASHBOARD_GOAL_METRICS = {"revenue", "profit"}


@app.get("/api/dashboard-goal")
async def get_dashboard_goal(year: int | None = None):
    year = year or datetime.now(timezone.utc).year
    goal = db.get_dashboard_goal(year)
    return JSONResponse(goal or {"year": year, "metric": "revenue", "amount": 0})


@app.put("/api/dashboard-goal")
async def set_dashboard_goal(fields: dict = Body(...)):
    year = fields.get("year") or datetime.now(timezone.utc).year
    metric = fields.get("metric", "revenue")
    if metric not in DASHBOARD_GOAL_METRICS:
        raise HTTPException(status_code=400, detail="metric muss 'revenue' oder 'profit' sein.")
    updated = db.set_dashboard_goal(year, {"metric": metric, "amount": fields.get("amount", 0)})
    return JSONResponse(updated)


def _expand_ebay_listings(listings):
    # Enriches each ebay_listings row with a thin card summary
    # (id/title/front_image_url), same purpose and batching approach as
    # _expand_purchase_items() above - one db.get_cards_by_ids() call for
    # the whole list instead of a db.get_card() round trip per listing.
    if not listings:
        return []
    card_ids = [l["card_id"] for l in listings]
    cards_by_id = {c["id"]: c for c in db.get_cards_by_ids(card_ids)}
    # Sale info is only looked up for listings that actually sold - keeps
    # the common case (no "Verkauft" rows in this page) free of an extra
    # query, same guard style as the card_ids/listing_ids checks above.
    sold_ids = [l["id"] for l in listings if l.get("status") == "Verkauft"]
    sales_by_listing = db.sales_by_listing_id(sold_ids) if sold_ids else {}
    # Verkauf ausserhalb von eBay, waehrend das Angebot hier noch lebt/
    # geplant ist - ebay.html zeigt dafuer einen Hinweis an (siehe
    # renderListingRow()), damit das Angebot zeitnah beendet werden kann.
    manual_sale_info = db.manual_sale_info_by_card_id(card_ids)
    # Fuer die Preisrecherche-Statusspalte (siehe ebay.html) - gleicher
    # +-20%-Schwellwert wie der Preis-Alarm auf der Kartenseite.
    price_research_info = db.price_research_by_card_ids(card_ids)
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
        sale = sales_by_listing.get(listing["id"])
        listing["sale_date"] = sale.get("sale_date") if sale else None
        listing["sale_price"] = sale.get("gross_price") if sale else None
        listing["sale_id"] = sale.get("id") if sale else None
        listing["tracking_number"] = sale.get("tracking_number") if sale else None
        listing["shipping_carrier"] = sale.get("shipping_carrier") if sale else None
        listing["delivered"] = bool(sale.get("delivered")) if sale else False
        listing["refunded"] = bool(sale.get("refunded")) if sale else False
        listing["manual_sale_channel"] = (manual_sale_info.get(listing["card_id"]) or {}).get("channel") or None
        listing["private_collection"] = bool(card.get("private_collection"))
        research = price_research_info.get(listing["card_id"])
        listing["price_research_avg"] = research["avg_price"] if research else None
        listing["price_research_count"] = research["count"] if research else 0
        expanded.append(listing)
    return expanded


def _listing_with_card(listing):
    return _expand_ebay_listings([listing])[0]


def _publish_listing(listing, scheduled_at=None, relist=False):
    """Shared publish flow for POST .../publish, publish-bulk, and the
    app-side scheduler (ebay_scheduler.py) - one place for "validate
    required aspects, resolve policies, create/update inventory item +
    offer, publish" so none of the three callers can drift apart.
    relist=True (only set by _relist_listing() below) marks that eBay just
    assigned a fresh listing_id (withdraw+republish) - resets listing_since
    and last_auto_relisted_at, unlike a plain edit-triggered republish of an
    already-live listing (same listing_id, both untouched)."""
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
        # eBay akzeptiert maximal 12 imageUrls pro Angebot (siehe
        # put_inventory_item's Kommentar) - Vorder-/Rueckseite zuerst,
        # weitere Fotos (z.B. Detailaufnahmen) danach, ueberzaehlige werden
        # stillschweigend abgeschnitten statt die Veroeffentlichung
        # abzubrechen.
        image_paths = [card.get("front_image_path"), card.get("back_image_path"), *_split_extra_image_paths(card)]
        image_urls = [storage.public_url(path) for path in image_paths if path][:12]

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
        now = datetime.now(timezone.utc).isoformat()
        updates.update({"status": "Veroeffentlicht", "published_at": now})
        # Erstveroeffentlichung (bisher keine ebay_listing_id) oder ein
        # Neu-Einstellen (relist=True) setzen "seit wann laeuft dieses
        # Listing" neu - eine reine Bearbeitung/Preisaenderung eines schon
        # laufenden Angebots (weder noch) laesst listing_since unangetastet.
        if relist or not listing.get("ebay_listing_id"):
            updates["listing_since"] = now
        if relist:
            updates["last_auto_relisted_at"] = now
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
        "description": fields.get("description") or ebay_listing.generate_description(card, fields.get("extra_note", "")),
        "condition": fields.get("condition", "NM"),
        "condition_id": fields.get("condition_id", "4000"),
        "grader": fields.get("grader", ""),
        "grade": fields.get("grade", ""),
        "listing_type": listing_type,
        "category_id": fields.get("category_id") or ebay_listing.CATEGORY_IDS[listing_type],
        "aspects": fields.get("aspects") or ebay_listing.build_aspects(card, listing_type),
        "price": fields.get("price", 0),
        "quantity": fields.get("quantity", 1),
        "auto_relist_after_days": fields.get("auto_relist_after_days") or None,
    }
    sku = ebay_listing.sku_for_card(card["card_no"])
    listing = db.create_ebay_listing(card_id, sku, row)
    listing["required_aspects"] = ebay_listing.required_aspects(listing_type)
    return JSONResponse(_listing_with_card(listing))


@app.post("/api/ebay/import")
async def import_ebay_listing(item_id: str = Form(...)):
    # Umgekehrte Richtung zu create_ebay_listing() oben: nicht eine im Tool
    # bereits vorhandene Karte auf eBay veroeffentlichen, sondern ein von
    # Hand (oder mit einem anderen Tool) direkt auf eBay erstelltes Angebot
    # per Artikelnummer nachtraeglich ins Tool holen - Karte, Fotos (per KI
    # erkannt wie beim Scan) und Angebots-Datensatz entstehen dabei neu.
    # Bewusst OHNE ebay_offer_id: die Inventory API (die dieses Tool fuer
    # Preisaenderung/Beenden nutzt) kann nur Angebote verwalten, die sie
    # selbst erstellt hat - ein fehlendes ebay_offer_id bei Status
    # "Veroeffentlicht" ist daher das Signal "extern verwaltet, im Tool nur
    # Nachverfolgung" (siehe _reject_if_externally_managed() unten).
    item_id = item_id.strip()
    if not item_id:
        raise HTTPException(status_code=400, detail="eBay-Artikelnummer darf nicht leer sein.")

    try:
        app_token = ebay_client.get_application_access_token()
        item = ebay_client.get_item_by_legacy_id(app_token, item_id)
    except ebay_client.EbayApiError as exc:
        raise HTTPException(
            status_code=502, detail=f"eBay-Angebot konnte nicht geladen werden: {exc}"
        ) from exc

    image_urls = item.get("image_urls") or []
    if len(image_urls) < 2:
        raise HTTPException(
            status_code=422,
            detail="Das eBay-Angebot hat weniger als 2 Fotos - Vorder-/Rückseite können nicht "
                   "automatisch übernommen werden.",
        )

    with tempfile.TemporaryDirectory(prefix="dcardslab_ebay_import_") as tmp_str:
        tmp = Path(tmp_str)
        front_path = tmp / "front.jpg"
        back_path = tmp / "back.jpg"
        try:
            for url, dest in ((image_urls[0], front_path), (image_urls[1], back_path)):
                photo_response = httpx.get(url, timeout=30)
                photo_response.raise_for_status()
                dest.write_bytes(photo_response.content)
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502, detail=f"eBay-Fotos konnten nicht heruntergeladen werden: {exc}"
            ) from exc

        fields = recognize_card(front_path=front_path, back_path=back_path)
        if not fields.get("title"):
            fields["title"] = item.get("title", "")

        duplicate = _find_duplicate_card_safe(fields)
        front_hash = _compute_image_hash_safe(front_path)
        if duplicate is None and front_hash:
            photo_duplicate = _find_duplicate_card_by_hash_safe(front_hash)
            if photo_duplicate:
                duplicate = {**photo_duplicate, "matched_by": "photo"}

        batch_id = db.create_batch(card_count=1)
        try:
            front_image_path = storage.upload_image(batch_id, 1, "front", front_path)
            back_image_path = storage.upload_image(batch_id, 1, "back", back_path)
            card_row = db.insert_card(
                batch_id, 1, fields, front_image_path, back_image_path, front_image_hash=front_hash,
            )
        except Exception as exc:
            db.update_batch_status(batch_id, "failed")
            raise HTTPException(
                status_code=502, detail=f"Karte anlegen fehlgeschlagen: {type(exc).__name__}: {exc}"
            ) from exc

    db.update_batch_status(batch_id, "ok")
    _create_default_inventory_item(card_row["id"], "", "")

    listing_type = ebay_listing.derive_listing_type(card_row)
    row = {
        "title": item.get("title") or ebay_listing.generate_title(card_row),
        "description": ebay_listing.generate_description(card_row),
        "condition": "NM",
        "condition_id": "4000",
        "listing_type": listing_type,
        "category_id": ebay_listing.CATEGORY_IDS[listing_type],
        "aspects": ebay_listing.build_aspects(card_row, listing_type),
        "price": item.get("price") or 0,
        "quantity": 1,
    }
    sku = ebay_listing.sku_for_card(card_row["card_no"])
    listing = db.create_ebay_listing(card_row["id"], sku, row)
    # listing_since: eBays eigenes Erstellungsdatum des Angebots (falls von
    # der Browse API geliefert - siehe ebay_client.get_item_by_legacy_id()),
    # sonst ersatzweise der Import-Zeitpunkt. Ohne das wuerde "Eingestellt
    # am" faelschlich den Import-Zeitpunkt statt des echten eBay-Startdatums
    # zeigen (Klaerung mit dem Nutzer).
    now_iso = datetime.now(timezone.utc).isoformat()
    listing = db.update_ebay_listing(listing["id"], {
        "status": "Veroeffentlicht", "ebay_listing_id": item_id,
        "published_at": now_iso, "listing_since": item.get("listing_since") or now_iso,
    })

    result = _attach_signed_urls(card_row)
    result["ebay_listing"] = _listing_with_card(listing)
    if duplicate:
        result["possible_duplicate"] = duplicate
    return JSONResponse(result)


@app.get("/api/ebay/listings")
async def list_ebay_listings(status: str | None = None, q: str | None = None):
    listings = _expand_ebay_listings(db.list_ebay_listings(status=status, q=q))
    return JSONResponse({"listings": listings})


@app.get("/api/ebay/listings/views")
async def ebay_listing_views(listing_ids: str):
    # Auf Abruf statt bei jedem GET /api/ebay/listings mitgeladen - die Sell
    # Analytics API ist ein eigener API-Aufruf mit eigenem Tageslimit, und
    # braucht den zusaetzlichen OAuth-Scope sell.analytics.readonly (siehe
    # README.md); ohne ihn soll ein einzelner fehlgeschlagener Aufruf nicht
    # die normale Angebotsliste (list_ebay_listings() oben) mitreissen.
    ids = [value for value in listing_ids.split(",") if value]
    try:
        token = ebay_client.get_access_token()
        views = ebay_client.get_listing_views(token, ids)
    except ebay_client.EbayNotAuthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ebay_client.EbayApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # In ebay_listings.last_known_views mitspeichern (Klaerung mit dem
    # Nutzer: soll bis zum naechsten Klick sichtbar bleiben, statt nach
    # jedem Seiten-Neuladen wieder bei "-" zu starten) - dafuer erst die
    # eBay-Listing-ID auf unsere interne ID abbilden, gleiches Muster wie
    # sync_ebay_sales()'s listings_by_item_id oben.
    if views:
        listings_by_item_id = {
            l["ebay_listing_id"]: l for l in db.list_ebay_listings() if l.get("ebay_listing_id")
        }
        for ebay_id, count in views.items():
            listing = listings_by_item_id.get(ebay_id)
            if listing:
                db.update_ebay_listing(listing["id"], {"last_known_views": count})
    return JSONResponse({"views": views})


@app.get("/api/ebay/listings/{listing_id}")
async def get_ebay_listing(listing_id: str):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    return JSONResponse(_listing_with_card(listing))


@app.post("/api/ebay/listings/{listing_id}/refresh-listing-since")
async def refresh_ebay_listing_since(listing_id: str):
    # Urspruenglich nur fuer importierte Angebote gedacht (die bekamen bei
    # import_ebay_listing() frueher immer den Import-Zeitpunkt statt eBays
    # echtem Startdatum). Betrifft aber genauso normale, ueber die API
    # eingestellte Angebote: die Migration, die listing_since eingefuehrt hat
    # (siehe supabase/schema.sql), fuellte die Spalte fuer bereits vor dieser
    # Migration veroeffentlichte Angebote mit published_at - das wird aber bei
    # JEDER Bearbeitung (z.B. Preisaenderung) neu gesetzt und ist daher nur
    # eine Naeherung, kein echtes Startdatum (Klaerung mit dem Nutzer, konkretes
    # Beispiel: eine per API eingestellte Karte, deren "Eingestellt am" nicht
    # zur tatsaechlichen eBay-Startzeit passte). Daher hier bewusst nicht auf
    # importierte/extern verwaltete Angebote eingeschraenkt - jedes Angebot mit
    # einer eBay-Artikelnummer kann sein echtes Startdatum per Browse-API
    # nachladen.
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    if not listing.get("ebay_listing_id"):
        raise HTTPException(status_code=400, detail="Angebot hat keine eBay-Artikelnummer.")
    try:
        app_token = ebay_client.get_application_access_token()
        item = ebay_client.get_item_by_legacy_id(app_token, listing["ebay_listing_id"])
    except ebay_client.EbayApiError as exc:
        raise HTTPException(
            status_code=502, detail=f"eBay-Angebot konnte nicht geladen werden: {exc}"
        ) from exc
    if not item.get("listing_since"):
        raise HTTPException(
            status_code=422, detail="eBay liefert für dieses Angebot kein Erstellungsdatum."
        )
    updated = db.update_ebay_listing(listing_id, {"listing_since": item["listing_since"]})
    return JSONResponse(_listing_with_card(updated))


def _is_externally_managed(listing):
    # Importierte Angebote (siehe import_ebay_listing()) haben nie ein
    # ebay_offer_id, weil sie nicht ueber die Inventory API erstellt wurden -
    # die kann daher auch keine Preisaenderung/Beendigung fuer sie ausfuehren
    # (ein update_offer/create_offer- bzw. withdraw_offer-Aufruf liefe ins
    # Leere oder legte faelschlich ein zweites Angebot an). Status
    # "Veroeffentlicht" + fehlendes ebay_offer_id ist das eindeutige Signal
    # dafuer - kein anderer Codepfad erzeugt diese Kombination.
    return listing.get("status") == "Veroeffentlicht" and not listing.get("ebay_offer_id")


_EXTERNALLY_MANAGED_DETAIL = (
    "Dieses Angebot wurde importiert und wird nicht vom Tool verwaltet - "
    "Änderungen bitte direkt auf eBay vornehmen."
)


def _update_ebay_listing_and_republish(listing_id, fields):
    # Shared by the single-listing PATCH and the bulk-price endpoint: a
    # change to an already-live listing (e.g. price) must be pushed back to
    # eBay, not just saved in our own DB.
    current = db.get_ebay_listing(listing_id)
    if current and _is_externally_managed(current):
        raise HTTPException(status_code=409, detail=_EXTERNALLY_MANAGED_DETAIL)
    updated = db.update_ebay_listing(listing_id, fields)
    if updated["status"] == "Veroeffentlicht":
        updated = _publish_listing(updated)
    return updated


@app.patch("/api/ebay/listings/{listing_id}")
async def update_ebay_listing(listing_id: str, fields: dict = Body(...)):
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    updated = _update_ebay_listing_and_republish(listing_id, fields)
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
    if _is_externally_managed(listing):
        raise HTTPException(status_code=409, detail=_EXTERNALLY_MANAGED_DETAIL)
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


@app.post("/api/ebay/listings/{listing_id}/end")
async def end_ebay_listing(listing_id: str):
    # Beendet ein LIVE-Angebot vorzeitig - z.B. wenn die Karte anderweitig
    # (Kleinanzeigen, Vinted, ...) verkauft wurde und schnellstmoeglich von
    # eBay runter muss. Nutzt denselben withdraw_offer()-Call wie das
    # Stornieren eines geplanten Angebots (unschedule_ebay_listing) - der
    # raeumt ein Offer bei eBay unabhaengig davon ab, ob es schon live oder
    # erst geplant ist.
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    if listing["status"] != "Veroeffentlicht":
        raise HTTPException(status_code=409, detail="Nur veröffentlichte Angebote können beendet werden.")
    if _is_externally_managed(listing):
        raise HTTPException(status_code=409, detail=_EXTERNALLY_MANAGED_DETAIL)
    if listing.get("ebay_offer_id"):
        try:
            token = ebay_client.get_access_token()
            ebay_client.withdraw_offer(token, listing["ebay_offer_id"])
        except ebay_client.EbayNotAuthorizedError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ebay_client.EbayApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    updated = db.update_ebay_listing(listing_id, {"status": "Beendet"})
    return JSONResponse(_listing_with_card(updated))


def _relist_listing(listing):
    """Beendet ein aktuell laufendes Angebot und veroeffentlicht es sofort
    neu (eBay vergibt dabei ein frisches Listing, was fuer die Sichtbarkeit/
    Suchranking relevant sein kann) - gemeinsame Basis fuer den manuellen
    "Neu einstellen"-Button (relist_ebay_listing()) und das automatische
    Re-Listing (_run_auto_relist_once()). Nutzt denselben withdraw_offer()-
    Call wie end_ebay_listing() oben, gefolgt vom bestehenden _publish_listing()-
    Ablauf (der - da ebay_offer_id schon gesetzt ist - update_offer() statt
    create_offer() nimmt, also dasselbe Offer wiederverwendet statt ein
    zweites anzulegen). relist=True an _publish_listing() setzt dabei sowohl
    listing_since (neue "Laeuft seit"-Anzeige) als auch last_auto_relisted_at
    (das "Neu eingestellt"-Badge, trotz Namens nicht mehr nur fuer die
    Automatik) neu - unabhaengig davon, ob manuell oder automatisch
    ausgeloest."""
    if listing.get("ebay_offer_id"):
        try:
            token = ebay_client.get_access_token()
            ebay_client.withdraw_offer(token, listing["ebay_offer_id"])
        except ebay_client.EbayNotAuthorizedError as exc:
            raise HTTPException(status_code=401, detail=str(exc)) from exc
        except ebay_client.EbayApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
    return _publish_listing(listing, relist=True)


@app.post("/api/ebay/listings/{listing_id}/relist")
async def relist_ebay_listing(listing_id: str):
    # Manueller Button (siehe Klaerung mit dem Nutzer: neben der Automation
    # explizit auch ein manueller Button gewuenscht) fuer ein unverkauftes,
    # laenger laufendes Angebot - z.B. um es nochmal "nach oben zu holen",
    # ohne auf den automatischen Re-Listing-Takt zu warten.
    listing = db.get_ebay_listing(listing_id)
    if listing is None:
        raise HTTPException(status_code=404, detail=f"eBay-Angebot {listing_id} nicht gefunden.")
    if listing["status"] != "Veroeffentlicht":
        raise HTTPException(status_code=409, detail="Nur veröffentlichte Angebote können neu eingestellt werden.")
    if _is_externally_managed(listing):
        raise HTTPException(status_code=409, detail=_EXTERNALLY_MANAGED_DETAIL)
    updated = _relist_listing(listing)
    return JSONResponse(_listing_with_card(updated))


def _run_auto_relist_once():
    """Ein Durchlauf des automatischen Re-Listings, periodisch aus
    ebay_scheduler.run_forever() aufgerufen. Zwei Bedingungen muessen
    zutreffen (Klaerung mit dem Nutzer: "Schalter + Markierung" statt eines
    einzelnen globalen An/Aus): der globale Schalter app_status.
    auto_relist_enabled UND die Markierung je Angebot (ebay_listings.
    auto_relist_after_days > 0, geprueft in db.list_listings_due_for_auto_relist()).
    Jedes Angebot einzeln try/except-isoliert wie bei _sync_ebay_returns_once().
    last_auto_relisted_at wird bei Erfolg schon von _relist_listing() selbst
    gesetzt (gilt seitdem auch fuer den manuellen Button); der Update-Call
    hier im finally-Block ist der eigentlich wichtige Teil fuer den
    Fehlerfall (gleiches Prinzip wie ebay_scheduler.py's
    mark_price_research_checked), damit ein dauerhaft fehlschlagendes
    Angebot nicht bei jedem Takt erneut versucht wird, sondern erst wieder
    nach auto_relist_after_days Tagen."""
    status = db.get_app_status() or {}
    if not status.get("auto_relist_enabled"):
        return 0
    try:
        due = db.list_listings_due_for_auto_relist()
    except Exception:
        logger.exception("Konnte faellige Angebote fuer automatisches Re-Listing nicht laden")
        return 0

    relisted = 0
    for listing in due:
        try:
            _relist_listing(listing)
            relisted += 1
        except Exception:
            logger.exception("Automatisches Re-Listing fehlgeschlagen fuer Angebot %s", listing.get("id"))
        finally:
            try:
                db.update_ebay_listing(listing["id"], {
                    "last_auto_relisted_at": datetime.now(timezone.utc).isoformat(),
                })
            except Exception:
                logger.exception("Konnte last_auto_relisted_at nicht speichern fuer Angebot %s", listing.get("id"))
    return relisted


@app.post("/api/ebay/listings/publish-bulk")
async def publish_ebay_listings_bulk(body: dict = Body(...)):
    results = []
    for listing_id in body.get("listing_ids", []):
        listing = db.get_ebay_listing(listing_id)
        if listing is None:
            results.append({"listing_id": listing_id, "status": "Fehler", "error": "Nicht gefunden."})
            continue
        if _is_externally_managed(listing):
            results.append({"listing_id": listing_id, "status": "Fehler", "error": _EXTERNALLY_MANAGED_DETAIL})
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


@app.post("/api/ebay/listings/price-bulk")
async def update_ebay_listings_price_bulk(body: dict = Body(...)):
    percent = body.get("percent")
    if percent is None:
        raise HTTPException(status_code=400, detail="percent ist erforderlich.")
    percent = float(percent)

    results = []
    for listing_id in body.get("listing_ids", []):
        listing = db.get_ebay_listing(listing_id)
        if listing is None:
            results.append({"listing_id": listing_id, "error": "Nicht gefunden."})
            continue
        try:
            # Nie negativ, auch bei einer sehr starken Rabattierung (< -100%).
            new_price = round(max(0.0, float(listing.get("price") or 0) * (1 + percent / 100)), 2)
            updated = _update_ebay_listing_and_republish(listing_id, {"price": new_price})
            results.append({"listing_id": listing_id, "price": updated["price"]})
        except HTTPException as exc:
            results.append({"listing_id": listing_id, "error": str(exc.detail)})
        except Exception as exc:
            # Gleiche Isolation wie publish-bulk oben - ein fehlgeschlagenes
            # Angebot darf den Rest der Auswahl nicht abbrechen.
            results.append({"listing_id": listing_id, "error": str(exc)})
    return JSONResponse({"results": results})


@app.get("/api/ebay/oauth/status")
async def ebay_oauth_status():
    try:
        response = httpx.get(f"{ebay_client.EBAY_OAUTH_SERVER_URL}/api/oauth/status", timeout=15)
    except httpx.HTTPError as exc:
        # Ohne dieses try/except wuerfe ein nicht erreichbarer oauth-Server
        # eine unbehandelte Exception, auf die FastAPI standardmaessig mit
        # reinem Text statt JSON antwortet - genau das hat zuvor schon die
        # Kartenseite blockiert (siehe price_research-Fix); Aufrufer wie das
        # Dashboard erwarten hier immer ein JSON-Objekt zurueck.
        return JSONResponse({"authorized": False, "error": str(exc)}, status_code=502)
    return JSONResponse(response.json(), status_code=response.status_code)


_AUTO_RE = re.compile(r"\bauto(?:graph(?:ed)?)?\b", re.IGNORECASE)
_PRINT_RUN_RE = re.compile(r"(\d{2,4})?/(\d{1,4})\b")


def _is_season_like(prefix_digits, suffix_digits):
    # season_year kann laut ai_card_recognition.py Formate wie "2024/25" oder
    # "24/25" annehmen - syntaktisch nicht von einer Auflagenangabe ("12/50")
    # zu unterscheiden. Eine Saison ist aber immer ein direkt aufeinander-
    # folgendes Jahrespaar (23/24, 99/00 beim Jahrhundertwechsel); eine echte
    # Auflage so gut wie nie.
    if prefix_digits is None or len(suffix_digits) != 2:
        return False
    return int(suffix_digits) == (int(prefix_digits[-2:]) + 1) % 100


def _variant_attrs(text):
    """Erkennt Auto(gramm)- und Auflagen-Hinweise ('/50') in einem Titel/einer
    Suchanfrage - rein textbasiert, da Karten kein eigenes Autogramm-Feld
    haben (siehe ebay_listing.py)."""
    text = text or ""
    print_run = None
    for match in _PRINT_RUN_RE.finditer(text):
        if _is_season_like(match.group(1), match.group(2)):
            continue
        print_run = match.group(2)
        break
    return bool(_AUTO_RE.search(text)), print_run


def _filter_price_research_results(query, results):
    # eBays Freitextsuche vermischt sonst Auto-/nummerierte Varianten (z.B.
    # "/50") mit der eigentlich gesuchten Basiskarte - deutlich teurere/
    # seltenere Varianten wuerden den Preisdurchschnitt verzerren. Eine
    # Auto- oder Auflagen-Angabe in einem Treffer zaehlt daher nur, wenn die
    # Suchanfrage selbst danach fragt (bei Auflage zusaetzlich exakt gleich).
    query_is_auto, query_print_run = _variant_attrs(query)
    filtered = []
    for item in results:
        item_is_auto, item_print_run = _variant_attrs(item.get("title", ""))
        if item_is_auto and not query_is_auto:
            continue
        if item_print_run and not query_print_run:
            continue
        if item_print_run and query_print_run and item_print_run != query_print_run:
            continue
        filtered.append(item)
    return filtered


def _own_listing_numeric_id(item_id):
    # Buy-Browse-Suchtreffer-IDs haben das Format "v1|<listingId>|<var>" -
    # der mittlere Teil ist dieselbe numerische ID, die publish_offer() beim
    # Veroeffentlichen als ebay_listing_id zurueckbekommt (siehe ebay_client.py).
    parts = (item_id or "").split("|")
    return parts[1] if len(parts) == 3 else None


def _mark_own_listings(results, own_listing_id):
    # Ohne eigene Verkaeuferinfo in den Suchtreffern (Buy-Browse-API liefert
    # dafuer keinen Verkaeufernamen mit) ist der Item-ID-Abgleich der einzige
    # Weg, das eigene aktive Angebot in den Ergebnissen zu erkennen - wichtig,
    # damit man es nicht versehentlich als "Marktpreis" fuer sich selbst uebernimmt.
    for item in results:
        item["is_own_listing"] = bool(own_listing_id) and _own_listing_numeric_id(item.get("item_id")) == own_listing_id
    return results


@app.get("/api/ebay/price-research")
async def ebay_price_research(q: str, own_listing_id: str | None = None):
    # Aktive Angebote (Buy/Browse API), nicht verkaufte Artikel - die dafuer
    # noetige Marketplace-Insights-API braucht eine gesonderte, von eBay
    # einzeln zu genehmigende Freigabe. Braucht keinen autorisierten
    # eBay-Account (Application-Token statt Nutzer-Token).
    try:
        token = ebay_client.get_application_access_token()
        results = ebay_client.search_active_listings(token, q)
    except ebay_client.EbayApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    results = _filter_price_research_results(q, results)
    results = _mark_own_listings(results, own_listing_id)
    return JSONResponse({"results": results})


@app.patch("/api/ebay/sales/{sale_id}")
async def update_ebay_sale(sale_id: str, fields: dict = Body(...)):
    updated = db.update_ebay_sale(sale_id, fields)
    if updated is None:
        raise HTTPException(status_code=404, detail=f"Verkauf {sale_id} nicht gefunden.")
    return JSONResponse(updated)


@app.post("/api/ebay/sales/{sale_id}/submit-tracking")
async def submit_ebay_sale_tracking(sale_id: str, fields: dict = Body(...)):
    # Uebermittelt Trackingnummer + Versanddienstleister an eBay (markiert
    # die Bestellung dort als versendet und benachrichtigt den Kaeufer
    # automatisch), speichert beides lokal und setzt zusaetzlich das
    # bestehende "Versendet"-Kaestchen der Karte - ein Schritt statt zwei.
    sale = db.get_ebay_sale(sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail=f"Verkauf {sale_id} nicht gefunden.")
    tracking_number = (fields.get("tracking_number") or "").strip()
    shipping_carrier = (fields.get("shipping_carrier") or "").strip()
    if not tracking_number or not shipping_carrier:
        raise HTTPException(status_code=400, detail="Trackingnummer und Versanddienstleister sind erforderlich.")
    try:
        token = ebay_client.get_access_token()
        ebay_client.submit_shipping_fulfillment(
            token, sale["ebay_order_id"], sale["ebay_line_item_id"], sale.get("quantity") or 1,
            tracking_number, shipping_carrier, datetime.now(timezone.utc).isoformat(),
        )
    except ebay_client.EbayNotAuthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ebay_client.EbayApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    updated = db.update_ebay_sale(sale_id, {"tracking_number": tracking_number, "shipping_carrier": shipping_carrier})
    if sale.get("card_id"):
        try:
            db.update_card(sale["card_id"], {"shipped": True})
        except Exception:
            # Tracking wurde bereits erfolgreich an eBay uebermittelt und
            # lokal gespeichert - ein fehlgeschlagenes Setzen des
            # "Versendet"-Kaestchens darf das nicht mehr zunichtemachen.
            logger.exception("Konnte 'Versendet' nicht setzen fuer Karte %s", sale.get("card_id"))
    return JSONResponse(updated)


@app.get("/api/ebay/sales/{sale_id}/shipping-address")
async def ebay_sale_shipping_address(sale_id: str):
    # Bewusst nicht gespeichert (siehe supabase/README.md-Datenschutz-
    # Ueberlegung dazu) - bei jedem Aufruf frisch von eBay geholt und nur
    # fuer die Anzeige/den Druck eines Adress-Etiketts durchgereicht.
    sale = db.get_ebay_sale(sale_id)
    if sale is None:
        raise HTTPException(status_code=404, detail=f"Verkauf {sale_id} nicht gefunden.")
    try:
        token = ebay_client.get_access_token()
        order = ebay_client.get_order(token, sale["ebay_order_id"])
    except ebay_client.EbayNotAuthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ebay_client.EbayApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    instructions = order.get("fulfillmentStartInstructions") or []
    ship_to = (instructions[0].get("shippingStep") or {}).get("shipTo") if instructions else None
    if not ship_to:
        raise HTTPException(status_code=404, detail="Keine Versandadresse in der eBay-Bestellung gefunden.")
    # Pseudonymer eBay-Handle (anders als die Adresse oben durchaus dauerhaft
    # gespeichert, siehe buyer_username-Migration) - hier als Backfill fuer
    # Verkaeufe, die vor Einfuehrung des Felds synchronisiert wurden, ohne
    # auf den naechsten sync_ebay_sales()-Lauf warten zu muessen.
    buyer_username = (order.get("buyer") or {}).get("username") or ""
    if buyer_username:
        try:
            db.set_ebay_sale_buyer_username(sale_id, buyer_username)
        except Exception:
            logger.exception("Konnte buyer_username nicht nachtragen fuer Verkauf %s", sale_id)

    # Sendungsverfolgung/Versandstatus ebenfalls hier auf Anfrage nachladen,
    # nicht nur ueber den periodischen Hintergrund-Sync (sync_ebay_sales) -
    # deckt den Fall ab, dass direkt im eBay Seller Hub (und ggf. ohne
    # Trackingnummer, z. B. Warenpost/Brief) versendet wurde: die Bestellung
    # gilt bei eBay dann trotzdem als FULFILLED.
    tracking_number = sale.get("tracking_number") or ""
    shipping_carrier = sale.get("shipping_carrier") or ""
    shipped = order.get("orderFulfillmentStatus") == "FULFILLED"
    if shipped:
        try:
            fulfillments = ebay_client.list_shipping_fulfillments(token, sale["ebay_order_id"])
        except ebay_client.EbayApiError:
            logger.exception(
                "Sendungsverfolgung konnte nicht von eBay geladen werden für Bestellung %s", sale["ebay_order_id"],
            )
            fulfillments = []
        for fulfillment in fulfillments:
            tracked_ids = {li.get("lineItemId") for li in fulfillment.get("lineItems") or []}
            if sale.get("ebay_line_item_id") in tracked_ids:
                tracking_number = fulfillment.get("trackingNumber", "") or tracking_number
                shipping_carrier = fulfillment.get("shippingCarrierCode", "") or shipping_carrier
                break
        update_fields = {}
        if tracking_number and tracking_number != sale.get("tracking_number"):
            update_fields["tracking_number"] = tracking_number
        if shipping_carrier and shipping_carrier != sale.get("shipping_carrier"):
            update_fields["shipping_carrier"] = shipping_carrier
        if update_fields:
            try:
                db.update_ebay_sale(sale_id, update_fields)
            except Exception:
                logger.exception("Konnte Sendungsverfolgung nicht speichern für Verkauf %s", sale_id)
        if sale.get("card_id"):
            try:
                db.update_card(sale["card_id"], {"shipped": True})
            except Exception:
                logger.exception("Konnte 'Versendet' nicht setzen für Karte %s", sale.get("card_id"))

    return JSONResponse({
        **ship_to, "buyer_username": buyer_username,
        "tracking_number": tracking_number, "shipping_carrier": shipping_carrier, "shipped": shipped,
    })


def _sync_ebay_sales_once():
    """Shared sync logic for POST /api/ebay/sync-sales and the periodic
    background sync (see ebay_scheduler.run_sales_sync_once()) - kept as one
    function so the two callers can't drift apart, same pattern as
    _publish_listing() above. Raises ebay_client.EbayNotAuthorizedError/
    EbayApiError on failure (callers translate those to HTTP status codes or
    just log+skip, see the two call sites). Returns (synced, skipped,
    newly_synced) where newly_synced is the list of {"card_id", "listing",
    "sale_fields"} dicts for sales upserted during this run - used by the
    email-notification background job to know what to mail about."""
    token = ebay_client.get_access_token()
    cursor = db.latest_sale_sync_cursor()
    since = cursor or (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    orders = ebay_client.get_orders(token, since)

    all_listings = db.list_ebay_listings()
    listings_by_sku = {l["sku"]: l for l in all_listings}
    # Fallback fuer importierte Angebote (main.py's import_ebay_listing()) -
    # die haben nie eine echte SKU auf eBay selbst, siehe
    # ebay_listing.match_sale_line_item()'s Kommentar.
    listings_by_item_id = {l["ebay_listing_id"]: l for l in all_listings if l.get("ebay_listing_id")}

    synced = skipped = 0
    newly_synced = []
    for order in orders:
        order_id = order.get("orderId", "")
        # Lazily geladen und pro Bestellung gecacht (nicht pro Line Item) -
        # eine Bestellung mit mehreren Karten braucht sonst denselben Aufruf
        # mehrfach. None = noch nicht versucht, [] = versucht, nichts
        # gefunden bzw. Ladefehler (siehe except unten).
        fulfillments = None
        order_line_items = order.get("lineItems", [])
        for line_item in order_line_items:
            listing = ebay_listing.match_sale_line_item(line_item, listings_by_sku, listings_by_item_id)
            if listing is None:
                skipped += 1
                continue
            delivery_cost = (line_item.get("deliveryCost") or {}).get("shippingCost") or {}
            shipping_charged = float(delivery_cost.get("value", 0) or 0)
            if not shipping_charged and len(order_line_items) == 1:
                # eBay meldet den vom Kaeufer gezahlten Versand manchmal nur
                # auf Bestellungs- statt auf Artikelebene (deliveryCost auf
                # dem Line Item bleibt dann 0, obwohl tatsaechlich Versand
                # gewaehlt/bezahlt wurde) - bei genau einer Position in der
                # Bestellung ist die Zuordnung trotzdem eindeutig, daher hier
                # als Fallback auf pricingSummary.deliveryCost der Bestellung.
                order_delivery_cost = (order.get("pricingSummary") or {}).get("deliveryCost") or {}
                shipping_charged = float(order_delivery_cost.get("value", 0) or 0)
            quantity = line_item.get("quantity", 1)
            line_item_cost = (line_item.get("lineItemCost") or {}).get("value")
            if line_item_cost is not None:
                # lineItemCost ist der Preis PRO EINHEIT, total dagegen bei
                # manchen Bestellungen (beobachtet, entgegen eBays eigener
                # Doku) der Gesamtbetrag INKLUSIVE Versand - lineItemCost *
                # quantity liefert daher zuverlässig den reinen Artikelpreis
                # ohne Versand (= eBays eigene "Zwischensumme"-Anzeige).
                gross_price = float(line_item_cost) * quantity
            else:
                # Fallback fuer den unwahrscheinlichen Fall, dass eBay
                # lineItemCost mal nicht mitliefert - besser eine grobe
                # Naeherung (ggf. inkl. Versand) als ein Verkaufspreis von 0.
                gross_price = float((line_item.get("total") or {}).get("value", 0) or 0)
            sale_fields = {
                "listing_id": listing["id"], "card_id": listing["card_id"],
                "ebay_order_id": order_id,
                "ebay_line_item_id": line_item.get("lineItemId", ""),
                "sale_date": order.get("creationDate"),
                "quantity": quantity,
                "gross_price": gross_price,
                # Vom Kaeufer gezahlter Versand - durchlaufender Posten, siehe
                # shipping_cost (manuell, tatsaechliches Porto) im Gewinn.
                "shipping_charged": shipping_charged,
                # Pseudonymer eBay-Handle fuer die Kaeufer-Historie (siehe
                # statistics-sales.html) - bewusst kein Klarname/Adresse.
                "buyer_username": (order.get("buyer") or {}).get("username") or "",
            }
            # Deckt den Fall ab, dass direkt im eBay Seller Hub versendet
            # wurde statt ueber dieses Tool - Trackingdaten trotzdem
            # automatisch uebernehmen und die Karte als versendet markieren,
            # statt dass sie faelschlich in der Versand-Checkliste haengen
            # bleibt. Die Karte gilt bereits als versendet, sobald eBay die
            # Bestellung als FULFILLED fuehrt, auch wenn keine passende
            # Sendungsverfolgung gefunden wird (z. B. Warenpost/Brief ohne
            # Tracking - bei eBay trotzdem als versendet erfasst). Nur bei
            # bereits abgeschlossener Bestellung nachgeladen, um unnoetige
            # eBay-Aufrufe bei den meisten (offenen) Sync-Laeufen zu vermeiden.
            order_fulfilled = order.get("orderFulfillmentStatus") == "FULFILLED"
            if order_fulfilled:
                if fulfillments is None:
                    try:
                        fulfillments = ebay_client.list_shipping_fulfillments(token, order_id)
                    except ebay_client.EbayApiError:
                        logger.exception(
                            "Sendungsverfolgung konnte nicht von eBay geladen werden für Bestellung %s", order_id,
                        )
                        fulfillments = []
                line_item_id = line_item.get("lineItemId", "")
                for fulfillment in fulfillments:
                    tracked_ids = {li.get("lineItemId") for li in fulfillment.get("lineItems") or []}
                    if line_item_id in tracked_ids:
                        sale_fields["tracking_number"] = fulfillment.get("trackingNumber", "")
                        sale_fields["shipping_carrier"] = fulfillment.get("shippingCarrierCode", "")
                        break
            db.upsert_ebay_sale(sale_fields)
            db.update_ebay_listing(listing["id"], {"status": "Verkauft"})
            if order_fulfilled:
                try:
                    db.update_card(listing["card_id"], {"shipped": True})
                except Exception:
                    logger.exception("Konnte 'Versendet' nicht setzen für Karte %s", listing["card_id"])
            try:
                db.zero_inventory_for_card(listing["card_id"])
            except Exception:
                # Same isolation principle as elsewhere in this file (e.g.
                # _create_default_inventory_item): the sale itself is
                # already recorded above, so a Supabase hiccup here must
                # not turn an otherwise-successful sync into a 500 - it
                # just leaves the inventory row(s) to be zeroed manually.
                logger.exception("Inventar-Nullung für Karte %s fehlgeschlagen", listing["card_id"])
            synced += 1
            newly_synced.append({"card_id": listing["card_id"], "listing": listing, "sale_fields": sale_fields})
    return synced, skipped, newly_synced


@app.post("/api/ebay/sync-sales")
async def sync_ebay_sales():
    try:
        synced, skipped, _ = _sync_ebay_sales_once()
    except ebay_client.EbayNotAuthorizedError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc
    except ebay_client.EbayApiError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse({"synced": synced, "skipped": skipped})


def _notify_new_sales(newly_synced):
    """Callback fuer ebay_scheduler.run_forever()'s on_new_sales - schickt
    eine zusammenfassende E-Mail sowie (falls Geraete registriert sind) eine
    Web-Push-Benachrichtigung ueber neu synchronisierte eBay-Verkaeufe aus
    dem periodischen Hintergrund-Sync (nicht dem manuellen Button, siehe
    sync_ebay_sales() oben - dort waere eine Benachrichtigung fuer eine
    selbst ausgeloeste Aktion unnoetig). Beide Kanäle teilen sich den
    gleichen "notify_on_sale"-Schalter (settings.html) - Push ist zusaetzlich
    an ein bestehendes Geraete-Abo gebunden (POST /api/push/subscribe), ohne
    eigenen zweiten An/Aus-Schalter. Fehler werden hier abgefangen statt an
    den Scheduler durchgereicht, damit ein SMTP-/Push-Problem den
    eigentlichen Sync nicht beeintraechtigt."""
    try:
        settings = db.get_app_status() or {}
        if not settings.get("notify_on_sale"):
            return
    except Exception:
        logger.exception("Benachrichtigungseinstellungen konnten nicht geladen werden")
        return
    try:
        subject, body = email_notify.format_sale_notification(newly_synced)
        email_notify.send_email(settings, subject, body)
    except Exception:
        logger.exception("E-Mail-Benachrichtigung fuer neue Verkaeufe fehlgeschlagen")
    try:
        if push_notify.is_configured():
            subject, body = email_notify.format_sale_notification(newly_synced)
            push_notify.send_push_to_all(subject, body, url="/ebay.html")
    except Exception:
        logger.exception("Push-Benachrichtigung fuer neue Verkaeufe fehlgeschlagen")


def _sync_ebay_returns_once():
    """Automatische Retouren-Erkennung: setzt das bestehende manuelle
    "Als Rückerstattung/Retoure markieren"-Kästchen (ebay_sales.refunded)
    automatisch, sobald eBay eine abgeschlossene Rückerstattung zu einem
    bereits synchronisierten Verkauf meldet - rein lesend/erkennend, kein
    Rückgabe-Management (annehmen/ablehnen) im Tool (siehe Klärung mit dem
    Nutzer). Nur von ebay_scheduler.run_returns_sync_once() aufgerufen (kein
    manueller Button wie beim Verkaufs-Sync) - dessen try/except fängt
    Fehler hier auf, ein Fehler beim einzelnen Retouren-Eintrag wird
    trotzdem schon hier isoliert abgefangen, damit ein defekter Eintrag
    nicht den ganzen Batch abbricht."""
    token = ebay_client.get_access_token()
    status = db.get_app_status() or {}
    since = status.get("last_returns_sync_at") or (datetime.now(timezone.utc) - timedelta(days=90)).isoformat()
    returns = ebay_client.get_return_requests(token, since)

    checked = matched = 0
    for return_record in returns:
        checked += 1
        try:
            order_id = return_record.get("orderId") or ""
            if not order_id:
                continue
            # Nur als abgeschlossene Rueckerstattung gewertet, wenn eBay
            # tatsaechlich mindestens einen Refund-Eintrag meldet - robuster
            # als sich auf einen bestimmten state-String zu verlassen, dessen
            # genaue Werte in eBays Post-Order API unverifiziert sind (siehe
            # ebay_client.get_return_requests()).
            refunds = ((return_record.get("refundInfo") or {}).get("refunds")) or []
            if not refunds:
                continue
            sale = db.get_ebay_sale_by_order_id(order_id)
            if sale is None or sale.get("refunded"):
                continue
            db.update_ebay_sale(sale["id"], {"refunded": True})
            matched += 1
        except Exception:
            logger.exception("Retouren-Abgleich fehlgeschlagen für Return %s", return_record.get("returnId"))
    return checked, matched


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

    inventory_items = _expand_inventory_items(db.list_inventory())
    inventory_headers = ["sku", "card_title", "quantity", "condition", "location", "notes", "value", "ebay_status"]
    inventory_rows = []
    for item in inventory_items:
        row = dict(item, card_title=item.get("card", {}).get("title", ""))
        inventory_rows.append([str(row.get(h, "") or "") for h in inventory_headers])
    inventory_value = sum(item["value"] for item in inventory_items if item["value"] is not None)

    wishlist_headers = ["title", "team", "set_name", "target_price", "notes"]
    wishlist_rows = [[str(item.get(h, "") or "") for h in wishlist_headers] for item in db.list_wishlist_items()]

    private_headers = ["title", "team", "set_name", "card_number", "tags"]
    private_rows = [
        [str(c.get(h, "") or "") for h in private_headers] for c in db.list_private_collection_cards()
    ]

    stats_summary = _compute_statistics()["summary"]
    statistics_rows = [
        ["Gesamt-Einkaufswert", str(stats_summary["total_cost"])],
        ["Gesamtumsatz", str(stats_summary["total_revenue"])],
        ["Realisierter Gewinn", str(stats_summary["realized_profit"])],
        ["ROI (verkauft)", "" if stats_summary["roi_pct"] is None else str(stats_summary["roi_pct"])],
        ["Verkaufte Karten", str(stats_summary["sold_count"])],
        ["Offene Karten (Kauf, unverkauft)", str(stats_summary["open_count"])],
        ["Durchschnittliche Marge (%)", "" if stats_summary["avg_margin_pct"] is None else str(stats_summary["avg_margin_pct"])],
        ["Durchschnittliche Liegedauer (Tage)", "" if stats_summary["avg_holding_days"] is None else str(stats_summary["avg_holding_days"])],
    ]

    stale_geplant = sum(
        1 for listing in listings
        if listing.get("status") == "Geplant" and (_days_pending(listing) or 0) > STALE_GEPLANT_DAYS
    )
    dashboard_rows = [
        ["Aktueller Inventarwert", str(round(inventory_value, 2))],
        ["Verkaufte Karten (gesamt)", str(stats_summary["sold_count"])],
        ["Offene Karten (Kauf, unverkauft)", str(stats_summary["open_count"])],
        ["Realisierter Gewinn (gesamt)", str(stats_summary["realized_profit"])],
        [f"'Geplant' seit über {STALE_GEPLANT_DAYS} Tagen ohne Veröffentlichung", str(stale_geplant)],
    ]

    sync_info = (["Information", "Wert"], [
        ["Quelle", "DCardsLab Supabase"],
        ["Synchronisiert", datetime.now(timezone.utc).isoformat(timespec="seconds")],
        ["Richtung", "Supabase -> Google Sheets"],
        ["Hinweis", "Supabase ist die Master-Datenbank; Sheets ist die externe Auswertungsansicht."],
    ])

    return {
        "Karten": (card_headers, card_rows), "Käufe": (purchase_headers, purchase_rows),
        "eBay": (ebay_headers, ebay_rows), "Inventar": (inventory_headers, inventory_rows),
        "Wunschliste": (wishlist_headers, wishlist_rows),
        "Private Sammlung": (private_headers, private_rows),
        "Statistiken": (["Kennzahl", "Wert"], statistics_rows),
        "Dashboard": (["Kennzahl", "Wert"], dashboard_rows),
        "Sync_Info": sync_info,
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
    now = datetime.now(timezone.utc)
    filename = f"dcardslab-backup-{now.date().isoformat()}.zip"
    try:
        db.record_backup_downloaded(now.isoformat())
    except Exception:
        # Der Zeitstempel dient nur der Dashboard-Anzeige - ein fehlgeschlagenes
        # Speichern darf den eigentlichen Backup-Download nicht verhindern.
        logger.exception("Backup-Zeitstempel konnte nicht gespeichert werden")
    return Response(
        content=data, media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@app.post("/api/backup/run-now")
async def run_backup_now():
    # Manueller Trigger (Einstellungen-Button) fuer das automatische Backup -
    # etwa direkt nach dem Anlegen des Storage-Buckets, ohne eine Woche oder
    # einen Server-Neustart abzuwarten. Nutzt dieselbe run_backup_now()
    # wie der Hintergrund-Loop, nur ohne dessen _is_backup_due()-Gate.
    try:
        backup.run_backup_now()
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    status = db.get_app_status() or {}
    return JSONResponse({"last_auto_backup_at": status.get("last_auto_backup_at")})


@app.post("/api/backup/restore")
async def restore_backup(file: UploadFile = File(...)):
    # Spielt ein zuvor per GET /api/backup (oder das automatische
    # Hintergrund-Backup) erzeugtes ZIP wieder ein - siehe
    # backup.restore_backup_zip() fuer das Merge-statt-Wipe-Verhalten.
    data = await file.read()
    try:
        summary = backup.restore_backup_zip(data)
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=400, detail="Keine gültige ZIP-Datei.") from exc
    return JSONResponse(summary)


@app.get("/api/portfolio/snapshots")
async def list_portfolio_snapshots():
    return JSONResponse({"snapshots": db.list_portfolio_snapshots()})


@app.post("/api/portfolio/snapshot-now")
async def create_portfolio_snapshot_now():
    # Manueller Trigger, gleiches Muster wie run_backup_now() oben - nutzt
    # dieselbe record_snapshot_now() wie der woechentliche Hintergrund-Loop,
    # nur ohne dessen _is_snapshot_due()-Gate.
    try:
        snapshot = portfolio.record_snapshot_now(_compute_portfolio_value)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return JSONResponse(snapshot)


@app.get("/api/app-status")
async def app_status():
    status = db.get_app_status() or {}
    return JSONResponse({
        "last_backup_at": status.get("last_backup_at"),
        "last_auto_backup_at": status.get("last_auto_backup_at"),
        "low_stock_threshold": status.get("low_stock_threshold") or 0,
        "price_alert_threshold_pct": status.get("price_alert_threshold_pct") or 20,
        "page_size": status.get("page_size") or 40,
        "view_density": status.get("view_density") or "comfort",
        "stale_listing_min_days": status.get("stale_listing_min_days") or STALE_LISTING_MIN_DAYS,
        "stale_listing_check_days": status.get("stale_listing_check_days") or STALE_LISTING_CHECK_MAX_AGE_DAYS,
        "stale_wishlist_min_days": status.get("stale_wishlist_min_days") or STALE_WISHLIST_MIN_DAYS,
        "csv_delimiter": status.get("csv_delimiter") or ";",
        "kleinunternehmer_prev_year_threshold": status.get("kleinunternehmer_prev_year_threshold") or KLEINUNTERNEHMER_PREV_YEAR_THRESHOLD,
        "kleinunternehmer_current_year_threshold": status.get("kleinunternehmer_current_year_threshold") or KLEINUNTERNEHMER_CURRENT_YEAR_THRESHOLD,
        "kleinunternehmer_hint_enabled": status.get("kleinunternehmer_hint_enabled", True),
        "failed_login_log": status.get("failed_login_log") or [],
        "auto_relist_enabled": bool(status.get("auto_relist_enabled")),
        "sender_address": status.get("sender_address") or "",
        "activity_cleared_at": status.get("activity_cleared_at"),
        "notify_on_sale": bool(status.get("notify_on_sale")),
        "notify_on_reminders": bool(status.get("notify_on_reminders")),
        "smtp_host": status.get("smtp_host") or "",
        "smtp_port": status.get("smtp_port"),
        "smtp_username": status.get("smtp_username") or "",
        "smtp_from": status.get("smtp_from") or "",
        "smtp_to": status.get("smtp_to") or "",
        "smtp_use_tls": status.get("smtp_use_tls", True),
        # Das gespeicherte Passwort wird nie im Klartext zurueckgegeben -
        # settings.html zeigt stattdessen nur, ob eins hinterlegt ist, damit
        # das Eingabefeld beim Speichern leer bleiben kann, ohne ein
        # vorhandenes Passwort zu loeschen (siehe update_notification_settings()).
        "smtp_password_set": bool(status.get("smtp_password")),
    })


@app.put("/api/notification-settings")
async def update_notification_settings(fields: dict = Body(...)):
    # smtp_password fehlt im Payload, wenn settings.html es leer laesst
    # (Frontend-Konvention: "leer = unveraendert", da das echte Passwort nie
    # zurueckgegeben wird, siehe smtp_password_set oben) - db.save_notification_
    # settings() aendert dann diese Spalte nicht.
    row = dict(fields)
    if not row.get("smtp_password"):
        row.pop("smtp_password", None)
    updated = db.save_notification_settings(row)
    return JSONResponse(updated)


@app.post("/api/notification-settings/test")
async def send_test_notification():
    settings = db.get_app_status() or {}
    try:
        email_notify.send_email(
            settings, "DCardsLab: Test-E-Mail",
            "Dies ist eine Test-E-Mail von DCardsLab - die SMTP-Einstellungen funktionieren.",
        )
    except email_notify.EmailNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except email_notify.SMTPAuthenticationHintError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"E-Mail-Versand fehlgeschlagen: {type(exc).__name__}: {exc}") from exc
    return JSONResponse({"sent": True})


@app.get("/api/push/vapid-public-key")
async def get_push_vapid_public_key():
    # settings.html braucht den oeffentlichen Schluessel fuer
    # PushManager.subscribe({applicationServerKey: ...}) - der private
    # Schluessel verlaesst push_notify.py nie.
    return JSONResponse({
        "public_key": push_notify.VAPID_PUBLIC_KEY,
        "configured": push_notify.is_configured(),
    })


@app.post("/api/push/subscribe")
async def subscribe_push(fields: dict = Body(...)):
    endpoint = fields.get("endpoint")
    keys = fields.get("keys") or {}
    if not endpoint or not keys.get("p256dh") or not keys.get("auth"):
        raise HTTPException(status_code=400, detail="Unvollständige Push-Subscription.")
    db.save_push_subscription(endpoint, keys["p256dh"], keys["auth"])
    return JSONResponse({"subscribed": True})


@app.post("/api/push/unsubscribe")
async def unsubscribe_push(fields: dict = Body(...)):
    endpoint = fields.get("endpoint")
    if endpoint:
        db.delete_push_subscription(endpoint)
    return JSONResponse({"unsubscribed": True})


@app.post("/api/push/test")
async def send_test_push():
    try:
        sent = push_notify.send_push_to_all(
            "DCardsLab: Test-Push",
            "Dies ist eine Test-Benachrichtigung von DCardsLab - Web-Push funktioniert.",
        )
    except push_notify.PushNotConfiguredError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return JSONResponse({"sent": sent})


@app.post("/api/app-status/clear-activity")
async def clear_activity():
    # "Letzte Aktivitaet" auf dem Dashboard ist kein gespeichertes Protokoll,
    # sondern wird live aus Kaeufen/Verkaeufen/Karten berechnet - "leeren"
    # merkt sich daher nur einen Zeitpunkt, ab dem wieder Ereignisse
    # angezeigt werden, statt tatsaechlich Daten zu loeschen.
    now = datetime.now(timezone.utc).isoformat()
    updated = db.set_activity_cleared(now)
    return JSONResponse(updated)


@app.put("/api/sender-address")
async def set_sender_address(fields: dict = Body(...)):
    updated = db.set_sender_address(fields.get("address", ""))
    return JSONResponse(updated)


@app.put("/api/low-stock-threshold")
async def set_low_stock_threshold(fields: dict = Body(...)):
    threshold = fields.get("threshold")
    if not isinstance(threshold, int) or isinstance(threshold, bool) or threshold < 0:
        raise HTTPException(status_code=400, detail="threshold muss eine Ganzzahl >= 0 sein.")
    updated = db.set_low_stock_threshold(threshold)
    return JSONResponse(updated)


@app.put("/api/auto-relist-enabled")
async def set_auto_relist_enabled(fields: dict = Body(...)):
    # Globaler Schalter fuer das automatische Re-Listing (settings.html) -
    # wirkt nur zusammen mit dem per-Angebot-Schalter auto_relist_after_days
    # (siehe _run_auto_relist_once() und db.list_listings_due_for_auto_relist()).
    updated = db.set_auto_relist_enabled(bool(fields.get("enabled")))
    return JSONResponse(updated)


@app.put("/api/price-alert-threshold")
async def set_price_alert_threshold(fields: dict = Body(...)):
    # Ersetzt die zuvor fest verdrahteten +-20% in card.html/dashboard.html/
    # ebay.html (siehe deren priceAlertThresholdPct).
    threshold = fields.get("threshold")
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or threshold <= 0:
        raise HTTPException(status_code=400, detail="threshold muss eine Zahl > 0 sein.")
    updated = db.set_price_alert_threshold(threshold)
    return JSONResponse(updated)


@app.put("/api/page-size")
async def set_page_size(fields: dict = Body(...)):
    # Globale Seitengroesse (Einstellungen) statt der zuvor pro Seite fest
    # verdrahteten 40 Zeilen (Karten, Kaeufe, Inventar, eBay, Inventur,
    # Wunschliste, Statistik-Listen - jede Seite liest sie sich selbst ueber
    # GET /api/app-status).
    size = fields.get("size")
    if not isinstance(size, int) or isinstance(size, bool) or not (5 <= size <= 500):
        raise HTTPException(status_code=400, detail="size muss eine Ganzzahl zwischen 5 und 500 sein.")
    updated = db.set_page_size(size)
    return JSONResponse(updated)


@app.put("/api/view-density")
async def set_view_density(fields: dict = Body(...)):
    density = fields.get("density")
    if density not in ("comfort", "compact"):
        raise HTTPException(status_code=400, detail="density muss 'comfort' oder 'compact' sein.")
    updated = db.set_view_density(density)
    return JSONResponse(updated)


@app.put("/api/csv-delimiter")
async def set_csv_delimiter(fields: dict = Body(...)):
    delimiter = fields.get("delimiter")
    if delimiter not in (",", ";"):
        raise HTTPException(status_code=400, detail="delimiter muss ',' oder ';' sein.")
    updated = db.set_csv_delimiter(delimiter)
    return JSONResponse(updated)


@app.put("/api/reminder-thresholds")
async def set_reminder_thresholds(fields: dict = Body(...)):
    def _positive_int(value):
        return isinstance(value, int) and not isinstance(value, bool) and value > 0

    min_days = fields.get("stale_listing_min_days")
    check_days = fields.get("stale_listing_check_days")
    wishlist_days = fields.get("stale_wishlist_min_days")
    if not (_positive_int(min_days) and _positive_int(check_days) and _positive_int(wishlist_days)):
        raise HTTPException(status_code=400, detail="Alle drei Schwellwerte müssen positive Ganzzahlen sein.")
    updated = db.set_reminder_thresholds(min_days, check_days, wishlist_days)
    return JSONResponse(updated)


@app.put("/api/kleinunternehmer-thresholds")
async def set_kleinunternehmer_thresholds(fields: dict = Body(...)):
    def _positive_number(value):
        return isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0

    prev_year_threshold = fields.get("prev_year_threshold")
    current_year_threshold = fields.get("current_year_threshold")
    if not (_positive_number(prev_year_threshold) and _positive_number(current_year_threshold)):
        raise HTTPException(status_code=400, detail="Beide Schwellwerte müssen positive Zahlen sein.")
    updated = db.set_kleinunternehmer_thresholds(prev_year_threshold, current_year_threshold)
    return JSONResponse(updated)


@app.put("/api/kleinunternehmer-hint-enabled")
async def set_kleinunternehmer_hint_enabled(fields: dict = Body(...)):
    # Schalter fuer den Paragraph-19-UStG-Hinweis auf gedruckten Verkaufs-
    # belegen (card.html) - abschaltbar, sobald die Kleinunternehmergrenze
    # ueberschritten ist und regulaer Umsatzsteuer ausgewiesen werden muss.
    updated = db.set_kleinunternehmer_hint_enabled(bool(fields.get("enabled")))
    return JSONResponse(updated)


# IP -> {"count": int, "locked_until": float (time.monotonic())} - reine
# In-Memory-Drossel gegen Passwort-Bruteforce auf /api/login; ueberlebt
# keinen Neustart und wirkt nur pro Prozess (kein Redis o.ae. noetig fuer
# dieses Ein-Nutzer-Tool). Absichtlich pro Quell-IP statt global, damit ein
# Angreifer nicht durch wiederholte Fehlversuche den echten Nutzer aussperren
# kann.
_LOGIN_ATTEMPT_LIMIT = 5
_LOGIN_LOCKOUT_SECONDS = 60
_failed_login_attempts = {}


@app.post("/api/login")
async def login(request: Request, fields: dict = Body(...)):
    if not APP_PASSWORD:
        raise HTTPException(status_code=401, detail="Falsches Passwort.")
    client_ip = request.client.host if request.client else "unknown"
    now = time.monotonic()
    entry = _failed_login_attempts.get(client_ip)
    if entry and entry["count"] >= _LOGIN_ATTEMPT_LIMIT and now < entry["locked_until"]:
        retry_after = int(entry["locked_until"] - now) + 1
        raise HTTPException(
            status_code=429,
            detail=f"Zu viele Fehlversuche - bitte in {retry_after} Sekunde(n) erneut versuchen.",
        )
    # hmac.compare_digest statt "==" - vermeidet einen Timing-Seitenkanal
    # (ein "==" bricht beim ersten falschen Zeichen ab, die Antwortzeit
    # verraet dadurch minimal etwas ueber korrekte Praefixe).
    password = str(fields.get("password") or "")
    if not hmac.compare_digest(password, APP_PASSWORD):
        entry = _failed_login_attempts.setdefault(client_ip, {"count": 0, "locked_until": 0.0})
        entry["count"] += 1
        if entry["count"] >= _LOGIN_ATTEMPT_LIMIT:
            entry["locked_until"] = now + _LOGIN_LOCKOUT_SECONDS
        try:
            db.record_failed_login(client_ip, datetime.now(timezone.utc).isoformat())
        except Exception:
            # Rein informativ (Anzeige in settings.html) - darf den Login-
            # Fehler selbst nicht verdecken, falls z.B. Supabase kurz nicht
            # erreichbar ist.
            logger.exception("Fehlgeschlagenen Login-Versuch konnte nicht protokolliert werden.")
        raise HTTPException(status_code=401, detail="Falsches Passwort.")
    _failed_login_attempts.pop(client_ip, None)
    request.session["authenticated"] = True
    return JSONResponse({"ok": True})


@app.post("/api/logout")
async def logout(request: Request):
    request.session.clear()
    return JSONResponse({"ok": True})


@app.get("/api/version")
async def get_version():
    # Beantwortet "laeuft im Container wirklich der erwartete PR/Commit?"
    # ohne Shell-Zugriff auf den Deployment-Host - alle drei Werte werden
    # automatisch beim Docker-Build erzeugt (siehe Dockerfile), kein
    # --build-arg noetig, funktioniert daher auch ueber eine NAS-Docker-App.
    return JSONResponse({
        "git_commit": GIT_COMMIT, "built_at": BUILD_TIME,
        "last_commit_subject": LAST_COMMIT_SUBJECT, "last_pr": LAST_PR,
    })


@app.middleware("http")
async def _require_login(request: Request, call_next):
    # Greift nur, wenn APP_PASSWORD tatsaechlich gesetzt ist - ohne
    # Konfiguration bleibt das Tool wie bisher ohne Login nutzbar (siehe
    # README.md, "Login einrichten"). login.html + die zum Rendern noetigen
    # statischen Assets (Logo/Icons/Manifest/Service-Worker) sowie der
    # Login-Endpoint selbst muessen erreichbar bleiben, sonst gaebe es keine
    # Moeglichkeit, sich ueberhaupt anzumelden.
    if not APP_PASSWORD:
        return await call_next(request)
    path = request.url.path
    if path in ("/login.html", "/api/login", "/manifest.json", "/sw.js") or path.startswith("/assets/"):
        return await call_next(request)
    if request.session.get("authenticated"):
        # Sitzung bei jeder authentifizierten Anfrage "anfassen" (mark_modified
        # in Starlettes Session-Klasse) - SessionMiddleware stellt dadurch bei
        # jeder Antwort ein frisch signiertes Cookie mit neuem Zeitstempel aus.
        # Macht aus SESSION_TIMEOUT_MINUTES ein gleitendes Inaktivitaets-Fenster
        # statt einer festen Ablaufzeit ab dem Login (ohne das wuerde die
        # Sitzung nach SESSION_TIMEOUT_MINUTES ablaufen, selbst bei staendiger
        # Nutzung).
        request.session["authenticated"] = True
        return await call_next(request)
    if path.startswith("/api/"):
        return JSONResponse({"detail": "Nicht angemeldet - bitte zuerst einloggen."}, status_code=401)
    return RedirectResponse(f"/login.html?{urlencode({'next': path})}", status_code=303)


@app.middleware("http")
async def _no_cache_for_html(request: Request, call_next):
    # StaticFiles sendet standardmaessig keinen expliziten Cache-Control-
    # Header - Browser wenden dann eine heuristische Cache-Lebensdauer an
    # (RFC 7234), wodurch z.B. help.html/release-notes.html nach einem
    # Deploy manchmal noch veraltet erscheinen, bis der Cache von selbst
    # ablaeuft. "no-cache" (nicht "no-store") erzwingt eine Revalidierung
    # bei jedem Laden - StaticFiles' ETag/Last-Modified-Unterstuetzung
    # liefert dann weiterhin ein schnelles 304, wenn sich nichts geaendert
    # hat, aber nie mehr eine blind gecachte alte Version ohne Nachfrage.
    response = await call_next(request)
    path = request.url.path
    if path == "/" or path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


# Zuletzt hinzugefuegt = aeusserste Schicht (Starlette wickelt Middleware in
# umgekehrter Hinzufuege-Reihenfolge) - laeuft dadurch vor _require_login/
# _no_cache_for_html und befuellt request.session, bevor die beiden es lesen.
# secret_key faellt ohne SESSION_SECRET_KEY auf einen zufaelligen Wert pro
# Prozessstart zurueck (siehe Warnung oben) - ganz ohne Schluessel wuerde
# SessionMiddleware selbst fehlschlagen. https_only/max_age explizit statt
# den SessionMiddleware-Defaults ueberlassen (siehe SESSION_COOKIE_SECURE/
# SESSION_TIMEOUT_MINUTES oben).
app.add_middleware(
    SessionMiddleware,
    secret_key=SESSION_SECRET_KEY or secrets.token_hex(32),
    max_age=SESSION_TIMEOUT_MINUTES * 60,
    https_only=SESSION_COOKIE_SECURE,
)

static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
