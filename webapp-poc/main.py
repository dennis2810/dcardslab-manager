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
import sys
import tempfile
import types
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
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
import storage  # noqa: E402

app = FastAPI(title="DCardLabs Web PoC")

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
async def list_cards():
    cards = [_attach_signed_urls(c) for c in db.list_cards()]
    return JSONResponse({"cards": cards})


@app.get("/api/cards/{card_id}")
async def get_card(card_id: str):
    card = db.get_card(card_id)
    if card is None:
        raise HTTPException(status_code=404, detail=f"Karte {card_id} nicht gefunden.")
    return JSONResponse(_attach_signed_urls(card))


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
