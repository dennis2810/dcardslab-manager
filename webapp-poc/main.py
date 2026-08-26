"""DCardLabs Web PoC - proves the core scan workflow works over HTTP before
any DB/auth/frontend investment.

POST /api/scan takes a front and back 9-up scan image (the same kind of
files you currently drag into the desktop app) and returns the same crop +
Claude-vision recognition result as a JSON list of 9 cards - nothing is
persisted, there is no auth, no eBay integration. That's deliberate: this
only answers one question - "does upload -> crop -> AI recognition work
cleanly as a web request?" - before we build a database, auth, or a real
frontend around it.

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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scanner"))
sys.path.insert(0, str(REPO_ROOT / "integrations"))

import scanner_v0_8_dynamic as scanner  # noqa: E402
from ai_card_recognition import recognize_card  # noqa: E402

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

        def process_one(fp):
            number = int(fp.stem)
            bp = back_map.get(number)
            if bp is None:
                return {"number": number, "status": f"Rückseite für Karte {number:03d} fehlt."}
            recognition = recognize_card(front_path=fp, back_path=bp)
            return {"number": number, **recognition}

        # Same pattern as pair_and_ocr() in the desktop app: recognize_card()
        # is a network round-trip, so a handful of cards run concurrently
        # instead of 9 sequential API calls.
        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(process_one, front_files))

    results.sort(key=lambda r: r["number"])
    return JSONResponse({"cards": results})


static_dir = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=static_dir, html=True), name="static")
