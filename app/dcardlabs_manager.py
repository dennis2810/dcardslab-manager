import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog
from pathlib import Path
from datetime import datetime
import sys
import sqlite3, csv, re, json, shutil, hashlib, os, zipfile, tempfile, logging, threading, time, urllib.request, urllib.error
import cv2

try:
    from PIL import Image, ImageTk
except ImportError:
    Image = None
    ImageTk = None

def _frozen_base():
    """Ordner der .exe, wenn als PyInstaller-Bundle gestartet, sonst der
    normale Projektordner (Python-Skript-Modus). In beiden Modi ist dies
    das beschreibbare Verzeichnis (DB, Bilder, Backups, Logs, Scan-Projekte)
    UND der Ort, an dem die echten scanner/-, integrations/- und templates/-
    Dateien liegen (--onedir bundelt sie als reale Dateien neben die .exe,
    daher ist kein separates _MEIPASS-Verzeichnis nötig)."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

PROJECT_ROOT = _frozen_base()
sys.path.insert(0, str(PROJECT_ROOT / "scanner"))
sys.path.insert(0, str(PROJECT_ROOT / "integrations"))

import scanner_v0_8_dynamic as scanner

APP = "DCardLabs SANDBOX v1.10.2-r9 v8.5 – eBay Publish"
EBAY_OAUTH_SERVER_URL = os.environ.get("DCARDSLAB_EBAY_SERVER_URL", "http://192.168.2.94:8080").rstrip("/")
BASE = PROJECT_ROOT
DB = BASE / "dcardlabs.db"
IMAGE_ROOT = BASE / "images" / "cards"
BACKUP_ROOT = BASE / "backups"
LOG_ROOT = BASE / "logs"
LOG_FILE = LOG_ROOT / "dcardlabs.log"
SCANNER_HASH = "44c34ea3c9593b3ee17bdd4de4022560c9fbc59034c60c7d191dd9a54b8246dd"



def setup_logging():
    """Configure a persistent DCardLabs log file."""
    LOG_ROOT.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("DCardLabs")
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.FileHandler(LOG_FILE, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s | %(levelname)s | %(message)s"
        ))
        logger.addHandler(handler)
    return logger


LOGGER = setup_logging()


def log_exception(context, exc):
    LOGGER.exception("%s: %s", context, exc)


def maximize_window(window):
    """Open a DCardLabs window maximized; fall back gracefully on other platforms."""
    try:
        window.update_idletasks()
        window.state("zoomed")
    except Exception:
        try:
            window.attributes("-zoomed", True)
        except Exception:
            pass


def fit_dialog(window, width, height, min_width=700, min_height=500):
    """Fit a child window into the usable screen area.

    Fixed pixel heights can exceed the usable area on Windows when display
    scaling is above 100 %. Dialogs therefore use the available screen size
    and remain resizable instead of relying on Toplevel zoom state.
    """
    try:
        window.update_idletasks()
        sw = max(800, int(window.winfo_screenwidth()))
        sh = max(600, int(window.winfo_screenheight()))
        max_w = max(760, sw - 40)
        max_h = max(520, sh - 90)
        w = min(int(width), max_w)
        h = min(int(height), max_h)
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2)
        window.geometry(f"{w}x{h}+{x}+{y}")
        window.minsize(min(int(min_width), w), min(int(min_height), h))
        window.resizable(True, True)
    except Exception:
        pass


def open_log_file(parent):
    try:
        LOG_ROOT.mkdir(parents=True, exist_ok=True)
        if not LOG_FILE.exists():
            LOG_FILE.write_text("DCardLabs Fehlerprotokoll\n", encoding="utf-8")
        if hasattr(os, "startfile"):
            os.startfile(str(LOG_FILE))
        else:
            import subprocess
            subprocess.Popen(["xdg-open", str(LOG_FILE)])
    except Exception as exc:
        log_exception("Logdatei konnte nicht geöffnet werden", exc)
        messagebox.showerror(
            "Logdatei",
            f"Die Logdatei konnte nicht geöffnet werden:\n{LOG_FILE}",
            parent=parent
        )


SCHEMA = """
CREATE TABLE IF NOT EXISTS cards (
    card_id INTEGER PRIMARY KEY AUTOINCREMENT,
    category TEXT DEFAULT '',
    theme TEXT DEFAULT '',
    team TEXT DEFAULT '',
    manufacturer TEXT DEFAULT '',
    set_name TEXT DEFAULT '',
    title TEXT NOT NULL,
    season_year TEXT DEFAULT '',
    card_number TEXT DEFAULT '',
    card_type TEXT DEFAULT '',
    variant TEXT DEFAULT '',
    is_numbered INTEGER DEFAULT 0,
    serial_number INTEGER,
    print_run INTEGER,
    language TEXT DEFAULT '',
    front_image TEXT DEFAULT '',
    back_image TEXT DEFAULT '',
    front_image_sha256 TEXT DEFAULT '',
    back_image_sha256 TEXT DEFAULT '',
    ocr_status TEXT DEFAULT '',
    ocr_confidence REAL DEFAULT 0,
    ocr_raw TEXT DEFAULT '',
    ocr_name TEXT DEFAULT '',
    ocr_team TEXT DEFAULT '',
    ocr_league TEXT DEFAULT '',
    ocr_set TEXT DEFAULT '',
    ocr_card_type TEXT DEFAULT '',
    ocr_card_number TEXT DEFAULT '',
    ocr_serial_number TEXT DEFAULT '',
    ocr_print_run TEXT DEFAULT '',
    ocr_variant TEXT DEFAULT '',
    squad_number TEXT DEFAULT '',
    position TEXT DEFAULT '',
    club_debut_season TEXT DEFAULT '',
    back_ocr_raw TEXT DEFAULT '',
    back_ocr_confidence REAL DEFAULT 0,
    back_year TEXT DEFAULT '',
    back_card_number TEXT DEFAULT '',
    back_serial_number TEXT DEFAULT '',
    back_print_run TEXT DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS inventory (
    inventory_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL,
    quantity INTEGER DEFAULT 1,
    condition TEXT DEFAULT 'NM',
    location TEXT DEFAULT '',
    notes TEXT DEFAULT '',
    FOREIGN KEY(card_id) REFERENCES cards(card_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS purchases (
    purchase_id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_date TEXT NOT NULL,
    platform TEXT DEFAULT '',
    seller TEXT DEFAULT '',
    card_count INTEGER DEFAULT 0,
    purchase_price REAL DEFAULT 0,
    shipping REAL DEFAULT 0,
    total_price REAL DEFAULT 0,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS scan_batches (
    batch_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    front_scan TEXT NOT NULL,
    back_scan TEXT NOT NULL,
    card_count INTEGER DEFAULT 0,
    status TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS ebay_settings (
    settings_id INTEGER PRIMARY KEY CHECK (settings_id = 1),
    category_name TEXT DEFAULT 'Trading Card Einzelkarten',
    category_id TEXT DEFAULT '261328',
    condition_ungraded_id TEXT DEFAULT '4000',
    condition_graded_id TEXT DEFAULT '2750',
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS ebay_listings (
    listing_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER NOT NULL UNIQUE,
    title TEXT DEFAULT '',
    description TEXT DEFAULT '',
    condition TEXT DEFAULT 'NM',
    price REAL DEFAULT 0,
    listing_format TEXT DEFAULT 'Festpreis',
    category TEXT DEFAULT '',
    sku TEXT DEFAULT '',
    status TEXT DEFAULT 'Entwurf',
    template_key TEXT DEFAULT 'football',
    exported_at TEXT DEFAULT '',
    scheduled_at TEXT DEFAULT '',
    ebay_item_id TEXT DEFAULT '',
    sold_at TEXT DEFAULT '',
    sale_price REAL DEFAULT 0,
    ebay_fees REAL DEFAULT 0,
    ebay_order_id TEXT DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY(card_id) REFERENCES cards(card_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS purchase_items (
    purchase_item_id INTEGER PRIMARY KEY AUTOINCREMENT,
    purchase_id INTEGER NOT NULL,
    card_id INTEGER NOT NULL,
    allocated_cost REAL DEFAULT 0,
    quantity INTEGER DEFAULT 1,
    notes TEXT DEFAULT '',
    FOREIGN KEY(purchase_id) REFERENCES purchases(purchase_id) ON DELETE CASCADE,
    FOREIGN KEY(card_id) REFERENCES cards(card_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS ebay_sales (
    sale_id INTEGER PRIMARY KEY AUTOINCREMENT,
    card_id INTEGER,
    listing_id INTEGER,
    ebay_item_id TEXT DEFAULT '',
    ebay_order_id TEXT DEFAULT '',
    sale_date TEXT DEFAULT '',
    quantity INTEGER DEFAULT 1,
    gross_price REAL DEFAULT 0,
    shipping_charged REAL DEFAULT 0,
    ebay_fees REAL DEFAULT 0,
    net_amount REAL DEFAULT 0,
    status TEXT DEFAULT 'Verkauft',
    imported_at TEXT NOT NULL,
    notes TEXT DEFAULT '',
    FOREIGN KEY(card_id) REFERENCES cards(card_id) ON DELETE SET NULL,
    FOREIGN KEY(listing_id) REFERENCES ebay_listings(listing_id) ON DELETE SET NULL
);
"""

def db():
    c = sqlite3.connect(DB)
    c.execute("PRAGMA foreign_keys=ON")
    c.executescript(SCHEMA)
    # Central eBay defaults. Existing databases are upgraded without losing data.
    c.execute(
        """INSERT OR IGNORE INTO ebay_settings
           (settings_id,category_name,category_id,condition_ungraded_id,
            condition_graded_id,updated_at)
           VALUES (1,?,?,?,?,?)""",
        ("Trading Card Einzelkarten", "261328", "4000", "2750",
         datetime.now().isoformat(timespec="seconds"))
    )
    c.commit()
    # Lightweight migration for v0.9 databases.
    cols = {r[1] for r in c.execute("PRAGMA table_info(cards)")}
    for name, typ, default in [
        ("ocr_confidence", "REAL", "0"),
        ("ocr_raw", "TEXT", "''"),
        ("ocr_name", "TEXT", "''"),
        ("ocr_team", "TEXT", "''"),
        ("ocr_league", "TEXT", "''"),
        ("ocr_set", "TEXT", "''"),
        ("ocr_card_type", "TEXT", "''"),
        ("ocr_card_number", "TEXT", "''"),
        ("ocr_serial_number", "TEXT", "''"),
        ("ocr_print_run", "TEXT", "''"),
        ("ocr_variant", "TEXT", "''"),
        ("squad_number", "TEXT", "''"),
        ("team", "TEXT", "''"),
        ("purchase_price", "REAL", "0"),
        ("purchase_date", "TEXT", "''"),
        ("purchase_source", "TEXT", "''"),
        ("condition", "TEXT", "''"),
        ("storage_location", "TEXT", "''"),
        ("notes", "TEXT", "''"),
        ("inventory_status", "TEXT", "'Im Bestand'"),
        ("sale_price", "REAL", "0"),
        ("sale_date", "TEXT", "''"),
        ("sale_platform", "TEXT", "''"),
        ("sold_status", "TEXT", "''"),

        ("position", "TEXT", "''"),
        ("club_debut_season", "TEXT", "''"),
        ("back_ocr_raw", "TEXT", "''"),
        ("back_ocr_confidence", "REAL", "0"),
        ("back_year", "TEXT", "''"),
        ("back_card_number", "TEXT", "''"),
        ("back_serial_number", "TEXT", "''"),
        ("back_print_run", "TEXT", "''"),
        ("front_image_sha256", "TEXT", "''"),
        ("back_image_sha256", "TEXT", "''"),
    ]:
        if name not in cols:
            c.execute(
                f"ALTER TABLE cards ADD COLUMN {name} {typ} DEFAULT {default}"
            )
    # eBay / purchase / sales migrations for existing databases.
    ebay_cols = {r[1] for r in c.execute("PRAGMA table_info(ebay_listings)")}
    for name, typ, default in [
        ("template_key", "TEXT", "'football'"),
        ("exported_at", "TEXT", "''"),
        ("scheduled_at", "TEXT", "''"),
        ("ebay_item_id", "TEXT", "''"),
        ("sold_at", "TEXT", "''"),
        ("sale_price", "REAL", "0"),
        ("ebay_fees", "REAL", "0"),
        ("ebay_order_id", "TEXT", "''"),
        ("ebay_offer_id", "TEXT", "''"),
        ("ebay_listing_id", "TEXT", "''"),
    ]:
        if name not in ebay_cols:
            c.execute(f"ALTER TABLE ebay_listings ADD COLUMN {name} {typ} DEFAULT {default}")

    c.commit()
    return c


def ensure_app_dirs():
    IMAGE_ROOT.mkdir(parents=True, exist_ok=True)
    BACKUP_ROOT.mkdir(parents=True, exist_ok=True)
    LOG_ROOT.mkdir(parents=True, exist_ok=True)


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def relative_image_ref(path):
    p = Path(path)
    try:
        return p.resolve().relative_to(BASE.resolve()).as_posix()
    except ValueError:
        return str(p)


def resolve_image_ref(ref):
    if not ref:
        return None
    p = Path(ref)
    return p if p.is_absolute() else BASE / p


def image_path_from_ref(ref):
    if not ref:
        return None
    path = resolve_image_ref(ref)
    return path if path and path.exists() else None


def store_card_image(source, card_id, side):
    """Copy an image into the managed library and return DB ref + SHA-256."""
    if not source:
        return "", ""
    LOGGER.info(
        "Bildoperation: Karte=%s Seite=%s Quelle=%s",
        card_id, side, source
    )
    src = Path(source)
    if not src.exists():
        raise FileNotFoundError(f"Bilddatei nicht gefunden: {src}")

    ensure_app_dirs()
    ext = src.suffix.lower()
    if ext not in {".jpg", ".jpeg", ".png", ".webp"}:
        ext = ".jpg"

    dst = IMAGE_ROOT / f"{int(card_id):06d}_{side}{ext}"
    dst.parent.mkdir(parents=True, exist_ok=True)

    try:
        same_file = src.resolve() == dst.resolve()
    except OSError:
        same_file = False

    if not same_file:
        # Write through a temporary file and atomically replace the managed
        # target. This avoids partially written images and Windows sharing
        # problems when a preview was just displayed.
        tmp_name = None
        try:
            fd, tmp_name = tempfile.mkstemp(
                prefix=f".{int(card_id):06d}_{side}_",
                suffix=ext,
                dir=str(dst.parent)
            )
            os.close(fd)
            shutil.copy2(src, tmp_name)
            os.replace(tmp_name, dst)
            tmp_name = None
        finally:
            if tmp_name:
                try:
                    os.unlink(tmp_name)
                except OSError:
                    pass

    return relative_image_ref(dst), sha256_file(dst)



def migrate_image_references():
    ensure_app_dirs()
    c=db()
    try:
        rows=c.execute(
            "SELECT card_id, front_image, back_image FROM cards ORDER BY card_id"
        ).fetchall()
        for card_id, front, back in rows:
            updates={}
            for side, ref in (("front",front),("back",back)):
                if not ref:
                    continue
                src=resolve_image_ref(ref)
                if not src or not src.exists():
                    continue
                if IMAGE_ROOT.resolve() in src.resolve().parents:
                    dst=src
                    new_ref=relative_image_ref(dst)
                else:
                    new_ref,_=store_card_image(src,card_id,side)
                    dst=resolve_image_ref(new_ref)
                updates[f"{side}_image"]=new_ref
                updates[f"{side}_image_sha256"]=sha256_file(dst)
            if updates:
                c.execute(
                    """UPDATE cards
                       SET front_image=COALESCE(?,front_image),
                           back_image=COALESCE(?,back_image),
                           front_image_sha256=COALESCE(?,front_image_sha256),
                           back_image_sha256=COALESCE(?,back_image_sha256)
                       WHERE card_id=?""",
                    (updates.get("front_image"),updates.get("back_image"),
                     updates.get("front_image_sha256"),updates.get("back_image_sha256"),
                     card_id)
                )
        c.commit()
    finally:
        c.close()


def create_db_backup(prefix="manual"):
    ensure_app_dirs()
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    target=BACKUP_ROOT/f"DCardLabs_{prefix}_{stamp}.db"
    src=sqlite3.connect(DB)
    dst=sqlite3.connect(target)
    try:
        src.backup(dst)
    finally:
        dst.close()
        src.close()
    return target


def create_project_backup():
    ensure_app_dirs()
    stamp=datetime.now().strftime("%Y%m%d_%H%M%S")
    target=BACKUP_ROOT/f"DCardLabs_Projektbackup_{stamp}.zip"
    snapshot=BACKUP_ROOT/f".snapshot_{stamp}.db"
    try:
        src=sqlite3.connect(DB)
        dst=sqlite3.connect(snapshot)
        try: src.backup(dst)
        finally:
            dst.close(); src.close()
        with zipfile.ZipFile(target,"w",zipfile.ZIP_DEFLATED) as z:
            z.write(snapshot,"dcardlabs.db")
            if IMAGE_ROOT.exists():
                for f in IMAGE_ROOT.rglob("*"):
                    if f.is_file():
                        z.write(f,Path("images/cards")/f.relative_to(IMAGE_ROOT))
    finally:
        if snapshot.exists():
            snapshot.unlink()
    return target


def restore_backup(path):
    path=Path(path)
    if not path.exists(): raise FileNotFoundError(str(path))
    emergency=create_db_backup("vor_restore")
    if path.suffix.lower()==".db":
        src=sqlite3.connect(path)
        dst=sqlite3.connect(DB)
        try: src.backup(dst)
        finally: dst.close(); src.close()
    else:
        tmp=Path(tempfile.mkdtemp(prefix="dcard_restore_"))
        try:
            with zipfile.ZipFile(path) as z: z.extractall(tmp)
            source=tmp/"dcardlabs.db"
            if not source.exists(): raise RuntimeError("Backup enthält keine dcardlabs.db.")
            src=sqlite3.connect(source); dst=sqlite3.connect(DB)
            try: src.backup(dst)
            finally: dst.close(); src.close()
            src_images=tmp/"images"/"cards"
            if src_images.exists():
                ensure_app_dirs()
                for f in src_images.rglob("*"):
                    if f.is_file():
                        target=IMAGE_ROOT/f.relative_to(src_images)
                        target.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copy2(f,target)
        finally:
            shutil.rmtree(tmp,ignore_errors=True)
    migrate_image_references()
    return emergency


SANDBOX_MODE = os.environ.get('DCARDLABS_SANDBOX', '0') == '1'


def maybe_auto_backup():
    ensure_app_dirs()
    autos=sorted(BACKUP_ROOT.glob("DCardLabs_auto_*.db"),
                 key=lambda x:x.stat().st_mtime, reverse=True)
    if not autos or datetime.now().timestamp()-autos[0].stat().st_mtime>=86400:
        create_db_backup("auto")
        autos=sorted(BACKUP_ROOT.glob("DCardLabs_auto_*.db"),
                     key=lambda x:x.stat().st_mtime, reverse=True)
    for f in autos[30:]:
        try:f.unlink()
        except OSError:pass


def check_image_references():
    c=db()
    try:
        rows=c.execute("""SELECT card_id,title,front_image,back_image,
                          front_image_sha256,back_image_sha256 FROM cards""").fetchall()
    finally:c.close()
    missing=[]; changed=[]
    for cid,title,front,back,fh,bh in rows:
        for side,ref,expected in (("Vorderseite",front,fh),("Rückseite",back,bh)):
            if not ref: continue
            path=resolve_image_ref(ref)
            if not path or not path.exists():
                missing.append((cid,title,side,ref))
            elif expected and sha256_file(path)!=expected:
                changed.append((cid,title,side,ref))
    return missing,changed


def safe_filename(text):
    text = re.sub(r"[^A-Za-z0-9ÄÖÜäöüß _-]", "", text or "").strip()
    return re.sub(r"\s+", " ", text)

def normalize_ocr_text(text):
    text = (text or "").replace("|", "I")
    text = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9' .-]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    # Common OCR artefacts in names.
    text = re.sub(r"\bGOLD\b$", "", text, flags=re.I).strip()
    return text

def find_tesseract():
    candidates = []
    # Mitgelieferte portable Tesseract-Version direkt neben der .exe
    # (bzw. neben dem Skript im Python-Modus) hat Vorrang, damit das
    # Bundle ohne separate Tesseract-Installation funktioniert.
    bundled = PROJECT_ROOT / "Tesseract-OCR" / "tesseract.exe"
    if bundled.is_file():
        candidates.append(str(bundled))
    # Frozen/portable mode deliberately uses only the bundled Tesseract.
    # This prevents a copied DCardLabs installation from silently depending
    # on C:\Program Files. Python/development mode keeps system fallback.
    if not getattr(sys, "frozen", False):
        which = shutil.which("tesseract")
        if which:
            candidates.append(which)
        for root in (
            os.environ.get("ProgramFiles", ""),
            os.environ.get("ProgramFiles(x86)", ""),
            os.environ.get("LOCALAPPDATA", ""),
        ):
            if root:
                candidates.extend([
                    os.path.join(root, "Tesseract-OCR", "tesseract.exe"),
                    os.path.join(root, "Programs", "Tesseract-OCR", "tesseract.exe"),
                ])
    for p in candidates:
        if p and os.path.isfile(p):
            return p
    return ""

def ocr_setup_status():
    try:
        import pytesseract
    except Exception as e:
        return False, f"Python-Modul pytesseract fehlt: {e}"
    exe = find_tesseract()
    if not exe:
        return False, (
            "Tesseract-OCR wurde nicht gefunden. "
            "pytesseract allein reicht nicht; die Tesseract-EXE muss installiert sein."
        )
    pytesseract.pytesseract.tesseract_cmd = exe
    try:
        version = str(pytesseract.get_tesseract_version()).splitlines()[0]
        return True, f"Tesseract gefunden: {exe} | {version}"
    except Exception as e:
        return False, f"Tesseract gefunden, aber nicht startbar: {e}"

def clean_ocr_candidate(text):
    text = (text or "").replace("|", " ").replace("_", " ")
    text = re.sub(r"[^A-Za-zÀ-ÖØ-öø-ÿ0-9' -]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    parts = text.split()

    while parts and len(parts[0]) == 1:
        parts.pop(0)
    while parts and len(parts[-1]) == 1:
        parts.pop()

    if len(parts) >= 3 and parts[-1].upper() in {
        "EC", "IF", "AE", "A", "I", "L", "7", "9", "O", "0",
        "EE", "E", "CE", "LF"
    }:
        parts.pop()

    text = " ".join(parts)
    text = re.sub(r"^PHRISTIAN\b", "CHRISTIAN", text, flags=re.I)
    text = re.sub(r"\bAMOURR$", "AMOURA", text, flags=re.I)
    return text

UNKNOWN_LAYOUT_ROOT = BASE / "Neue_Vorlage_pruefen"

def _save_unknown_layout(card, reason, detail=""):
    """
    Speichert das Kartenbild fuer die manuelle Pruefung, wenn ocr_name()
    keinen brauchbaren Treffer liefert (z.B. weil die Karte einem noch
    unbekannten Vorlagen-Layout entspricht). Dient als Grundlage, um
    spaeter gezielt neue Regionsdefinitionen zu ergaenzen, OHNE die
    bestehenden vier Regionen zu veraendern. Ein Fehler beim Speichern
    darf den eigentlichen Scan-Vorgang nicht unterbrechen.
    """
    try:
        UNKNOWN_LAYOUT_ROOT.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        cv2.imwrite(str(UNKNOWN_LAYOUT_ROOT / f"{stamp}.png"), card)
        (UNKNOWN_LAYOUT_ROOT / f"{stamp}.txt").write_text(
            f"Zeitpunkt: {datetime.now().isoformat()}\n"
            f"Grund: {reason}\n"
            f"Details: {detail}\n",
            encoding="utf-8"
        )
    except Exception:
        pass

def ocr_name(card):
    """
    Targeted OCR v1.4.1.
    The v0.8 scanner/detection engine remains completely untouched.
    """
    ok, setup = ocr_setup_status()
    if not ok:
        return "", "OCR nicht verfügbar", 0, setup

    try:
        import pytesseract

        h, w = card.shape[:2]

        regions = [
            # White PSG-style nameplate.
            ("white_nameplate", .910, .947, .04, .80),

            # Topps Gold / black nameplate.
            ("gold_nameplate", .872, .932, .18, .76),
            ("gold_nameplate_tight", .882, .923, .22, .72),
            ("gold_nameplate_lower", .890, .935, .20, .74),
        ]

        candidates = []

        for region, y1, y2, x1, x2 in regions:
            crop0 = card[int(y1*h):int(y2*h), int(x1*w):int(x2*w)]
            if crop0.size == 0:
                continue

            crop = cv2.resize(
                crop0, None, fx=8, fy=8,
                interpolation=cv2.INTER_CUBIC
            )
            gray = cv2.cvtColor(crop, cv2.COLOR_BGR2GRAY)

            # Gold lettering on a dark nameplate benefits from local contrast
            # and explicit black/white separation.
            clahe = cv2.createCLAHE(clipLimit=2.2, tileGridSize=(8, 8))
            enhanced = clahe.apply(gray)

            variants = [
                ("gray", gray),
                ("clahe", enhanced),
                (
                    "otsu",
                    cv2.threshold(
                        enhanced, 0, 255,
                        cv2.THRESH_BINARY + cv2.THRESH_OTSU
                    )[1]
                ),
                (
                    "otsu_inv",
                    cv2.threshold(
                        enhanced, 0, 255,
                        cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU
                    )[1]
                ),
            ]

            for variant, img in variants:
                for psm in (7, 6):
                    try:
                        data = pytesseract.image_to_data(
                            img,
                            config=f"--psm {psm} -c preserve_interword_spaces=1",
                            lang="eng",
                            output_type=pytesseract.Output.DICT
                        )
                    except Exception as e:
                        return "", "OCR-Fehler", 0, str(e)

                    raw_words = []
                    confs = []

                    for i, raw in enumerate(data.get("text", [])):
                        raw = raw.strip()
                        if not raw:
                            continue
                        raw_words.append(raw)
                        try:
                            confs.append(float(data["conf"][i]))
                        except Exception:
                            pass

                    raw_text = " ".join(raw_words)
                    cleaned = clean_ocr_candidate(raw_text)
                    parts = cleaned.split()

                    if not 2 <= len(parts) <= 3:
                        continue
                    if sum(ch.isalpha() for ch in cleaned) < 8:
                        continue

                    upper = cleaned.upper()
                    blocked = any(x in upper for x in (
                        "PARIS", "SAINT", "GERMAIN", "BUNDESLIGA",
                        "MIDFIELDER", "DEFENDER", "FORWARD",
                        "GOALKEEPER", "GOLD", "TOPPS"
                    ))
                    if blocked:
                        continue

                    valid = [c for c in confs if c >= 0]
                    conf = sum(valid) / len(valid) if valid else 0

                    score = conf + 10
                    if len(parts) in (2, 3):
                        score += 5
                    if region == "gold_nameplate_tight":
                        score += 7
                    elif region == "gold_nameplate":
                        score += 5
                    elif region == "white_nameplate":
                        score += 4
                    if variant in ("clahe", "otsu"):
                        score += 2
                    if psm == 7:
                        score += 3

                    candidates.append(
                        (score, conf, cleaned, raw_text, region, variant, psm)
                    )

        if not candidates:
            _save_unknown_layout(card, "kein Kandidat gefunden")
            return "", "nicht erkannt", 0, setup

        candidates.sort(reverse=True)
        best = candidates[0]

        status = "ok" if best[1] >= 65 else "prüfen"
        if best[1] < 40:
            _save_unknown_layout(
                card, "sehr geringe Konfidenz",
                f"Text: {best[2]!r} | Konfidenz: {best[1]}"
            )
        return best[2], status, round(float(best[1]), 1), best[3]

    except Exception as e:
        return "", "OCR-Fehler", 0, str(e)
def parse_back_ocr(raw):
    """Extract explicitly labelled backside facts without changing scanning."""
    raw = re.sub(r"\s+", " ", raw or "").strip()
    result = {
        "back_year": "",
        "back_card_number": "",
        "back_serial_number": "",
        "back_print_run": "",
        "squad_number": "",
        "position": "",
        "club_debut_season": "",
        "team": "",
        "card_type": "",
    }

    m = re.search(r"\bSquad\s+Number\s*[:\-]?\s*(\d{1,3})\b", raw, re.I)
    if m:
        result["squad_number"] = m.group(1)

    m = re.search(
        r"\bPosition\s*[:\-]?\s*([A-Za-z][A-Za-z -]{2,30}?)(?=\s+Squad\s+Number\b|$)",
        raw, re.I
    )
    if m:
        result["position"] = m.group(1).strip()

    # OCR may turn "2023/24" into "2023 24".
    m = re.search(
        r"\bClub\s+Debut\s+Season\s*[:\-]?\s*((?:20\d{2})\s*(?:[/\- ]\s*\d{2,4}))",
        raw, re.I
    )
    if m:
        result["club_debut_season"] = re.sub(r"\s+", "", m.group(1)).replace("-", "/")
        if "/" not in result["club_debut_season"]:
            result["club_debut_season"] = result["club_debut_season"][:4] + "/" + result["club_debut_season"][4:]

    # Explicit numbered formats: 123/199 or #123/199.
    for pat in (
        r"#\s*(\d{1,4})\s*/\s*(\d{1,5})",
        r"\b(\d{1,4})\s*/\s*(\d{1,5})\b",
    ):
        m = re.search(pat, raw)
        if m:
            result["back_serial_number"] = m.group(1)
            result["back_print_run"] = m.group(2)
            break

    # Only an explicit Card/Card No. label becomes card number.
    m = re.search(
        r"\b(?:Card|Card\s*No\.?|Card\s*Number)\s*[:#]?\s*(\d{1,4})\b",
        raw, re.I
    )
    if m:
        result["back_card_number"] = m.group(1)

    # Never interpret a bare copyright year as the card year.
    m = re.search(
        r"\b(?:Season|Saison|Year|Jahr)\s*[:\-]?\s*((?:20\d{2})(?:\s*[/\-]\s*\d{2,4})?)\b",
        raw, re.I
    )
    if m:
        result["back_year"] = re.sub(r"\s+", "", m.group(1)).replace("-", "/")

    for variant in ("GOLD", "SILVER", "BLACK", "PLATINUM", "LIMITED EDITION"):
        if re.search(r"\b" + re.escape(variant) + r"\b", raw, re.I):
            result["card_type"] = variant.title()
            break

    return result


def enrich_back_fields(back_ocr_raw):
    return parse_back_ocr(back_ocr_raw)

def scan_one(path, out_dir, quality, rotate):
    path = Path(path)
    if cv2.imread(str(path)) is None:
        raise RuntimeError(f"Scan konnte nicht gelesen werden:\n{path}")

    files = scanner.process(path, out_dir, quality, rotate)
    if len(files) != 9:
        raise RuntimeError(
            f"Dynamic Grid hat {len(files)} statt 9 Karten erkannt."
        )

    bad = []
    for p in files:
        im = cv2.imread(str(p))
        if im is None:
            bad.append(f"{Path(p).name}: nicht lesbar")
        elif im.shape[:2] != (880, 630):
            bad.append(f"{Path(p).name}: {im.shape[1]}x{im.shape[0]}")

    if bad:
        raise RuntimeError(
            "Ausgabeprüfung fehlgeschlagen:\n" + "\n".join(bad)
        )
    return [Path(p) for p in files]

def ocr_backside(card):
    ok, setup = ocr_setup_status()
    empty={"raw":"","confidence":0,"year":"","card_number":"","serial_number":"","print_run":"","status":"deaktiviert"}
    if not ok: empty["status"]="OCR nicht verfügbar"; return empty
    try:
        import pytesseract
        if card is None: empty["status"]="nicht lesbar"; return empty
        img=cv2.resize(card,None,fx=2.5,fy=2.5,interpolation=cv2.INTER_CUBIC)
        gray=cv2.cvtColor(img,cv2.COLOR_BGR2GRAY)
        variants=[gray,cv2.threshold(gray,0,255,cv2.THRESH_BINARY+cv2.THRESH_OTSU)[1]]
        best=("",0)
        for v in variants:
            d=pytesseract.image_to_data(v,config="--psm 6",lang="eng",output_type=pytesseract.Output.DICT)
            words=[]; conf=[]
            for i,t in enumerate(d.get("text",[])):
                t=t.strip()
                if t:
                    words.append(t)
                    try: conf.append(float(d["conf"][i]))
                    except: pass
            c=sum(x for x in conf if x>=0)/max(1,len([x for x in conf if x>=0]))
            if c>best[1]: best=(" ".join(words),c)
        raw=normalize_ocr_text(best[0])
        m=re.search(r"(?<!\d)(\d{1,4})\s*/\s*(\d{1,5})(?!\d)",raw)
        pr=f"{m.group(1)}/{m.group(2)}" if m else ""
        serial=m.group(1) if m else ""
        years=re.findall(r"(?<!\d)(20(?:1\d|2[0-9]))(?!\d)",raw)
        nums=re.findall(r"(?i)(?:card|no\.?|number|nr\.?)\s*#?\s*(\d{1,5})",raw)
        return {"raw":raw,"confidence":round(best[1],1),"year":years[0] if years else "","card_number":nums[0] if nums else "","serial_number":serial,"print_run":pr,"status":"ok" if best[1]>=45 else "prüfen"}
    except Exception as e:
        empty["raw"]=str(e); empty["status"]="OCR-Fehler"; return empty

def infer_card_metadata(front_raw, back_raw, back_struct):
    combined = " ".join(x for x in (front_raw or "", back_raw or "") if x)
    upper = combined.upper()
    result = {
        "category": "",
        "theme": "",
        "manufacturer": "",
        "set_name": "",
        "season_year": "",
        "card_type": "",
        "variant": "",
        "team": "",
        "position": back_struct.get("position", ""),
        "squad_number": back_struct.get("squad_number", ""),
        "club_debut_season": back_struct.get("club_debut_season", ""),
        "card_number": back_struct.get("back_card_number", ""),
        "serial_number": back_struct.get("back_serial_number", ""),
        "print_run": back_struct.get("back_print_run", ""),
        "is_numbered": 1 if back_struct.get("back_serial_number") and back_struct.get("back_print_run") else 0,
    }

    if "BUNDESLIGA" in upper or any(
        x in upper for x in ("MIDFIELDER", "DEFENDER", "FORWARD", "GOALKEEPER")
    ):
        result["category"] = "Fußball"
    if re.search(r"\bTOPPS\b", upper):
        result["manufacturer"] = "Topps"
    if re.search(r"\bBUNDESLIGA\b", upper):
        result["set_name"] = "Bundesliga"

    for label in ("GOLD", "SILVER", "BLACK", "PLATINUM", "LIMITED EDITION"):
        if re.search(r"\b" + re.escape(label) + r"\b", upper):
            result["card_type"] = label.title()
            break

    # Only explicitly labelled Season/Saison becomes the card season.
    m = re.search(
        r"\b(?:Season|Saison)\s*[:\-]?\s*((?:20\d{2})\s*[/\-]\s*\d{2,4})\b",
        combined, re.I
    )
    if m and "CLUB DEBUT SEASON" not in combined[max(0, m.start()-25):m.start()].upper():
        result["season_year"] = re.sub(r"\s+", "", m.group(1)).replace("-", "/")

    clubs = [
        "BORUSSIA DORTMUND", "SV WERDER BREMEN",
        "PARIS SAINT-GERMAIN", "PARIS SAINT GERMAIN",
        "BORUSSIA MÖNCHENGLADBACH", "BAYERN MUNICH",
        "FC BAYERN MÜNCHEN", "RB LEIPZIG", "VFL WOLFSBURG",
        "VfB STUTTGART", "1. FC KÖLN"
    ]
    for club in clubs:
        if club.upper() in upper:
            result["team"] = club
            break

    return result


def pair_and_ocr(front_files, back_files, pair_dir, do_ocr, do_back_ocr):
    back_map = {int(p.stem): p for p in back_files}
    pairs = []

    for fp in sorted(front_files, key=lambda p: int(p.stem)):
        number = int(fp.stem)
        bp = back_map.get(number)
        if bp is None:
            raise RuntimeError(f"Rückseite für Karte {number:03d} fehlt.")

        title, ocr_status, confidence, raw = "", "deaktiviert", 0, ""
        back_ocr = {
            "raw": "", "confidence": 0, "year": "", "card_number": "",
            "serial_number": "", "print_run": "", "status": "deaktiviert"
        }

        if do_ocr:
            title, ocr_status, confidence, raw = ocr_name(cv2.imread(str(fp)))
        if do_back_ocr:
            back_ocr = ocr_backside(cv2.imread(str(bp)))

        back_struct = parse_back_ocr(back_ocr.get("raw", ""))
        meta = infer_card_metadata(raw, back_ocr.get("raw", ""), back_struct)

        # Keep compatibility with the older backside parser.
        if not meta["card_number"]:
            meta["card_number"] = back_ocr.get("card_number", "")
        if not meta["serial_number"] and "/" in back_ocr.get("print_run", ""):
            s, p = back_ocr["print_run"].split("/", 1)
            meta["serial_number"], meta["print_run"] = s, p

        safe = safe_filename(title)
        folder = pair_dir / f"{number:03d}"
        folder.mkdir(parents=True, exist_ok=True)
        stem = f"{number:03d}" + (f"_{safe}" if safe else "")
        front_dst = folder / f"{stem}_Vorderseite.jpg"
        back_dst = folder / f"{stem}_Rueckseite.jpg"
        shutil.copy2(fp, front_dst)
        shutil.copy2(bp, back_dst)

        pairs.append({
            "number": number, "title": title, "ocr_status": ocr_status,
            "ocr_confidence": confidence, "ocr_raw": raw,
            "front": str(front_dst.resolve()), "back": str(back_dst.resolve()),
            "back_ocr_raw": back_ocr.get("raw", ""),
            "back_ocr_confidence": back_ocr.get("confidence", 0),
            "back_year": back_ocr.get("year", ""),
            "back_card_number": back_ocr.get("card_number", ""),
            "back_serial_number": back_ocr.get("serial_number", ""),
            "back_print_run": back_ocr.get("print_run", ""),
            "back_ocr_status": back_ocr.get("status", ""),
            **meta,
        })

    if len(pairs) != 9:
        raise RuntimeError(f"{len(pairs)} statt 9 Kartenpaare.")
    return pairs


def write_pair_exports(pairs, pair_dir):
    headers = [
        "Karte", "Name/Titel", "Kategorie", "Hersteller", "Set/Serie",
        "Kartentyp", "Saison", "Team", "Position", "Squad Number",
        "Kartennummer", "Numbered", "Seriennummer", "Print Run",
        "OCR Status", "OCR Confidence", "Rückseiten-OCR"
    ]
    with (pair_dir / "karten_paarung.csv").open(
        "w", newline="", encoding="utf-8-sig"
    ) as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(headers)
        for r in pairs:
            w.writerow([
                r["number"], r["title"], r.get("category", ""),
                r.get("manufacturer", ""), r.get("set_name", ""),
                r.get("card_type", ""), r.get("season_year", ""),
                r.get("team", ""), r.get("position", ""),
                r.get("squad_number", ""), r.get("card_number", ""),
                "Ja" if r.get("is_numbered") else "Nein",
                r.get("serial_number", ""), r.get("print_run", ""),
                r["ocr_status"], r["ocr_confidence"],
                r.get("back_ocr_raw", "")
            ])

    (pair_dir / "karten_paarung.json").write_text(
        json.dumps(pairs, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )


def insert_cards(pairs, batch_id):
    c = db()
    try:
        for r in pairs:
            title = r["title"].strip() or f"Unbenannte Karte {r['number']:03d}"

            def int_or_none(value):
                try:
                    return int(value) if str(value).strip() else None
                except (TypeError, ValueError):
                    return None

            c.execute(
                """
                INSERT INTO cards (
                    category, theme, manufacturer, set_name, title,
                    season_year, card_number, card_type, variant,
                    is_numbered, serial_number, print_run, language,
                    front_image, back_image,
                    ocr_status, ocr_confidence, ocr_raw, ocr_name,
                    ocr_team, ocr_set, ocr_card_type,
                    ocr_card_number, ocr_serial_number, ocr_print_run,
                    ocr_variant, squad_number, position, club_debut_season,
                    back_ocr_raw, back_ocr_confidence, back_year,
                    back_card_number, back_serial_number, back_print_run,
                    created_at
                )
                VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    r.get("category", ""),
                    r.get("theme", ""),
                    r.get("manufacturer", ""),
                    r.get("set_name", ""),
                    title,
                    r.get("season_year", ""),
                    r.get("card_number", ""),
                    r.get("card_type", ""),
                    r.get("variant", ""),
                    r.get("is_numbered", 0),
                    int_or_none(r.get("serial_number")),
                    int_or_none(r.get("print_run")),
                    "",
                    r["front"], r["back"],
                    r["ocr_status"], r["ocr_confidence"], r["ocr_raw"], title,
                    r.get("team", ""),
                    r.get("set_name", ""),
                    r.get("card_type", ""),
                    r.get("card_number", ""),
                    r.get("serial_number", ""),
                    r.get("print_run", ""),
                    r.get("variant", ""),
                    r.get("squad_number", ""),
                    r.get("position", ""),
                    r.get("club_debut_season", ""),
                    r.get("back_ocr_raw", ""),
                    r.get("back_ocr_confidence", 0),
                    r.get("back_year", ""),
                    r.get("back_card_number", ""),
                    r.get("back_serial_number", ""),
                    r.get("back_print_run", ""),
                    __import__("datetime").datetime.now().isoformat(timespec="seconds")
                )
            )
            card_id = c.execute("SELECT last_insert_rowid()").fetchone()[0]
            front_ref, front_hash = store_card_image(r["front"], card_id, "front")
            back_ref, back_hash = store_card_image(r["back"], card_id, "back")
            c.execute(
                """UPDATE cards
                   SET front_image=?, back_image=?,
                       front_image_sha256=?, back_image_sha256=?
                   WHERE card_id=?""",
                (front_ref, back_ref, front_hash, back_hash, card_id)
            )
            c.execute(
                "INSERT INTO inventory(card_id, quantity) VALUES (?,?)",
                (card_id, 1)
            )

        c.execute(
            "UPDATE scan_batches SET card_count=?, status=? WHERE batch_id=?",
            (len(pairs), "OK", batch_id)
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def add_manual_card(data):
    """Add a card directly to SQLite without scanner/OCR."""
    c = db()
    try:
        title = (data.get("title") or data.get("name") or "").strip()
        if not title:
            raise ValueError("Name/Titel ist erforderlich.")

        def number(value):
            try:
                return float(str(value).replace(",", ".")) if str(value).strip() else 0
            except (TypeError, ValueError):
                return 0

        fields = {
            "category": data.get("category", ""),
            "theme": data.get("theme", ""),
            "team": data.get("team", ""),
            "manufacturer": data.get("manufacturer", ""),
            "set_name": data.get("set_name", ""),
            "title": title,
            "season_year": data.get("season_year", ""),
            "card_number": data.get("card_number", ""),
            "card_type": data.get("card_type", ""),
            "variant": data.get("variant", ""),
            "is_numbered": 1 if data.get("is_numbered") else 0,
            "serial_number": data.get("serial_number") or None,
            "print_run": data.get("print_run") or None,
            "language": data.get("language", ""),
            "front_image": data.get("front_image", ""),
            "back_image": data.get("back_image", ""),
            "purchase_price": number(data.get("purchase_price")),
            "purchase_date": data.get("purchase_date", ""),
            "purchase_source": data.get("purchase_source", ""),
            "condition": data.get("condition", ""),
            "storage_location": data.get("storage_location", ""),
            "notes": data.get("notes", ""),
            "inventory_status": data.get("inventory_status", "Im Bestand"),
        }

        columns = list(fields) + ["created_at"]
        values = list(fields.values()) + [
            __import__("datetime").datetime.now().isoformat(timespec="seconds")
        ]
        placeholders = ",".join("?" for _ in values)

        cur = c.execute(
            f"INSERT INTO cards ({','.join(columns)}) VALUES ({placeholders})",
            values
        )
        card_id = cur.lastrowid
        if data.get("front_image"):
            ref, digest = store_card_image(data["front_image"], card_id, "front")
            c.execute("UPDATE cards SET front_image=?, front_image_sha256=? WHERE card_id=?",
                      (ref, digest, card_id))
        if data.get("back_image"):
            ref, digest = store_card_image(data["back_image"], card_id, "back")
            c.execute("UPDATE cards SET back_image=?, back_image_sha256=? WHERE card_id=?",
                      (ref, digest, card_id))
        c.execute(
            """INSERT INTO inventory(
                card_id, quantity, condition, location, notes
            ) VALUES (?,?,?,?,?)""",
            (
                card_id, int(data.get("quantity", 1) or 1),
                data.get("condition", "NM"),
                data.get("storage_location", ""),
                data.get("notes", "")
            )
        )
        c.commit()
        return card_id
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def add_manual_inventory(card_id, quantity=1, condition="", location="", notes=""):
    """Create or update an inventory record for an existing card."""
    c = db()
    try:
        card = c.execute("SELECT card_id FROM cards WHERE card_id=?", (int(card_id),)).fetchone()
        if not card:
            raise ValueError(f"Karte mit ID {card_id} existiert nicht.")
        quantity = int(quantity or 1)
        existing = c.execute(
            "SELECT inventory_id FROM inventory WHERE card_id=? ORDER BY inventory_id ASC LIMIT 1",
            (int(card_id),)
        ).fetchone()
        if existing:
            inventory_id = existing[0]
            c.execute(
                "UPDATE inventory SET quantity=?, condition=?, location=?, notes=? WHERE inventory_id=?",
                (quantity, condition, location, notes, inventory_id)
            )
        else:
            cur = c.execute(
                "INSERT INTO inventory(card_id, quantity, condition, location, notes) VALUES(?,?,?,?,?)",
                (int(card_id), quantity, condition, location, notes)
            )
            inventory_id = cur.lastrowid
        c.commit()
        return inventory_id
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def add_manual_purchase(data):
    """Create a purchase entry manually."""
    c = db()
    try:
        def money(v):
            try:
                return float(str(v).replace(",", ".")) if str(v).strip() else 0
            except (TypeError, ValueError):
                return 0
        cur = c.execute(
            """INSERT INTO purchases
               (purchase_date, platform, seller, card_count,
                purchase_price, shipping, total_price, notes)
               VALUES(?,?,?,?,?,?,?,?)""",
            (
                data.get("purchase_date", ""),
                data.get("platform", ""),
                data.get("seller", ""),
                int(data.get("card_count", 1) or 1),
                money(data.get("purchase_price")),
                money(data.get("shipping")),
                money(data.get("total_price")),
                data.get("notes", "")
            )
        )
        purchase_id = cur.lastrowid
        c.commit()
        return purchase_id
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def update_inventory_entry(inventory_id, card_id, quantity, condition, location, notes):
    """Update one inventory record without changing the card master data."""
    c = db()
    try:
        card = c.execute("SELECT card_id FROM cards WHERE card_id=?", (int(card_id),)).fetchone()
        if not card:
            raise ValueError(f"Karte mit ID {card_id} existiert nicht.")
        quantity = int(quantity or 1)
        if quantity < 1:
            raise ValueError("Die Menge muss mindestens 1 sein.")
        c.execute(
            """UPDATE inventory SET card_id=?, quantity=?, condition=?, location=?, notes=?
               WHERE inventory_id=?""",
            (int(card_id), quantity, condition or "", location or "", notes or "", int(inventory_id))
        )
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def open_inventory_editor(parent, inventory_id, on_saved=None):
    """Edit an existing inventory row, especially location and notes."""
    win = tk.Toplevel(parent)
    win.title(f"Inventar bearbeiten – #{inventory_id}")
    fit_dialog(win, 620, 380, min_width=560, min_height=340)
    win.transient(parent)
    win.grab_set()
    frm = ttk.Frame(win, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    c = db()
    row = c.execute(
        """SELECT i.card_id, c.title, i.quantity, i.condition, i.location, i.notes
           FROM inventory i JOIN cards c ON c.card_id=i.card_id
           WHERE i.inventory_id=?""", (int(inventory_id),)
    ).fetchone()
    c.close()
    if not row:
        messagebox.showerror("Inventar", f"Inventareintrag #{inventory_id} wurde nicht gefunden.", parent=win)
        win.destroy()
        return

    ttk.Label(frm, text="Karte").grid(row=0, column=0, sticky="w", padx=(0,10), pady=5)
    ttk.Label(frm, text=f"#{row[0]} – {row[1]}").grid(row=0, column=1, sticky="w", pady=5)
    entries={}
    for r,(label,key) in enumerate([
        ("Menge","quantity"),("Zustand","condition"),("Lagerort","location"),("Notizen","notes")
    ], start=1):
        ttk.Label(frm,text=label).grid(row=r,column=0,sticky="w",padx=(0,10),pady=5)
        w=tk.Text(frm,height=4) if key=="notes" else ttk.Entry(frm)
        w.grid(row=r,column=1,sticky="ew",pady=5)
        entries[key]=w

    def setv(key,val):
        w=entries[key]
        if isinstance(w,tk.Text):
            w.insert("1.0", "" if val is None else str(val))
        else:
            w.insert(0, "" if val is None else str(val))
    def getv(key):
        w=entries[key]
        return w.get("1.0","end").strip() if isinstance(w,tk.Text) else w.get().strip()
    setv("quantity",row[2]); setv("condition",row[3]); setv("location",row[4]); setv("notes",row[5])

    def save():
        try:
            update_inventory_entry(inventory_id,row[0],getv("quantity"),getv("condition"),getv("location"),getv("notes"))
            if on_saved: on_saved()
            win.destroy()
        except Exception as exc:
            messagebox.showerror("Inventar",str(exc),parent=win)
    bottom=ttk.Frame(win,padding=(14,8)); bottom.pack(side="bottom",fill="x")
    ttk.Button(bottom,text="Speichern",command=save).pack(side="right",padx=5)
    ttk.Button(bottom,text="Abbrechen",command=win.destroy).pack(side="right",padx=5)
    win.bind("<Return>", lambda e: save())


def open_manual_inventory_dialog(parent=None, on_saved=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title("Inventar manuell hinzufügen / bearbeiten")
    win.geometry("620x360")
    maximize_window(win)
    frm = ttk.Frame(win, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    fields = [
        ("Karten-ID", "card_id"), ("Menge", "quantity"),
        ("Zustand", "condition"), ("Lagerort", "location"),
        ("Notizen", "notes")
    ]
    entries = {}
    for row, (label, key) in enumerate(fields):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=(0,10), pady=5)
        e = ttk.Entry(frm, width=48)
        e.grid(row=row, column=1, sticky="ew", pady=5)
        entries[key] = e

    def save():
        try:
            iid = add_manual_inventory(
                entries["card_id"].get(), entries["quantity"].get(),
                entries["condition"].get(), entries["location"].get(),
                entries["notes"].get()
            )
            messagebox.showinfo("Inventar gespeichert",
                                f"Inventareintrag gespeichert.\n\nInventar-ID: {iid}", parent=win)
            win.destroy()
            if on_saved:
                on_saved()
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc), parent=win)

    ttk.Button(frm, text="Inventar speichern", command=save).grid(
        row=len(fields), column=1, sticky="e", pady=12
    )
    if parent is not None:
        win.transient(parent)
        win.grab_set()
    return win


def update_purchase(purchase_id, data):
    c = db()
    try:
        def money(v):
            try: return float(str(v).replace(",", ".")) if str(v).strip() else 0
            except (TypeError, ValueError): return 0
        c.execute(
            """UPDATE purchases SET purchase_date=?, platform=?, seller=?, card_count=?,
               purchase_price=?, shipping=?, total_price=?, notes=? WHERE purchase_id=?""",
            (data.get("purchase_date", ""), data.get("platform", ""), data.get("seller", ""),
             int(data.get("card_count", 1) or 1), money(data.get("purchase_price")),
             money(data.get("shipping")), money(data.get("total_price")), data.get("notes", ""), int(purchase_id))
        )
        c.commit()
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def open_purchase_editor(parent, purchase_id, on_saved=None):
    win=tk.Toplevel(parent); win.title(f"Kauf bearbeiten – #{purchase_id}")
    fit_dialog(win,620,470,min_width=560,min_height=420); win.transient(parent); win.grab_set()
    frm=ttk.Frame(win,padding=14); frm.pack(fill="both",expand=True); frm.columnconfigure(1,weight=1)
    c=db(); row=c.execute("SELECT purchase_date,platform,seller,card_count,purchase_price,shipping,total_price,notes FROM purchases WHERE purchase_id=?",(int(purchase_id),)).fetchone(); c.close()
    if not row:
        messagebox.showerror("Kauf",f"Kauf #{purchase_id} wurde nicht gefunden.",parent=win); win.destroy(); return
    fields=[("Kaufdatum","purchase_date"),("Plattform / Quelle","platform"),("Verkäufer","seller"),("Anzahl Karten","card_count"),("Kaufpreis","purchase_price"),("Versand","shipping"),("Gesamtpreis","total_price"),("Notizen","notes")]
    entries={}
    for r,(label,key) in enumerate(fields):
        ttk.Label(frm,text=label).grid(row=r,column=0,sticky="w",padx=(0,10),pady=5)
        w=tk.Text(frm,height=4) if key=="notes" else ttk.Entry(frm)
        w.grid(row=r,column=1,sticky="ew",pady=5); entries[key]=w
        val=row[r]
        if isinstance(w,tk.Text): w.insert("1.0", "" if val is None else str(val))
        else: w.insert(0, "" if val is None else str(val))
    def get(key):
        w=entries[key]; return w.get("1.0","end").strip() if isinstance(w,tk.Text) else w.get().strip()
    def save():
        try:
            update_purchase(purchase_id,{k:get(k) for _,k in fields})
            if on_saved: on_saved()
            win.destroy()
        except Exception as exc: messagebox.showerror("Kauf",str(exc),parent=win)
    bottom=ttk.Frame(win,padding=(14,8)); bottom.pack(side="bottom",fill="x")
    ttk.Button(bottom,text="Speichern",command=save).pack(side="right",padx=5)
    ttk.Button(bottom,text="Abbrechen",command=win.destroy).pack(side="right",padx=5)


def open_manual_purchase_dialog(parent=None, on_saved=None):
    import tkinter as tk
    from tkinter import ttk, messagebox
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title("Kauf manuell hinzufügen")
    win.geometry("620x440")
    maximize_window(win)
    frm = ttk.Frame(win, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    fields = [
        ("Kaufdatum", "purchase_date"), ("Plattform / Quelle", "platform"),
        ("Verkäufer", "seller"), ("Anzahl Karten", "card_count"),
        ("Kaufpreis", "purchase_price"), ("Versand", "shipping"),
        ("Gesamtpreis", "total_price"), ("Notizen", "notes")
    ]
    entries = {}
    for row, (label, key) in enumerate(fields):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=(0,10), pady=5)
        e = ttk.Entry(frm, width=48)
        e.grid(row=row, column=1, sticky="ew", pady=5)
        entries[key] = e

    def save():
        try:
            pid = add_manual_purchase({k: e.get() for k, e in entries.items()})
            messagebox.showinfo("Kauf gespeichert",
                                f"Kauf wurde gespeichert.\n\nKauf-ID: {pid}", parent=win)
            win.destroy()
            if on_saved:
                on_saved()
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc), parent=win)

    ttk.Button(frm, text="Kauf speichern", command=save).grid(
        row=len(fields), column=1, sticky="e", pady=12
    )
    if parent is not None:
        win.transient(parent)
        win.grab_set()
    return win


def update_inventory_card(card_id, **changes):
    """Update purchase, condition, storage and sale fields."""
    allowed = {
        "purchase_price", "purchase_date", "purchase_source",
        "condition", "storage_location", "notes", "inventory_status",
        "sale_price", "sale_date", "sale_platform", "sold_status"
    }
    updates = {k: v for k, v in changes.items() if k in allowed}
    if not updates:
        return
    c = db()
    try:
        sql = "UPDATE cards SET " + ", ".join(f"{k}=?" for k in updates)
        sql += " WHERE card_id=?"
        c.execute(sql, list(updates.values()) + [card_id])
        c.commit()
    finally:
        c.close()


def open_manual_card_dialog(parent=None):
    """Dialog for manually adding a card with a scrollable form."""
    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title("Karte manuell hinzufügen")
    fit_dialog(win, 780, 820, min_width=680, min_height=560)

    # Fixed action bar: always visible, never part of the scrollable content.
    actions = ttk.Frame(win, padding=(14, 8))
    actions.pack(side="bottom", fill="x")

    # Scrollable form. This fixes the lower fields/images being inaccessible
    # on smaller screens or with Windows display scaling.
    outer = ttk.Frame(win)
    outer.pack(side="top", fill="both", expand=True)
    canvas = tk.Canvas(outer, highlightthickness=0)
    scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
    form = ttk.Frame(canvas, padding=14)
    form.columnconfigure(1, weight=1)
    canvas_window = canvas.create_window((0, 0), window=form, anchor="nw")

    def on_form_configure(_event=None):
        canvas.configure(scrollregion=canvas.bbox("all"))

    def on_canvas_configure(event):
        canvas.itemconfigure(canvas_window, width=event.width)

    form.bind("<Configure>", on_form_configure)
    canvas.bind("<Configure>", on_canvas_configure)
    canvas.configure(yscrollcommand=scrollbar.set)
    canvas.pack(side="left", fill="both", expand=True)
    scrollbar.pack(side="right", fill="y")

    def wheel(event):
        canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")
        return "break"
    canvas.bind_all("<MouseWheel>", wheel)

    fields = [
        ("Name / Titel","title"),("Kategorie","category"),
        ("Hersteller","manufacturer"),("Set / Serie","set_name"),
        ("Kartentyp","card_type"),("Variante","variant"),
        ("Saison","season_year"),("Team / Verein","team"),
        ("Position","position"),("Squad Number","squad_number"),
        ("Kartennummer","card_number"),("Seriennummer","serial_number"),
        ("Print Run","print_run"),("Zustand","condition"),
        ("Kaufpreis","purchase_price"),("Kaufdatum","purchase_date"),
        ("Quelle / Händler","purchase_source"),("Lagerort","storage_location"),
        ("Notizen","notes")
    ]
    entries = {}
    for row, (label, key) in enumerate(fields):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0,10), pady=4)
        e = ttk.Entry(form, width=56)
        e.grid(row=row, column=1, sticky="ew", pady=4)
        entries[key] = e

    image_vars = {"front_image": tk.StringVar(), "back_image": tk.StringVar()}
    def choose_image(key, label):
        path = filedialog.askopenfilename(
            parent=win, title=f"{label} auswählen",
            filetypes=[("Bilddateien", "*.jpg *.jpeg *.png *.webp"),
                       ("Alle Dateien", "*.*")]
        )
        if path:
            image_vars[key].set(path)

    row = len(fields)
    for label, key in (("Vorderseite", "front_image"), ("Rückseite", "back_image")):
        ttk.Label(form, text=label).grid(row=row, column=0, sticky="w", padx=(0,10), pady=4)
        ttk.Entry(form, textvariable=image_vars[key], state="readonly").grid(
            row=row, column=1, sticky="ew", pady=4)
        ttk.Button(form, text="Auswählen…",
                   command=lambda k=key, l=label: choose_image(k, l)).grid(
            row=row, column=2, padx=8, pady=4)
        row += 1

    numbered = tk.BooleanVar(value=False)
    ttk.Checkbutton(form, text="Numbered", variable=numbered).grid(
        row=row, column=1, sticky="w", pady=6)

    def save():
        data = {key: e.get() for key, e in entries.items()}
        data["is_numbered"] = numbered.get()
        data["front_image"] = image_vars["front_image"].get()
        data["back_image"] = image_vars["back_image"].get()
        try:
            cid = add_manual_card(data)
            messagebox.showinfo(
                "Karte gespeichert",
                f"Karte wurde gespeichert.\n\nKarten-ID: {cid}",
                parent=win
            )
            win.destroy()
        except Exception as exc:
            messagebox.showerror("Fehler", str(exc), parent=win)

    ttk.Button(actions, text="💾 Karte speichern", command=save).pack(side="right", padx=4)
    ttk.Button(actions, text="Abbrechen", command=win.destroy).pack(side="right", padx=4)

    def close_dialog(_event=None):
        try:
            canvas.unbind_all("<MouseWheel>")
        except Exception:
            pass
        win.destroy()
    win.protocol("WM_DELETE_WINDOW", close_dialog)
    win.bind("<Escape>", close_dialog)

    if parent is not None:
        win.transient(parent)
        win.grab_set()
    return win


HANDY_IMPORT_CONFIG = BASE / "handy_import_config.json"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp"}


def load_handy_import_folder():
    try:
        cfg = json.loads(HANDY_IMPORT_CONFIG.read_text(encoding="utf-8"))
        folder = cfg.get("folder", "")
        return Path(folder) if folder else None
    except Exception:
        return None


def save_handy_import_folder(folder):
    HANDY_IMPORT_CONFIG.write_text(
        json.dumps({"folder": str(folder)}, indent=2), encoding="utf-8"
    )


def scan_handy_import_folder(folder):
    """
    Findet neue Bilder im Handy-Import-Ordner (z.B. ein Dropbox-/Google
    Drive-/OneDrive-Ordner, in den das Handy automatisch Fotos ablegt)
    und gruppiert sie chronologisch zu Zweierpaaren (Vorderseite,
    Rueckseite) - in der Annahme, dass am Handy pro Karte zuerst die
    Vorder-, dann die Rueckseite fotografiert wird. Falsch zugeordnete
    Paare lassen sich im Importdialog per Klick tauschen.
    Bereits importierte Originale liegen im Unterordner 'importiert/'
    und werden hier nicht mehr beruecksichtigt.
    """
    folder = Path(folder)
    if not folder.is_dir():
        return [], []
    files = sorted(
        (p for p in folder.iterdir()
         if p.is_file() and p.suffix.lower() in IMAGE_EXTS),
        key=lambda p: p.stat().st_mtime
    )
    pairs = []
    for i in range(0, len(files) - 1, 2):
        pairs.append((files[i], files[i + 1]))
    leftover = files[len(pairs) * 2:]
    return pairs, leftover


def archive_handy_import_files(folder, *paths):
    """Verschiebt importierte Originalfotos nach 'importiert/' und gibt
    die neuen Pfade in derselben Reihenfolge zurück."""
    target = Path(folder) / "importiert"
    target.mkdir(exist_ok=True)
    result = []
    for p in paths:
        p = Path(p)
        dest = target / p.name
        try:
            if p.resolve() != dest.resolve():
                shutil.move(str(p), str(dest))
            result.append(dest)
        except Exception:
            # Falls die Datei bereits archiviert wurde, den Zielpfad weiterverwenden.
            if dest.exists():
                result.append(dest)
            else:
                result.append(p)
    return result


def open_handy_import_dialog(parent=None):
    """
    Liest den konfigurierten Handy-Import-Ordner ein, zeigt gefundene
    Kartenpaare nacheinander mit Bildvorschau und OCR-Namensvorschlag
    (ueber die bestehende ocr_name()) und speichert sie ueber
    add_manual_card() - denselben Weg wie beim manuellen Hinzufuegen.
    """
    folder = load_handy_import_folder()
    if folder is None or not Path(folder).is_dir():
        chosen = filedialog.askdirectory(
            parent=parent,
            title="Handy-Import-Ordner auswählen (z.B. Dropbox/Google Drive-Ordner)"
        )
        if not chosen:
            return
        folder = Path(chosen)
        save_handy_import_folder(folder)

    pairs, leftover = scan_handy_import_folder(folder)
    if not pairs:
        msg = "Keine neuen Kartenpaare im Import-Ordner gefunden."
        if leftover:
            msg += (
                f"\n\n{len(leftover)} übrig gebliebene Einzeldatei(en) "
                "ohne Partnerbild (ungerade Anzahl Fotos)."
            )
        messagebox.showinfo("Handy-Import", msg, parent=parent)
        return

    state = {"index": 0, "swapped": False, "initial": None}

    win = tk.Toplevel(parent) if parent is not None else tk.Tk()
    win.title("Aus Handy-Ordner importieren")
    fit_dialog(win, 1100, 820, min_width=900, min_height=620)

    info = ttk.Label(win, text="")
    info.pack(anchor="w", padx=14, pady=(12, 4))

    preview_frame = ttk.Frame(win)
    preview_frame.pack(padx=14, pady=6)
    front_preview = ttk.Label(preview_frame, text="Vorderseite")
    front_preview.grid(row=0, column=0, padx=8)
    back_preview = ttk.Label(preview_frame, text="Rückseite")
    back_preview.grid(row=0, column=1, padx=8)

    frm = ttk.Frame(win, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    fields = [
        ("Name / Titel", "title"), ("Kategorie", "category"),
        ("Team / Verein", "team"), ("Saison", "season_year"),
        ("Kartennummer", "card_number"), ("Zustand", "condition"),
    ]
    entries = {}
    for row, (label, key) in enumerate(fields):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=(0, 10), pady=4)
        e = ttk.Entry(frm, width=48)
        e.grid(row=row, column=1, sticky="ew", pady=4)
        entries[key] = e

    thumbs = {}  # verhindert, dass PhotoImage vom Garbage Collector entfernt wird
    prev_btn = None
    next_btn = None

    def load_preview(path, widget):
        if Image is None or ImageTk is None:
            widget.configure(text=path.name)
            return
        try:
            img = Image.open(path)
            img.thumbnail((260, 260))
            photo = ImageTk.PhotoImage(img)
            thumbs[widget] = photo
            widget.configure(image=photo, text="")
        except Exception:
            widget.configure(text=path.name)

    def current_pair():
        front, back = pairs[state["index"]]
        if state["swapped"]:
            front, back = back, front
        return front, back

    def current_values():
        return tuple(e.get() for e in entries.values())

    # OCR cache: compute both sides once in the background when the dialog opens.
    # This removes the previous delay on every Next/Previous click.
    ocr_cache = {}
    ocr_lock = threading.Lock()
    ocr_started = set()

    def ocr_worker():
        for pair in pairs:
            for path in pair:
                key = str(path.resolve()) if path.exists() else str(path)
                with ocr_lock:
                    if key in ocr_started or key in ocr_cache:
                        continue
                    ocr_started.add(key)
                try:
                    img = cv2.imread(str(path))
                    if img is None:
                        result = ("", "Bild nicht lesbar", 0)
                    else:
                        name, status, conf, _raw = ocr_name(img)
                        result = (name, status, conf)
                except Exception as exc:
                    LOGGER.exception("Handy-Import OCR fehlgeschlagen: %s", path)
                    result = ("", "OCR-Fehler", 0)
                with ocr_lock:
                    ocr_cache[key] = result

    threading.Thread(target=ocr_worker, name="DCardLabs-HandyOCR", daemon=True).start()

    def refresh_current_ocr():
        if not win.winfo_exists() or state["index"] >= len(pairs):
            return
        front, _back = current_pair()
        key = str(front.resolve()) if front.exists() else str(front)
        cached = ocr_cache.get(key)
        if cached is None:
            win.after(120, refresh_current_ocr)
            return
        # Only fill the title if the user has not entered something manually.
        if not entries["title"].get().strip():
            name, status, conf = cached
            if name:
                entries["title"].insert(0, name)
            info.configure(text=info.cget("text").split("   |  OCR")[0] + f"   |  OCR: {status} ({conf}%)")

    def show_current():
        if state["index"] >= len(pairs):
            win.destroy()
            messagebox.showinfo(
                "Handy-Import", "Alle gefundenen Kartenpaare wurden bearbeitet.",
                parent=parent
            )
            return

        state["swapped"] = False
        front, back = current_pair()
        info.configure(
            text=f"Karte {state['index'] + 1} von {len(pairs)}  –  {front.name} / {back.name}"
        )
        load_preview(front, front_preview)
        load_preview(back, back_preview)

        for e in entries.values():
            e.delete(0, "end")

        # OCR is precomputed in a background thread for all import images.
        # Navigation therefore never waits for Tesseract.
        ocr_key = str(front.resolve()) if front.exists() else str(front)
        cached = ocr_cache.get(ocr_key)
        if cached is not None:
            name, ocr_status, conf = cached
            if name:
                entries["title"].insert(0, name)
            info.configure(
                text=info.cget("text") + f"   |  OCR: {ocr_status} ({conf}%)"
            )
        else:
            info.configure(text=info.cget("text") + "   |  OCR wird im Hintergrund vorbereitet …")
            win.after(120, refresh_current_ocr)

        state["initial"] = current_values()
        prev_btn.configure(state="normal" if state["index"] > 0 else "disabled")
        next_btn.configure(
            state="normal" if state["index"] < len(pairs) - 1 else "disabled"
        )

    def swap():
        state["swapped"] = not state["swapped"]
        front, back = current_pair()
        load_preview(front, front_preview)
        load_preview(back, back_preview)

    def save_current(silent=False):
        front, back = current_pair()
        data = {key: e.get() for key, e in entries.items()}
        data["front_image"] = str(front)
        data["back_image"] = str(back)
        try:
            card_id = add_manual_card(data)
            archived = archive_handy_import_files(folder, front, back)
            # Die Navigation soll auch nach dem Speichern weiter funktionieren.
            # Deshalb werden die Paarpfade auf die archivierten Dateien umgestellt.
            if len(archived) == 2:
                pairs[state["index"]] = (Path(archived[0]), Path(archived[1]))
                state["swapped"] = False
            state["initial"] = current_values()
            if not silent:
                messagebox.showinfo(
                    "Handy-Import",
                    f"Karte wurde gespeichert.\n\nKarten-ID: {card_id}",
                    parent=win
                )
            return True
        except Exception as exc:
            LOGGER.exception("Handy-Import: Speichern fehlgeschlagen")
            messagebox.showerror("Fehler", str(exc), parent=win)
            return False

    def has_unsaved_changes():
        return state["initial"] is not None and current_values() != state["initial"]

    def navigate(direction):
        target = state["index"] + direction
        if target < 0 or target >= len(pairs):
            return

        if has_unsaved_changes():
            answer = messagebox.askyesnocancel(
                "Ungespeicherte Änderungen",
                "Du hast Änderungen an dieser Karte vorgenommen.\n\n"
                "Ja = speichern und wechseln\n"
                "Nein = Änderungen verwerfen und wechseln\n"
                "Abbrechen = aktuelle Karte weiter bearbeiten",
                parent=win
            )
            if answer is None:
                return
            if answer and not save_current(silent=True):
                return

        state["index"] = target
        show_current()

    def save_and_next():
        if not save_current(silent=True):
            return
        state["index"] += 1
        show_current()

    def skip():
        if has_unsaved_changes():
            answer = messagebox.askyesnocancel(
                "Ungespeicherte Änderungen",
                "Die Änderungen wurden noch nicht gespeichert.\n\n"
                "Ja = speichern und überspringen\n"
                "Nein = verwerfen und überspringen\n"
                "Abbrechen = zurück zur Karte",
                parent=win
            )
            if answer is None:
                return
            if answer and not save_current(silent=True):
                return
        state["index"] += 1
        show_current()

    # Feste Navigations-/Aktionsleiste: Speichern ist immer sichtbar.
    btns = ttk.Frame(win, padding=(14, 8))
    btns.pack(side="bottom", fill="x")

    prev_btn = ttk.Button(btns, text="◀ Vorherige Karte", command=lambda: navigate(-1))
    prev_btn.pack(side="left", padx=4)
    ttk.Button(btns, text="⇄ Vorder-/Rückseite tauschen", command=swap).pack(side="left", padx=4)
    next_btn = ttk.Button(btns, text="Nächste Karte ▶", command=lambda: navigate(1))
    next_btn.pack(side="left", padx=4)

    ttk.Button(btns, text="Abbrechen", command=win.destroy).pack(side="right", padx=4)
    ttk.Button(btns, text="Überspringen", command=skip).pack(side="right", padx=4)
    ttk.Button(btns, text="Karte speichern & weiter", command=save_and_next).pack(side="right", padx=4)
    ttk.Button(btns, text="💾 Karte speichern", command=lambda: save_current(silent=False)).pack(side="right", padx=4)

    win.bind("<Alt-Left>", lambda e: navigate(-1))
    win.bind("<Alt-Right>", lambda e: navigate(1))
    win.bind("<Escape>", lambda e: win.destroy())

    if parent is not None:
        win.transient(parent)
        win.grab_set()

    show_current()
    return win


def delete_card(card_id):
    """Delete a card and its dependent inventory records."""
    c = db()
    try:
        card = c.execute(
            "SELECT title FROM cards WHERE card_id=?", (card_id,)
        ).fetchone()
        if not card:
            raise ValueError(f"Karte mit ID {card_id} existiert nicht.")

        c.execute("DELETE FROM inventory WHERE card_id=?", (card_id,))
        c.execute("DELETE FROM cards WHERE card_id=?", (card_id,))
        c.commit()
        return card[0]
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def delete_inventory(inventory_id):
    """Delete one inventory record."""
    c = db()
    try:
        row = c.execute(
            "SELECT inventory_id FROM inventory WHERE inventory_id=?",
            (inventory_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Inventareintrag {inventory_id} existiert nicht.")
        c.execute("DELETE FROM inventory WHERE inventory_id=?", (inventory_id,))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def delete_purchase(purchase_id):
    """Delete one purchase record."""
    c = db()
    try:
        row = c.execute(
            "SELECT purchase_id FROM purchases WHERE purchase_id=?",
            (purchase_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"Kauf {purchase_id} existiert nicht.")
        c.execute("DELETE FROM purchases WHERE purchase_id=?", (purchase_id,))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def delete_ebay_listing(listing_id):
    """Delete one eBay listing/draft record without deleting the card itself."""
    c = db()
    try:
        row = c.execute(
            "SELECT listing_id FROM ebay_listings WHERE listing_id=?",
            (listing_id,)
        ).fetchone()
        if not row:
            raise ValueError(f"eBay-Datensatz {listing_id} existiert nicht.")
        c.execute("DELETE FROM ebay_listings WHERE listing_id=?", (listing_id,))
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def delete_ebay_sale(sale_id):
    c = db()
    try:
        c.execute("DELETE FROM ebay_sales WHERE sale_id=?", (int(sale_id),))
        c.commit()
    finally:
        c.close()


def confirm_and_delete(parent, kind, record_id, on_deleted=None):
    """UI confirmation for deletion of a card, inventory, purchase or eBay draft."""
    from tkinter import messagebox

    labels = {
        "card": "Karte",
        "inventory": "Inventareintrag",
        "purchase": "Kauf",
        "ebay": "eBay-Datensatz",
        "ebay_sales": "Verkauf"
    }
    label = labels.get(kind, "Datensatz")

    if kind == "card":
        question = (
            f"{label} #{record_id} wirklich löschen?\n\n"
            "Der zugehörige Inventareintrag wird ebenfalls gelöscht.\n"
            "Diese Aktion kann nicht rückgängig gemacht werden."
        )
    else:
        question = (
            f"{label} #{record_id} wirklich löschen?\n\n"
            "Diese Aktion kann nicht rückgängig gemacht werden."
        )

    if not messagebox.askyesno("Datensatz löschen", question, parent=parent):
        return False

    try:
        if kind == "card":
            delete_card(record_id)
        elif kind == "inventory":
            delete_inventory(record_id)
        elif kind == "purchase":
            delete_purchase(record_id)
        elif kind == "ebay":
            delete_ebay_listing(record_id)
        elif kind == "ebay_sales":
            delete_ebay_sale(record_id)
        else:
            raise ValueError("Unbekannter Datensatztyp.")

        if on_deleted:
            on_deleted()
        return True
    except Exception as exc:
        messagebox.showerror(
            "Löschen fehlgeschlagen",
            str(exc),
            parent=parent
        )
        return False


def update_card(card_id, values, front_image=None, back_image=None):
    """Persist card fields and optional front/back image changes.

    Image arguments:
      None -> leave unchanged
      ""   -> remove image
      path -> replace/select image
    """
    LOGGER.info("Karte %s speichern: front=%r back=%r", card_id, front_image, back_image)
    c = db()
    old_images = {"front": "", "back": ""}
    new_images = dict(old_images)

    try:
        old = c.execute(
            "SELECT front_image, back_image FROM cards WHERE card_id=?",
            (card_id,)
        ).fetchone()
        if old:
            old_images = {"front": old[0] or "", "back": old[1] or ""}
            new_images = dict(old_images)

        c.execute(
            """
            UPDATE cards SET
                category=?, theme=?, team=?, manufacturer=?, set_name=?, title=?,
                season_year=?, card_number=?, card_type=?, variant=?,
                is_numbered=?, serial_number=?, print_run=?, language=?
            WHERE card_id=?
            """,
            (*values, card_id)
        )

        changed_sides = set()

        for side, source in (("front", front_image), ("back", back_image)):
            if source is None:
                continue

            old_ref = old_images.get(side, "")
            if source == "":
                new_ref = ""
                digest = ""
                changed_sides.add(side)
            else:
                source_path = resolve_image_ref(source)
                old_path = resolve_image_ref(old_ref)

                # If the user simply saved without changing the image, do not
                # copy the managed file onto itself.
                same_as_old = False
                if source_path and old_path:
                    try:
                        same_as_old = source_path.resolve() == old_path.resolve()
                    except OSError:
                        same_as_old = False

                if same_as_old and old_ref:
                    new_ref = old_ref
                    digest = sha256_file(old_path)
                else:
                    new_ref, digest = store_card_image(source, card_id, side)
                    changed_sides.add(side)

            c.execute(
                f"""UPDATE cards
                    SET {side}_image=?, {side}_image_sha256=?
                    WHERE card_id=?""",
                (new_ref, digest, card_id)
            )
            new_images[side] = new_ref

        c.commit()
        LOGGER.info(
            "Karte %s DB-Commit erfolgreich: front=%r back=%r",
            card_id, new_images["front"], new_images["back"]
        )

        verify = c.execute(
            "SELECT front_image, back_image FROM cards WHERE card_id=?",
            (card_id,)
        ).fetchone()

        expected = (new_images["front"], new_images["back"])
        if tuple(verify or ("", "")) != expected:
            raise RuntimeError(
                "Die Bildänderung wurde nicht korrekt in der Datenbank gespeichert."
            )

    except Exception as exc:
        c.rollback()
        log_exception(f"Speichern von Karte #{card_id} fehlgeschlagen", exc)
        raise
    finally:
        c.close()

    # Only after a successful DB commit remove old managed files that are no
    # longer referenced. This also cleans up old extensions after replacement.
    for side in changed_sides:
        old_ref = old_images.get(side, "")
        new_ref = new_images.get(side, "")
        if not old_ref or old_ref == new_ref:
            continue
        old_path = resolve_image_ref(old_ref)
        if old_path and old_path.exists():
            try:
                if IMAGE_ROOT.resolve() in old_path.resolve().parents:
                    old_path.unlink()
            except OSError:
                pass


def ebay_generate_title(card, max_len=80):
    """Create a conservative, factual eBay title from existing card data."""
    parts = []
    for key in ("manufacturer", "set_name", "season_year", "title",
                "card_number", "variant", "category", "theme"):
        value = str(card.get(key, "") or "").strip()
        if not value or value.lower() in {"none", "null", "-"}:
            continue
        if value not in parts:
            parts.append(value)
    title = re.sub(r"\s+", " ", " ".join(parts)).strip()
    if len(title) <= max_len:
        return title
    short = title[:max_len].rstrip()
    if " " in short:
        short = short.rsplit(" ", 1)[0]
    return short.rstrip(" -/,")[:max_len]


def ebay_generate_description(card, condition="NM"):
    lines = [
        f"Zum Verkauf steht: {card.get('title') or 'Sammelkarte'}",
        "",
        "Kartendaten:",
    ]
    labels = [
        ("Kategorie", "category"), ("Thema / Franchise", "theme"),
        ("Team / Verein", "team"), ("Hersteller", "manufacturer"),
        ("Set / Serie", "set_name"), ("Saison / Jahr", "season_year"),
        ("Kartennummer", "card_number"), ("Kartentyp", "card_type"),
        ("Variante", "variant"), ("Sprache", "language"),
    ]
    for label, key in labels:
        value = str(card.get(key, "") or "").strip()
        if value and value.lower() not in {"none", "null", "-"}:
            lines.append(f"{label}: {value}")
    if int(card.get("is_numbered") or 0):
        if card.get("serial_number") not in (None, "", "None"):
            lines.append(f"Seriennummer: {card.get('serial_number')}")
        if card.get("print_run") not in (None, "", "None"):
            lines.append(f"Print Run: {card.get('print_run')}")
    lines += [
        "",
        f"Zustand: {condition}",
        "",
        "Die abgebildete Karte ist Bestandteil des Angebots. "
        "Vorder- und Rückseite sind auf den Fotos zu sehen.",
        "",
        "Bitte die Fotos und Angaben vor dem Kauf prüfen. "
        "Bei Fragen zur Karte gerne melden.",
    ]
    return "\n".join(lines)


def ebay_get_card(card_id):
    c=db()
    row=c.execute(
        """SELECT category, theme, team, manufacturer, set_name, title,
                  season_year, card_number, card_type, variant,
                  is_numbered, serial_number, print_run, language,
                  front_image, back_image
           FROM cards WHERE card_id=?""",(card_id,)
    ).fetchone()
    inv=c.execute(
        "SELECT condition FROM inventory WHERE card_id=? "
        "ORDER BY inventory_id ASC LIMIT 1",(card_id,)
    ).fetchone()
    c.close()
    if not row:
        return None
    keys=[
        "category","theme","team","manufacturer","set_name","title",
        "season_year","card_number","card_type","variant","is_numbered",
        "serial_number","print_run","language","front_image","back_image"
    ]
    data=dict(zip(keys,row))
    data["condition"]=(inv[0] if inv else "NM") or "NM"
    return data


def ebay_get_settings():
    c = db()
    row = c.execute(
        """SELECT category_name, category_id, condition_ungraded_id,
                  condition_graded_id FROM ebay_settings WHERE settings_id=1"""
    ).fetchone()
    c.close()
    if not row:
        return {
            "category_name": "Trading Card Einzelkarten",
            "category_id": "261328",
            "condition_ungraded_id": "4000",
            "condition_graded_id": "2750",
        }
    return dict(zip(
        ["category_name", "category_id", "condition_ungraded_id", "condition_graded_id"],
        row
    ))


def ebay_save_settings(category_name, category_id, ungraded_id, graded_id):
    now = datetime.now().isoformat(timespec="seconds")
    c = db()
    try:
        c.execute(
            """UPDATE ebay_settings
               SET category_name=?, category_id=?, condition_ungraded_id=?,
                   condition_graded_id=?, updated_at=?
               WHERE settings_id=1""",
            (category_name.strip(), category_id.strip(), ungraded_id.strip(),
             graded_id.strip(), now)
        )
        c.commit()
    finally:
        c.close()



def open_manual_sale_dialog(parent, on_saved=None):
    """Create a manual sale record with the same fixed action-bar pattern as purchases."""
    win = tk.Toplevel(parent)
    win.title("Verkauf hinzufügen")
    fit_dialog(win, 720, 650, min_width=620, min_height=520)
    win.transient(parent)
    win.grab_set()

    frm = ttk.Frame(win, padding=14)
    frm.pack(fill="both", expand=True)
    frm.columnconfigure(1, weight=1)

    fields = [
        ("Karten-ID", "card_id"),
        ("eBay Item ID", "ebay_item_id"),
        ("Order ID", "ebay_order_id"),
        ("Verkaufsdatum", "sale_date"),
        ("Menge", "quantity"),
        ("Brutto (€)", "gross_price"),
        ("Versand (€)", "shipping_charged"),
        ("eBay-Gebühren (€)", "ebay_fees"),
        ("Status", "status"),
        ("Notizen", "notes"),
    ]
    entries = {}
    for row, (label, key) in enumerate(fields):
        ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", padx=5, pady=5)
        if key == "status":
            e = ttk.Combobox(frm, values=["Verkauft", "Storniert", "Erstattet"],
                             state="readonly")
        elif key == "notes":
            e = tk.Text(frm, height=4, width=45)
        else:
            e = ttk.Entry(frm)
        e.grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        entries[key] = e

    entries["sale_date"].insert(0, datetime.now().strftime("%Y-%m-%d"))
    entries["quantity"].insert(0, "1")
    entries["status"].set("Verkauft")
    net_var = tk.StringVar(value="0,00 €")
    ttk.Label(frm, text="Netto").grid(row=len(fields), column=0, sticky="w", padx=5, pady=5)
    ttk.Label(frm, textvariable=net_var, font=("", 10, "bold")).grid(
        row=len(fields), column=1, sticky="w", padx=5, pady=5
    )

    def value(key):
        w = entries[key]
        if isinstance(w, tk.Text):
            return w.get("1.0", "end").strip()
        return w.get().strip()

    def calc(*_):
        try:
            net = float(value("gross_price").replace(",", ".") or 0) + \
                  float(value("shipping_charged").replace(",", ".") or 0) - \
                  float(value("ebay_fees").replace(",", ".") or 0)
            net_var.set(f"{net:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
        except ValueError:
            net_var.set("–")

    for key in ("gross_price", "shipping_charged", "ebay_fees"):
        entries[key].bind("<KeyRelease>", calc)

    def save():
        try:
            card_id = int(value("card_id")) if value("card_id") else None
            quantity = int(value("quantity") or 1)
            gross = float(value("gross_price").replace(",", ".") or 0)
            shipping = float(value("shipping_charged").replace(",", ".") or 0)
            fees = float(value("ebay_fees").replace(",", ".") or 0)
            if quantity < 1:
                raise ValueError("Die Menge muss mindestens 1 sein.")
            if card_id is not None:
                c = db()
                exists = c.execute("SELECT 1 FROM cards WHERE card_id=?", (card_id,)).fetchone()
                c.close()
                if not exists:
                    raise ValueError(f"Karte #{card_id} wurde nicht gefunden.")
            sale_id = ebay_record_sale(
                card_id=card_id,
                ebay_item_id=value("ebay_item_id"),
                ebay_order_id=value("ebay_order_id"),
                sale_date=value("sale_date"),
                quantity=quantity,
                gross_price=gross,
                shipping_charged=shipping,
                ebay_fees=fees,
                notes=value("notes"),
            )
            # Respect manually selected status if it differs from the default.
            if value("status") != "Verkauft":
                c = db()
                c.execute("UPDATE ebay_sales SET status=? WHERE sale_id=?",
                          (value("status"), sale_id))
                c.commit(); c.close()
            if on_saved:
                on_saved()
            win.destroy()
        except Exception as exc:
            messagebox.showerror("Verkauf", str(exc), parent=win)

    bottom = ttk.Frame(win, padding=(14, 8))
    bottom.pack(side="bottom", fill="x")
    ttk.Button(bottom, text="Speichern", command=save).pack(side="right", padx=5)
    ttk.Button(bottom, text="Abbrechen", command=win.destroy).pack(side="right", padx=5)
    calc()


def open_sale_editor(parent, sale_ids, current_index, on_saved=None):
    """Edit sales with previous/next navigation, matching the card/eBay editors."""
    if not sale_ids or current_index < 0 or current_index >= len(sale_ids):
        return

    win = tk.Toplevel(parent)
    win.title("Verkauf bearbeiten")
    fit_dialog(win, 760, 680, min_width=650, min_height=540)
    win.transient(parent)

    outer = ttk.Frame(win, padding=14)
    outer.pack(fill="both", expand=True)
    outer.columnconfigure(1, weight=1)

    header = ttk.Frame(outer)
    header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
    header.columnconfigure(0, weight=1)
    record_label = ttk.Label(header, text="", font=("", 12, "bold"))
    record_label.grid(row=0, column=0, sticky="w")
    nav = ttk.Frame(header)
    nav.grid(row=0, column=1, sticky="e")

    fields = [
        ("Karten-ID", "card_id"), ("eBay Item ID", "ebay_item_id"),
        ("Order ID", "ebay_order_id"), ("Verkaufsdatum", "sale_date"),
        ("Menge", "quantity"), ("Brutto (€)", "gross_price"),
        ("Versand (€)", "shipping_charged"), ("eBay-Gebühren (€)", "ebay_fees"),
        ("Status", "status"), ("Notizen", "notes"),
    ]
    entries = {}
    for row, (label, key) in enumerate(fields, start=1):
        ttk.Label(outer, text=label).grid(row=row, column=0, sticky="w", padx=5, pady=5)
        if key == "status":
            w = ttk.Combobox(outer, values=["Verkauft", "Storniert", "Erstattet"],
                             state="readonly")
        elif key == "notes":
            w = tk.Text(outer, height=5)
        else:
            w = ttk.Entry(outer)
        w.grid(row=row, column=1, sticky="ew", padx=5, pady=5)
        entries[key] = w

    net_var = tk.StringVar()
    net_row = len(fields) + 1
    ttk.Label(outer, text="Netto").grid(row=net_row, column=0, sticky="w", padx=5, pady=5)
    ttk.Label(outer, textvariable=net_var, font=("", 10, "bold")).grid(
        row=net_row, column=1, sticky="w", padx=5, pady=5
    )

    state = {"index": current_index}

    def get(key):
        w = entries[key]
        return w.get("1.0", "end").strip() if isinstance(w, tk.Text) else w.get().strip()

    def setv(key, val):
        w = entries[key]
        if isinstance(w, tk.Text):
            w.delete("1.0", "end"); w.insert("1.0", "" if val is None else str(val))
        else:
            w.delete(0, "end"); w.insert(0, "" if val is None else str(val))

    def calc(*_):
        try:
            net = float(get("gross_price").replace(",", ".") or 0) + \
                  float(get("shipping_charged").replace(",", ".") or 0) - \
                  float(get("ebay_fees").replace(",", ".") or 0)
            net_var.set(f"{net:,.2f} €".replace(",", "X").replace(".", ",").replace("X", "."))
        except ValueError:
            net_var.set("–")

    for key in ("gross_price", "shipping_charged", "ebay_fees"):
        entries[key].bind("<KeyRelease>", calc)

    def load(index):
        state["index"] = index
        sale_id = sale_ids[index]
        c = db()
        row = c.execute(
            """SELECT card_id, ebay_item_id, ebay_order_id, sale_date, quantity,
                      gross_price, shipping_charged, ebay_fees, status, notes
               FROM ebay_sales WHERE sale_id=?""", (sale_id,)
        ).fetchone()
        c.close()
        if not row:
            messagebox.showerror("Verkauf", f"Verkauf #{sale_id} wurde nicht gefunden.", parent=win)
            return
        for key, val in zip([f[1] for f in fields], row):
            setv(key, val)
        record_label.configure(
            text=f"Verkauf #{sale_id}  •  {index + 1} von {len(sale_ids)}"
        )
        prev_btn.configure(state="normal" if index > 0 else "disabled")
        next_btn.configure(state="normal" if index < len(sale_ids)-1 else "disabled")
        calc()

    def save():
        try:
            card_id = int(get("card_id")) if get("card_id") else None
            quantity = int(get("quantity") or 1)
            gross = float(get("gross_price").replace(",", ".") or 0)
            shipping = float(get("shipping_charged").replace(",", ".") or 0)
            fees = float(get("ebay_fees").replace(",", ".") or 0)
            if quantity < 1:
                raise ValueError("Die Menge muss mindestens 1 sein.")
            now = datetime.now().isoformat(timespec="seconds")
            c = db()
            c.execute(
                """UPDATE ebay_sales
                   SET card_id=?, ebay_item_id=?, ebay_order_id=?, sale_date=?,
                       quantity=?, gross_price=?, shipping_charged=?, ebay_fees=?,
                       net_amount=?, status=?, notes=?
                   WHERE sale_id=?""",
                (card_id, get("ebay_item_id"), get("ebay_order_id"), get("sale_date"),
                 quantity, gross, shipping, fees, gross + shipping - fees,
                 get("status") or "Verkauft", get("notes"), sale_ids[state["index"]])
            )
            c.commit(); c.close()
            if on_saved:
                on_saved()
            # Refresh sale IDs from the current visible order after save.
            if on_saved:
                pass
            load(state["index"])
        except Exception as exc:
            messagebox.showerror("Verkauf", str(exc), parent=win)

    def previous():
        if state["index"] > 0:
            load(state["index"] - 1)

    def next_():
        if state["index"] < len(sale_ids) - 1:
            load(state["index"] + 1)

    prev_btn = ttk.Button(nav, text="◀ Vorherige", command=previous)
    prev_btn.pack(side="left", padx=4)
    next_btn = ttk.Button(nav, text="Nächste ▶", command=next_)
    next_btn.pack(side="left", padx=4)

    bottom = ttk.Frame(win, padding=(14, 8))
    bottom.pack(side="bottom", fill="x")
    ttk.Button(bottom, text="Speichern", command=save).pack(side="right", padx=5)
    ttk.Button(bottom, text="Schließen", command=win.destroy).pack(side="right", padx=5)

    win.bind("<Alt-Left>", lambda e: previous())
    win.bind("<Alt-Right>", lambda e: next_())
    load(current_index)

def ebay_record_sale(card_id=None, listing_id=None, ebay_item_id="", ebay_order_id="",
                     sale_date="", quantity=1, gross_price=0, shipping_charged=0,
                     ebay_fees=0, notes=""):
    """Store a future eBay sale import and update the linked listing/card status."""
    now = datetime.now().isoformat(timespec="seconds")
    net = float(gross_price or 0) + float(shipping_charged or 0) - float(ebay_fees or 0)
    c = db()
    try:
        cur = c.execute(
            """INSERT INTO ebay_sales
               (card_id,listing_id,ebay_item_id,ebay_order_id,sale_date,quantity,
                gross_price,shipping_charged,ebay_fees,net_amount,status,imported_at,notes)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (card_id, listing_id, str(ebay_item_id or ""), str(ebay_order_id or ""),
             sale_date or now, int(quantity or 1), float(gross_price or 0),
             float(shipping_charged or 0), float(ebay_fees or 0), net, "Verkauft", now, notes or "")
        )
        if listing_id:
            c.execute(
                """UPDATE ebay_listings SET status='Verkauft', sold_at=?, sale_price=?,
                   ebay_fees=?, ebay_item_id=?, ebay_order_id=?, updated_at=? WHERE listing_id=?""",
                (sale_date or now, float(gross_price or 0), float(ebay_fees or 0),
                 str(ebay_item_id or ""), str(ebay_order_id or ""), now, int(listing_id))
            )
        c.commit()
        return cur.lastrowid
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def link_card_to_purchase(purchase_id, card_id, allocated_cost=0, quantity=1, notes=""):
    """Link an individual card to a purchase for later margin calculations."""
    c = db()
    try:
        cur = c.execute(
            """INSERT INTO purchase_items(purchase_id,card_id,allocated_cost,quantity,notes)
               VALUES(?,?,?,?,?)""",
            (int(purchase_id), int(card_id), float(allocated_cost or 0), int(quantity or 1), notes or "")
        )
        c.commit(); return cur.lastrowid
    except Exception:
        c.rollback(); raise
    finally:
        c.close()


def ebay_sandbox_create_offer(card_id, title, description, condition_id, price,
                              listing_format, category_id, sku):
    """Create/update an eBay Sandbox inventory item and unpublished offer via the OAuth server."""
    card = ebay_get_card(card_id) or {}
    quantity = _ebay_inventory_quantity(int(card_id))
    if quantity < 1:
        raise ValueError("Die Karte hat keine verfügbare Inventarmenge.")

    aspects = {"Kategorie": [str(card.get("category") or "Sammelkarte")]}
    for label, key in (("Thema / Franchise", "theme"), ("Team / Verein", "team"),
                       ("Hersteller", "manufacturer"), ("Set / Serie", "set_name"),
                       ("Saison / Jahr", "season_year"), ("Kartennummer", "card_number"),
                       ("Sprache", "language")):
        value = str(card.get(key) or "").strip()
        if value:
            aspects[label] = [value]

    payload = {
        "sku": sku or f"DC-{int(card_id):06d}",
        "title": str(title or "").strip()[:80],
        "description": str(description or "").strip(),
        "condition": "NEW",
        "quantity": int(quantity),
        "marketplace_id": "EBAY_DE",
        "format": "FIXED_PRICE" if listing_format == "Festpreis" else "AUCTION",
        "category_id": str(category_id or "").strip(),
        "price": float(price),
        "currency": "EUR",
        "aspects": aspects,
    }
    if payload["price"] <= 0:
        raise ValueError("Bitte zuerst einen Preis größer als 0 eingeben.")
    if not payload["category_id"].isdigit():
        raise ValueError("Die eBay Kategorie-ID ist ungültig.")

    req = urllib.request.Request(
        EBAY_OAUTH_SERVER_URL + "/api/ebay/offer/test-create",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json", "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Der eBay-OAuth-Server ist nicht erreichbar ({EBAY_OAUTH_SERVER_URL}).\n\n{exc}") from exc

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"success": False, "error": raw}
    # eBay error 25002 means that an offer for this SKU already exists.
    # The response contains the existing offerId; recover and persist it.
    if status != 200 or not result.get("success"):
        detail = result.get("error") or result.get("offer", {}).get("response") or raw
        existing_offer_id = ""
        try:
            payload = json.loads(detail) if isinstance(detail, str) else detail
            for err in payload.get("errors", []) if isinstance(payload, dict) else []:
                for param in err.get("parameters", []) or []:
                    if str(param.get("name", "")).lower() == "offerid":
                        existing_offer_id = str(param.get("value") or "").strip()
                        break
                if existing_offer_id:
                    break
        except Exception:
            pass
        if existing_offer_id:
            result = {"success": True, "existing": True, "offer": {"offer_id": existing_offer_id},
                      "message": "Das eBay-Angebot existiert bereits; die vorhandene Offer-ID wurde übernommen."}
        else:
            raise RuntimeError(f"eBay Sandbox: Offer konnte nicht erstellt werden.\n\n{detail}")

    offer_id = str(result.get("offer", {}).get("offer_id") or "").strip()
    if not offer_id:
        raise RuntimeError("eBay hat keine Offer-ID zurückgegeben.")

    now = datetime.now().isoformat(timespec="seconds")
    c = db()
    try:
        c.execute("UPDATE ebay_listings SET ebay_offer_id=?, status=?, updated_at=? WHERE card_id=?",
                  (offer_id, "Offer erstellt", now, int(card_id)))
        c.commit()
    finally:
        c.close()
    return result


def ebay_sandbox_get_offer(offer_id):
    """Read an existing unpublished/published eBay Sandbox offer."""
    offer_id = str(offer_id or "").strip()
    if not offer_id:
        raise ValueError("Keine eBay Offer-ID vorhanden.")

    req = urllib.request.Request(
        EBAY_OAUTH_SERVER_URL + "/api/ebay/offer/" + urllib.parse.quote(offer_id, safe=""),
        method="GET",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Der eBay-OAuth-Server ist nicht erreichbar ({EBAY_OAUTH_SERVER_URL}).\n\n{exc}"
        ) from exc

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"success": False, "error": raw}
    if status != 200 or not result.get("success"):
        detail = result.get("error") or result.get("response") or raw
        raise RuntimeError(f"eBay Sandbox: Offer konnte nicht gelesen werden.\n\n{detail}")
    return result


def ebay_sandbox_publish_offer(offer_id):
    """Publish an existing eBay Sandbox offer and return the new listing ID."""
    offer_id = str(offer_id or "").strip()
    if not offer_id:
        raise ValueError("Keine eBay Offer-ID vorhanden.")

    req = urllib.request.Request(
        EBAY_OAUTH_SERVER_URL + "/api/ebay/offer/" + urllib.parse.quote(offer_id, safe="") + "/publish",
        data=b"",
        method="POST",
        headers={"Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=45) as response:
            raw = response.read().decode("utf-8", errors="replace")
            status = response.status
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        status = exc.code
    except urllib.error.URLError as exc:
        raise RuntimeError(
            f"Der eBay-OAuth-Server ist nicht erreichbar ({EBAY_OAUTH_SERVER_URL}).\n\n{exc}"
        ) from exc

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        result = {"success": False, "error": raw}

    if status != 200 or not result.get("success"):
        error_text = str(result.get("error") or "").strip()
        response = result.get("response")
        if isinstance(response, (dict, list)):
            response_text = json.dumps(response, ensure_ascii=False, indent=2)
        else:
            response_text = str(response or "").strip()
        if error_text and response_text:
            detail = error_text + "\n\neBay-Antwort:\n" + response_text
        else:
            detail = error_text or response_text or raw
        raise RuntimeError(f"eBay Sandbox: Offer konnte nicht veröffentlicht werden.\n\n{detail}")

    listing_id = str(
        result.get("listing_id")
        or result.get("listingId")
        or (result.get("publish") or {}).get("listing_id")
        or (result.get("publish") or {}).get("listingId")
        or ""
    ).strip()
    if not listing_id:
        raise RuntimeError("eBay hat beim Publish keine Listing-ID zurückgegeben.")

    return result, listing_id


def ebay_publish_check(card_id, title, description, condition, price,
                       listing_format, category_id, sku, offer_id=""):
    """Run a safe pre-publish validation without publishing anything."""
    checks = []
    def check(label, ok, detail):
        checks.append((bool(ok), label, detail))

    title = str(title or "").strip()
    description = str(description or "").strip()
    condition = str(condition or "").strip()
    category_id = str(category_id or "").strip()
    sku = str(sku or "").strip()
    offer_id = str(offer_id or "").strip()

    check("Titel", bool(title), "vorhanden" if title else "fehlt")
    check("Beschreibung", bool(description), "vorhanden" if description else "fehlt")
    try:
        numeric_price = float(str(price or "0").replace(",", "."))
    except Exception:
        numeric_price = 0
    check("Preis", numeric_price > 0, f"{numeric_price:.2f} €" if numeric_price > 0 else "ungültig")
    check("Zustand", bool(condition), "vorhanden" if condition else "fehlt")
    check("Angebotsformat", listing_format in ("Festpreis", "Auktion"), listing_format or "fehlt")
    check("eBay Kategorie-ID", category_id.isdigit(), category_id or "ungültig/fehlt")
    check("SKU / Lagerkennung", bool(sku), sku or "fehlt")

    c = db()
    try:
        row = c.execute(
            "SELECT front_image, back_image FROM cards WHERE card_id=?", (int(card_id),)
        ).fetchone()
    finally:
        c.close()
    front_ok = bool(row and image_path_from_ref(row[0]))
    back_ok = bool(row and image_path_from_ref(row[1]))
    check("Vorderseitenbild", front_ok, "vorhanden" if front_ok else "fehlt")
    check("Rückseitenbild", back_ok, "vorhanden" if back_ok else "fehlt")
    check("Offer-ID", bool(offer_id), offer_id if offer_id else "noch nicht erstellt")

    # The three Business Policies are intentionally not treated as local
    # failures because their availability depends on the eBay account and
    # can be temporarily unavailable in Sandbox.
    checks.append((None, "Business Policies", "werden beim eBay-Publish serverseitig geprüft"))
    return checks

def ebay_save_draft(card_id, title, description, condition, price,
                    listing_format, category, sku, status="Entwurf", template_key="football"):
    now=datetime.now().isoformat(timespec="seconds")
    c=db()
    try:
        c.execute(
            """INSERT INTO ebay_listings
               (card_id,title,description,condition,price,listing_format,
                category,sku,status,template_key,created_at,updated_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(card_id) DO UPDATE SET
                 title=excluded.title,
                 description=excluded.description,
                 condition=excluded.condition,
                 price=excluded.price,
                 listing_format=excluded.listing_format,
                 category=excluded.category,
                 sku=excluded.sku,
                 status=excluded.status,
                 template_key=excluded.template_key,
                 updated_at=excluded.updated_at""",
            (card_id,title,description,condition,price,listing_format,
             category,sku,status,template_key or "football",now,now)
        )
        c.commit()
    finally:
        c.close()




def ebay_export_from_template(parent):
    """Create an eBay Draft CSV (Action=Draft) for the selected/current drafts."""
    try:
        template_path = _ebay_default_template_path()
        target = filedialog.askdirectory(title="Zielordner für eBay-Importdatei auswählen", parent=parent)
        if not target:
            return
        c = db()
        drafts = c.execute(
            """SELECT e.card_id,e.title,e.description,e.condition,e.price,e.listing_format,
                      e.category,e.sku,e.template_key,c.front_image,c.back_image,c.category,
                      c.team,c.manufacturer,c.set_name,c.season_year,c.card_number,c.card_type,
                      c.variant,c.language,c.theme
               FROM ebay_listings e JOIN cards c ON c.card_id=e.card_id
               ORDER BY e.listing_id ASC"""
        ).fetchall()
        settings = c.execute("SELECT category_id FROM ebay_settings WHERE settings_id=1").fetchone()
        c.close()
        if not drafts:
            messagebox.showinfo("eBay-Importdatei", "Es gibt noch keine eBay-Entwürfe.", parent=parent)
            return

        rows, header_idx, headers = _ebay_template_rows_from_csv(template_path)
        hm = _ebay_template_header_map(headers)
        hidx = {h:i for i,h in enumerate(headers)}
        base = [""] * len(headers)
        out_rows = rows[:header_idx+1]
        written = 0
        for d in drafts:
            (card_id,title,desc,cond,price,fmt,ecat,sku,template_key,front_ref,back_ref,
             ccat,team,mfr,setname,season,cnum,ctype,variant,lang,theme) = d
            category = str(ecat or "").strip() if str(ecat or "").strip().isdigit() else str((settings or ["47140"])[0])
            row = list(base)
            def put(field, value):
                col = hm.get(field)
                if col is not None and value not in (None, ""):
                    row[hidx[col]] = value
            cfg = _ebay_template_catalog().get(template_key or "football") or _ebay_template_catalog()["football"]
            put("action", "Draft")
            put("sku", sku or f"DC-{card_id:06d}")
            put("category", category)
            put("title", str(title or "")[:80])
            put("startprice", price if price not in (None, "") else "")
            put("quantity", _ebay_inventory_quantity(card_id))
            cond_id = str(cond or "") if str(cond or "").isdigit() else str(ebay_get_settings()["condition_ungraded_id"])
            put("condition", cond_id)
            put("card_condition", _ebay_card_condition_value(cond))
            put("description", _ebay_standard_description(desc))
            put("format", _ebay_format_value(fmt or "FixedPrice"))
            put("sport", cfg.get("sport", ""))
            put("manufacturer", mfr)
            put("player", title)
            put("franchise", theme or setname or title)
            put("team", team)
            put("season", season)
            put("cardname", ctype or variant)
            put("cardnumber", cnum)
            put("producttype", "Trading Card")
            put("language", lang or "Deutsch")
            put("location", "Köln")
            out_rows.append(row)
            written += 1

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        out_dir = Path(target) / f"DCardLabs_eBay_Import_{stamp}"
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / "DCardLabs_eBay_Import.csv"
        with output_path.open("w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f, delimiter=";", lineterminator="\n").writerows(out_rows)
        messagebox.showinfo(
            "eBay-Importdatei",
            f"eBay-Importdatei erstellt.\n\nEntwürfe: {written}\n\n{output_path}",
            parent=parent
        )
    except Exception as exc:
        log_exception("eBay-Importdatei fehlgeschlagen", exc)
        messagebox.showerror("eBay-Importdatei", f"Die Importdatei konnte nicht erstellt werden:\n\n{exc}\n\nDetails: {LOG_FILE}", parent=parent)


def ebay_export_bundle(parent):
    """Export all eBay drafts and copy referenced card images."""
    try:
        target = filedialog.askdirectory(
            title="Zielordner für eBay-Export auswählen", parent=parent
        )
        if not target:
            return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        export_dir = Path(target) / f"DCardLabs_eBay_Export_{stamp}"
        images_dir = export_dir / "Bilder"
        images_dir.mkdir(parents=True, exist_ok=True)

        c = db()
        rows = c.execute(
            """SELECT e.listing_id, e.card_id, c.title, c.category, c.theme,
                      c.team, c.manufacturer, c.set_name, c.season_year,
                      c.card_number, c.card_type, c.variant, c.language,
                      e.title, e.description, e.condition, e.price,
                      e.listing_format, e.category, e.sku, e.status,
                      c.front_image, c.back_image
               FROM ebay_listings e JOIN cards c ON c.card_id=e.card_id
               ORDER BY e.listing_id ASC"""
        ).fetchall()
        c.close()

        if not rows:
            messagebox.showinfo("eBay-Export", "Es gibt noch keine eBay-Entwürfe.", parent=parent)
            try:
                images_dir.rmdir(); export_dir.rmdir()
            except OSError:
                pass
            return

        csv_path = export_dir / "ebay_entwuerfe.csv"
        fields = [
            "Listing-ID","Karten-ID","Karte","Kategorie-Karte","Thema/Franchise",
            "Team/Verein","Hersteller","Set/Serie","Saison/Jahr","Kartennummer",
            "Kartentyp","Variante","Sprache","eBay-Titel","Beschreibung","Zustand",
            "Preis","Angebotsformat","eBay-Kategorie","SKU","Status","Vorderseite","Rückseite"
        ]
        copied = 0
        with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=fields, delimiter=";")
            w.writeheader()
            for row in rows:
                (lid,cid,ctitle,ccat,theme,team,mfr,setname,season,cnum,ctype,variant,lang,
                 etitle,desc,cond,price,fmt,ecat,sku,status,front_ref,back_ref) = row
                image_out = {"front":"","back":""}
                for side, ref in (("front",front_ref),("back",back_ref)):
                    path = image_path_from_ref(ref)
                    if path and path.exists():
                        safe = re.sub(r"[^A-Za-z0-9_-]+", "_", str(sku or f"DC-{cid:06d}"))
                        out_name = f"{safe}_{side}{path.suffix.lower() or '.jpg'}"
                        shutil.copy2(path, images_dir/out_name)
                        image_out[side] = str(Path("Bilder")/out_name)
                        copied += 1
                    elif ref:
                        LOGGER.warning("eBay-Export: Bild fehlt für Karte %s (%s): %s", cid, side, ref)
                w.writerow({
                    "Listing-ID":lid,"Karten-ID":cid,"Karte":ctitle or "","Kategorie-Karte":ccat or "",
                    "Thema/Franchise":theme or "","Team/Verein":team or "","Hersteller":mfr or "",
                    "Set/Serie":setname or "","Saison/Jahr":season or "","Kartennummer":cnum or "",
                    "Kartentyp":ctype or "","Variante":variant or "","Sprache":lang or "",
                    "eBay-Titel":etitle or "","Beschreibung":desc or "","Zustand":cond or "",
                    "Preis":price if price is not None else "","Angebotsformat":fmt or "",
                    "eBay-Kategorie":ecat or "","SKU":sku or "","Status":status or "",
                    "Vorderseite":image_out["front"],"Rückseite":image_out["back"]
                })
        LOGGER.info("eBay-Export erstellt: %s | Entwürfe=%s | Bilder=%s", export_dir, len(rows), copied)
        messagebox.showinfo("eBay-Export", f"Export erfolgreich erstellt.\n\nEntwürfe: {len(rows)}\nBilder kopiert: {copied}\n\n{export_dir}", parent=parent)
    except Exception as exc:
        log_exception("eBay-Export fehlgeschlagen", exc)
        messagebox.showerror("eBay-Export", f"Der eBay-Export ist fehlgeschlagen.\n\n{exc}\n\nDetails: {LOG_FILE}", parent=parent)


def _ebay_template_header_map(headers):
    """Map German eBay draft/category templates to DCardLabs fields."""
    result = {}
    for h in headers:
        raw = str(h or "").strip()
        key = re.sub(r"[^a-z0-9äöüß]+", "", raw.lower())
        if key.startswith("action"): result["action"] = raw
        elif "customlabel" in key or key in {"sku", "lagerhaltungsnummer"}: result["sku"] = raw
        elif key in {"category", "categoryid"} or key.startswith("categoryid"): result["category"] = raw
        elif key in {"title", "titel"}: result["title"] = raw
        elif key == "conditionid" or key == "zustandsid": result["condition"] = raw
        elif "cardcondition" in key or "kartenzustand" in key or key.startswith("cdcardcondition") or key.startswith("cdkartenzustand"): result["card_condition"] = raw
        elif "sportart" in key: result["sport"] = raw
        elif key in {"franchise"} or key.startswith("cfranchise"): result["franchise"] = raw
        elif key in {"hersteller", "manufacturer"} or key.startswith("chersteller"): result["manufacturer"] = raw
        elif "spielersportler" in key: result["player"] = raw
        elif key in {"saison", "season"} or key.startswith("csaison"): result["season"] = raw
        elif key == "liga" or key.startswith("cliga"): result["league"] = raw
        elif key == "team" or key.startswith("cteam"): result["team"] = raw
        elif "kartenname" in key: result["cardname"] = raw
        elif "kartennummer" in key: result["cardnumber"] = raw
        elif "produktart" in key: result["producttype"] = raw
        elif key in {"sprache", "language"}: result["language"] = raw
        elif key in {"price", "preis", "startprice"}: result["startprice"] = raw
        elif "buynowprice" in key: result["buynowprice"] = raw
        elif key == "quantity" or key in {"menge", "stückzahl", "stueckzahl"}: result["quantity"] = raw
        elif key.startswith("picurl") or key in {"itemphotourl", "pictureurl", "imageurl"}: result["picurl"] = raw
        elif key in {"description", "beschreibung"}: result["description"] = raw
        elif key == "format": result["format"] = raw
        elif key in {"duration", "listingduration"} or "duration" in key: result["duration"] = raw
        elif key in {"location", "standort", "artikelstandort"}: result["location"] = raw
        elif "shippingprofilename" in key: result["shippingprofile"] = raw
        elif "returnprofilename" in key: result["returnprofile"] = raw
        elif "paymentprofilename" in key: result["paymentprofile"] = raw
        elif "shippingtype" in key: result["shippingtype"] = raw
        elif "shippingservice1option" in key: result["shipping1"] = raw
        elif "shippingservice1cost" in key: result["shipping1cost"] = raw
        elif "dispatchtimemax" in key: result["dispatch"] = raw
        elif key in {"scheduletime", "starttime", "startzeit"}: result["schedule_time"] = raw
        elif "returnsacceptedoption" in key: result["returnsaccepted"] = raw
        elif "immediatepayrequired" in key: result["immediatepay"] = raw
        elif "bestofferenabled" in key: result["bestoffer"] = raw
    return result


def _find_ebay_csv_header(rows):
    for idx, row in enumerate(rows):
        first = str(row[0] if row else "").strip()
        if first.lstrip('*').startswith("Action(") and any("CustomLabel" in str(v).replace(" ", "") or "Custom label" in str(v) for v in row):
            return idx, row
    raise ValueError("Keine eBay-Angebotsvorlage mit der erwarteten Kopfzeile gefunden.")


def _ebay_template_rows_from_csv(path):
    raw = Path(path).read_bytes()
    text = raw.decode("utf-8-sig", errors="replace")
    rows = list(csv.reader(text.splitlines(), delimiter=";"))
    header_idx, headers = _find_ebay_csv_header(rows)
    return rows, header_idx, headers


def _ebay_default_template_path():
    path = PROJECT_ROOT / "templates" / "ebay" / "eBay-draft-listing-template_DE.csv"
    if not path.exists():
        raise FileNotFoundError("Die eBay-Entwurfsvorlage fehlt: " + str(path))
    return path


def _ebay_template_catalog():
    """Available eBay offer templates. Non-sport can be supplied as a CSV later."""
    root = PROJECT_ROOT / "templates" / "ebay"
    return {
        "football": {
            "name": "Fußball-Sammelkarten",
            "path": root / "eBay-category-listing-template_261328.csv",
            "default_category": "261328",
            "sport": "Fußball",
        },
        "non_sport": {
            "name": "Non-Sport-Sammelkarten",
            "path": root / "eBay-category-listing-template_non_sport.csv",
            "default_category": "183050",
            "sport": "",
        },
    }


def _ebay_required_aspects(template_key="football"):
    """Return template-specific required item aspects from the eBay CSV."""
    path = _ebay_offer_template_path(template_key)
    if not path or not path.exists():
        return []
    try:
        rows = list(csv.reader(path.read_text(encoding="utf-8-sig").splitlines(), delimiter=";"))
        if len(rows) < 2:
            return []
        headers = rows[1]
        required = []
        for h in headers:
            raw = str(h or "").strip()
            if raw.startswith("*") and raw not in {headers[0]}:
                # Core fields are handled separately in the UI.
                key = re.sub(r"[^a-z0-9äöüß]+", "", raw.lstrip("*").lower())
                if key in {
                    "action", "category", "categoryid", "title", "conditionid",
                    "description", "format", "duration", "startprice", "quantity",
                    "location", "dispatchtimemax", "returnsacceptedoption",
                    "csportart"
                }:
                    continue
                required.append(raw)
        # eBay explicitly states the required aspects in an Info row.
        for row in rows[7:10]:
            text = " ".join(str(x or "") for x in row)
            m = re.search(r"required aspects are (.+)$", text, re.I)
            if m:
                for part in re.split(r",|;| and ", m.group(1)):
                    part = part.strip().rstrip(".")
                    if part and part not in required:
                        required.append(part)
        return required
    except Exception:
        return []


def _ebay_required_aspect_value(aspect, card, template_key="football"):
    """Resolve an eBay template aspect. Template-derived values are automatic."""
    a = re.sub(r"[^a-z0-9äöüß]+", "", str(aspect or "").lower())
    if a == "sportart":
        cfg = _ebay_template_catalog().get(template_key) or _ebay_template_catalog()["football"]
        return cfg.get("sport", "")
    mapping = {
        "franchise": "theme",
        "hersteller": "manufacturer",
        "edition": "set_name",
        "saison": "season_year",
        "liga": "",
        "team": "team",
        "spielersportler": "title",
        "charakter": "title",
        "abgebildetepersonkünstler": "title",
        "parallelvariante": "variant",
    }
    field = mapping.get(a)
    return card.get(field, "") if field else ""


def _ebay_draft_validation(card, title, condition, price, fmt, category, description, template_key="football"):
    """Validate only fields that must be complete before an active export."""
    errors = []
    checks = [
        ("Kategorie", category), ("Titel", title), ("Zustand", condition),
        ("Preis", price), ("Beschreibung", description), ("Format", fmt),
        ("Bilder", card.get("front_image") or card.get("back_image")),
    ]
    try:
        if float(str(price or "").replace(",", ".")) <= 0:
            checks[3] = ("Preis", "")
    except (TypeError, ValueError):
        checks[3] = ("Preis", "")
    for label, value in checks:
        if not str(value or "").strip(): errors.append(label)
    try:
        if _ebay_inventory_quantity(int(card.get("card_id", 0) or 0)) < 1: errors.append("Menge")
    except Exception:
        errors.append("Menge")
    # Only genuine template-specific required aspects are validated here.
    # Sportart is derived from the selected template, not from Karten-Kategorie.
    for aspect in _ebay_required_aspects(template_key):
        value = _ebay_required_aspect_value(aspect, card, template_key)
        if not str(value or "").strip(): errors.append(str(aspect).lstrip("*"))
    return list(dict.fromkeys(errors))


def _ebay_offer_template_path(template_key="football"):
    cfg = _ebay_template_catalog().get(template_key) or _ebay_template_catalog()["football"]
    path = cfg["path"]
    return path if path.exists() else None


def _ebay_format_value(value):
    """Normalize DCardLabs listing format to the eBay template value."""
    v = str(value or "").strip().lower()
    if v in {"fixedprice", "fixed price", "festpreis", "festpreisangebot", "fixed-price"}:
        return "FixedPrice"
    if v in {"auction", "auktion", "auctionstyle"}:
        return "Auction"
    # DCardLabs currently creates fixed-price listings by default.
    return "FixedPrice"


def _ebay_inventory_quantity(card_id):
    """Return the available inventory quantity for a card, defaulting to 1."""
    try:
        c = db()
        row = c.execute(
            "SELECT COALESCE(SUM(quantity), 0) FROM inventory WHERE card_id=?",
            (int(card_id),)
        ).fetchone()
        c.close()
        quantity = int(row[0] or 0) if row else 0
        return max(1, quantity)
    except Exception:
        return 1


def _ebay_card_condition_value(condition):
    value = str(condition or "").strip().upper()
    return {"NM":"400010", "NEAR MINT":"400010", "NEAR MINT OR BETTER":"400010", "EX":"400011", "EXCELLENT":"400011", "VG":"400012", "VERY GOOD":"400012", "G":"400013", "GOOD":"400013", "POOR":"400013"}.get(value, "400010" if value else "")


def _ebay_standard_description(card_desc=""):
    """Build a clean HTML description for the eBay listing template."""
    import html

    base = str(card_desc or "").strip()
    base_html = ""
    if base:
        paragraphs = [p.strip() for p in base.split("\n\n") if p.strip()]
        rendered = []
        for para in paragraphs:
            lines = [line.strip() for line in para.splitlines() if line.strip()]
            if not lines:
                continue
            rendered.append("<p>" + "<br>".join(html.escape(line) for line in lines) + "</p>")
        base_html = "\n".join(rendered)

    standard = """
<h3>Versand &amp; Kombiversand</h3>
<ul>
  <li><strong>Sicher verpackt:</strong> Jede Karte wird geschützt in einer weichen Hülle (Sleeve) und zusätzlich in einer festen Plastikhülle (Toploader) knicksicher versendet.</li>
  <li><strong>Versandrabatt:</strong> Kombiversand ist aktiv! Egal wie viele Karten du bei mir kaufst, du zahlst nur einmalig die Versandkosten für den ersten Artikel. Jede weitere Karte reist komplett kostenlos mit.</li>
  <li><em>Wichtig bei Großbestellungen:</em> Bitte vor der Zahlung die Gesamtrechnung abwarten, falls die Kartenanzahl das Gewicht für einen Standardbrief überschreitet.</li>
</ul>
<h3>Mehr Karten entdecken</h3>
<p><a href="https://ebay.de/sch/dennis281086/m.html">Hier klicken, um meine anderen Sammelkarten anzusehen und Versandkosten zu sparen!</a></p>
<p><em>Rechtlicher Hinweis: Dies ist ein Privatverkauf. Der Verkauf erfolgt unter Ausschluss jeglicher Gewährleistung, Sachmängelhaftung oder Rücknahme.</em></p>
""".strip()
    return (base_html + "\n" + standard).strip() if base_html else standard


def _ebay_schedule_time(hours=2):
    """Return an eBay ScheduleTime value in UTC, comfortably in the future."""
    from datetime import timedelta, timezone
    now_utc = datetime.now(timezone.utc)
    return (now_utc + timedelta(hours=hours)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def ebay_export_offer_from_template(parent, template_key=None, schedule_hours=None):
    """Generate, but never upload, an eBay active-listing CSV (Action=Add)."""
    try:
        catalog = _ebay_template_catalog()
        if template_key is None:
            dlg = tk.Toplevel(parent); dlg.title("eBay-Angebot vorbereiten"); dlg.transient(parent); dlg.grab_set()
            fit_dialog(dlg, 560, 300, 520, 260)
            ttk.Label(dlg, text="Angebotsvorlage", font=("Segoe UI", 10, "bold")).pack(anchor="w", padx=16, pady=(16, 6))
            template_var = tk.StringVar(value="football")
            names = [(k, v["name"]) for k,v in catalog.items()]
            combo = ttk.Combobox(dlg, textvariable=template_var, values=[n for _,n in names], state="readonly", width=42)
            combo.current(0); combo.pack(fill="x", padx=16)
            reverse = {n:k for k,n in names}
            ttk.Label(dlg, text="Startzeit (Stunden ab jetzt)").pack(anchor="w", padx=16, pady=(14, 6))
            hours_var=tk.StringVar(value="2")
            ttk.Spinbox(dlg, from_=1, to=720, textvariable=hours_var, width=8).pack(anchor="w", padx=16)
            result={}
            def accept():
                result["key"]=reverse.get(template_var.get(), "football")
                try: result["hours"]=max(1, int(hours_var.get()))
                except: result["hours"]=2
                dlg.destroy()
            ttk.Frame(dlg).pack(fill="both", expand=True)
            bf=ttk.Frame(dlg); bf.pack(fill="x", padx=16, pady=12)
            ttk.Button(bf,text="Abbrechen",command=dlg.destroy).pack(side="right")
            ttk.Button(bf,text="Weiter",command=accept).pack(side="right",padx=(0,8))
            parent.wait_window(dlg)
            if not result: return
            template_key=result["key"]; schedule_hours=result["hours"]
        if schedule_hours is None:
            schedule_hours=2
        cfg=catalog.get(template_key) or catalog["football"]
        template_path = _ebay_offer_template_path(template_key)
        if template_path is None:
            selected = filedialog.askopenfilename(title=f"eBay-Angebotsvorlage für {cfg['name']} auswählen", parent=parent, filetypes=[("CSV-Dateien", "*.csv"), ("Alle Dateien", "*.*")])
            if not selected: return
            template_path = Path(selected)
        if not messagebox.askyesno("eBay-Angebot erstellen", f"Vorlage: {cfg['name']}\nStartzeit: +{schedule_hours} Stunden\n\nDie Datei verwendet Action=Add. eBay legt die Angebote damit für die Zukunft an; du kannst sie vor dem Start prüfen.\n\nDatei jetzt erzeugen?", parent=parent): return
        target = filedialog.askdirectory(title="Zielordner für eBay-Angebotsdatei auswählen", parent=parent)
        if not target: return
        c=db()
        drafts=c.execute("""SELECT e.card_id,e.title,e.description,e.condition,e.price,e.listing_format,e.category,e.sku,e.status,c.front_image,c.back_image,c.category,c.team,c.manufacturer,c.set_name,c.season_year,c.card_number,c.card_type,c.variant,c.language,c.theme,(SELECT i.condition FROM inventory i WHERE i.card_id=e.card_id ORDER BY i.inventory_id ASC LIMIT 1) AS inv_condition FROM ebay_listings e JOIN cards c ON c.card_id=e.card_id ORDER BY e.listing_id ASC""").fetchall()
        settings=c.execute("SELECT category_id FROM ebay_settings WHERE settings_id=1").fetchone(); c.close()
        default_category=str(settings[0] or cfg["default_category"]) if settings else cfg["default_category"]
        if not drafts: messagebox.showinfo("eBay-Angebot", "Es gibt noch keine eBay-Entwürfe.", parent=parent); return
        rows, header_idx, headers=_ebay_template_rows_from_csv(template_path); hm=_ebay_template_header_map(headers); hidx={h:i for i,h in enumerate(headers)}
        required=["action","category","title","condition","description","format","duration","startprice","quantity","location","picurl","schedule_time"]
        missing=[x for x in required if x not in hm]
        if missing: raise ValueError("Die Angebotsvorlage enthält nicht die benötigten Spalten: "+", ".join(missing))
        base=[""]*len(headers); out_rows=rows[:header_idx+1]; blocked=[]; written=0; image_count=0
        for d in drafts:
            (card_id,title,desc,cond,price,fmt,ecat,sku,status,front_ref,back_ref,ccat,team,mfr,setname,season,cnum,ctype,variant,lang,theme,inv_condition)=d
            errors=[]; category=str(ecat or "").strip() if str(ecat or "").strip().isdigit() else default_category
            if not category.isdigit(): errors.append("Category ID fehlt")
            front=image_path_from_ref(front_ref); back=image_path_from_ref(back_ref)
            card_data = {
                "card_id": card_id, "category": ccat, "theme": theme, "team": team,
                "manufacturer": mfr, "set_name": setname, "title": title,
                "season_year": season, "card_number": cnum, "card_type": ctype,
                "variant": variant, "language": lang, "front_image": front_ref, "back_image": back_ref,
            }
            validation = _ebay_draft_validation(
                card_data, title, cond, price, fmt, category, desc, template_key
            )
            errors.extend(validation)
            # Keep export diagnostics concise and avoid duplicate category errors.
            errors = list(dict.fromkeys(errors))
            if errors: blocked.append(f"Karte #{card_id}: "+", ".join(errors)); continue
            try:
                from google_drive_sync import upload_card_images
                image_result=upload_card_images(BASE,card_id,front_path=front,back_path=back); urls=image_result.get("urls",[])
                if not urls: raise ValueError("keine Bild-URL erzeugt")
                image_count+=len(urls)
            except Exception as exc:
                blocked.append(f"Karte #{card_id}: Bildbereitstellung fehlgeschlagen – {exc}"); LOGGER.exception("eBay-Bildbereitstellung fehlgeschlagen für Karte #%s",card_id); continue
            row=list(base)
            def put(field,value):
                col=hm.get(field)
                if col is not None and value not in (None,""): row[hidx[col]]=value
            put("action","Add"); put("sku",sku or f"DC-{card_id:06d}"); put("category",category); put("title",str(title or "")[:80]); put("condition","4000" if not str(cond or "").strip().isdigit() else str(cond).strip()); put("card_condition",_ebay_card_condition_value(inv_condition or "NM")); put("sport",cfg["sport"]); put("manufacturer",mfr); put("player",title); put("team",team); put("season",season); put("league","Bundesliga" if "bundesliga" in (str(setname)+str(theme)).lower() else ""); put("franchise",theme or setname or title); put("cardname",ctype or variant); put("cardnumber",cnum); put("producttype","Trading Card"); put("language",lang or "Deutsch"); put("picurl","|".join(urls)); put("description",_ebay_standard_description(desc)); put("format",_ebay_format_value(fmt or "FixedPrice")); put("schedule_time",_ebay_schedule_time(schedule_hours)); put("duration","GTC"); put("startprice",price); put("quantity",_ebay_inventory_quantity(card_id)); put("location","Köln"); put("shippingprofile","Pauschal: DE_DeutschePostBrief EUR 0,95, 3 Unt (276219276019)"); put("returnprofile","No Return Accepted (276219275019)"); put("paymentprofile","eBay Managed Payments (276219277019)"); put("returnsaccepted","ReturnsNotAccepted"); put("dispatch","3"); put("bestoffer","false")
            out_rows.append(row); written+=1
            exported_now=datetime.now().isoformat(timespec="seconds")
            scheduled_now=_ebay_schedule_time(schedule_hours)
            c2=db()
            c2.execute("UPDATE ebay_listings SET status=?, template_key=?, exported_at=?, scheduled_at=?, updated_at=? WHERE card_id=?", ("Geplant", template_key, exported_now, scheduled_now, exported_now, card_id))
            c2.commit(); c2.close()
            LOGGER.info("eBay-Angebot vorbereitet: Karte=%s Vorlage=%s Bilder=%s Start=%s",card_id,template_key,len(urls),scheduled_now)
        if written==0: raise ValueError("Keine Karte erfüllt die Sicherheitsprüfung.\n\n"+"\n".join(blocked[:10]))
        stamp=datetime.now().strftime("%Y%m%d_%H%M%S"); out_dir=Path(target)/f"DCardLabs_eBay_Angebote_{stamp}"; out_dir.mkdir(parents=True,exist_ok=True); output_path=out_dir/"DCardLabs_eBay_Angebote.csv"
        with output_path.open("w",newline="",encoding="utf-8-sig") as f: csv.writer(f,delimiter=";",lineterminator="\n").writerows(out_rows)
        msg=f"Angebotsdatei erstellt.\n\nSicherheitsprüfung: {written} Angebot(e) bereit.\nStartzeit: +{schedule_hours} Stunden nach Export\nBilder bereitgestellt: {image_count}\nBlockiert: {len(blocked)}\n\n{output_path}"
        if blocked: msg+="\n\nNicht exportiert:\n"+"\n".join(blocked[:8])
        messagebox.showinfo("eBay-Angebot",msg,parent=parent)
    except Exception as exc:
        log_exception("eBay-Angebotsdatei fehlgeschlagen",exc); messagebox.showerror("eBay-Angebot",f"Die Angebotsdatei konnte nicht erstellt werden:\n\n{exc}\n\nDetails: {LOG_FILE}",parent=parent)


def google_drive_setup(parent):
    try:
        from google_drive_sync import setup
        setup(BASE)
        LOGGER.info("Google Drive erfolgreich autorisiert und Ordnerstruktur geprüft.")
        messagebox.showinfo(
            "Google Drive",
            "Google Drive wurde erfolgreich eingerichtet.\n\n"
            "DCardLabs/Backups, DCardLabs/Cards und DCardLabs/eBay stehen bereit.\n\n"
            "Automatische Backups werden ab dem nächsten Programmstart/-ende hochgeladen.",
            parent=parent
        )
        return True
    except Exception as exc:
        log_exception("Google-Drive-Einrichtung fehlgeschlagen", exc)
        messagebox.showerror("Google Drive", f"Einrichtung fehlgeschlagen:\n\n{exc}\n\nDetails: {LOG_FILE}", parent=parent)
        return False


def google_drive_backup_now(parent=None, interactive=False, reason="manual"):
    """Create a full project ZIP and upload it to Google Drive/Backups."""
    try:
        from google_drive_sync import backup_project_to_drive, setup
        if interactive:
            setup(BASE)
        else:
            from google_drive_sync import _get_service
            if _get_service(BASE, interactive=False) is None:
                LOGGER.info("Google-Drive-Backup übersprungen (%s): noch nicht autorisiert.", reason)
                return None
        local_path, result = backup_project_to_drive(BASE, create_project_backup)
        LOGGER.info(
            "Google-Drive-Backup erfolgreich: Grund=%s lokal=%s drive_id=%s",
            reason, local_path, (result or {}).get("id") if result else None
        )
        return result
    except Exception as exc:
        log_exception(f"Google-Drive-Backup fehlgeschlagen ({reason})", exc)
        if parent is not None:
            messagebox.showerror(
                "Google-Drive-Backup",
                f"Das Google-Drive-Backup konnte nicht erstellt werden.\n\n{exc}\n\nDetails: {LOG_FILE}",
                parent=parent
            )
        return None


def google_drive_startup_backup(parent):
    """Non-blocking startup backup. If OAuth is not set up yet, it only logs."""
    google_drive_backup_now(parent=parent, interactive=False, reason="Programmstart")

def main():
    ensure_app_dirs()
    db().close()
    migrate_image_references()
    if not SANDBOX_MODE:
        maybe_auto_backup()
    root = tk.Tk()

    def tk_callback_exception(exc_type, exc_value, exc_traceback):
        LOGGER.error(
            "Tkinter-Callback-Fehler",
            exc_info=(exc_type, exc_value, exc_traceback)
        )
        try:
            messagebox.showerror(
                "DCardLabs – Fehler",
                f"Ein UI-Fehler ist aufgetreten.\n\n"
                f"Details stehen in:\n{LOG_FILE}\n\n{exc_value}",
                parent=root
            )
        except Exception:
            pass

    root.report_callback_exception = tk_callback_exception
    root.title(APP)
    maximize_window(root)
    root.geometry("1320x800")
    if SANDBOX_MODE:
        root.title(APP + "  [NUR TESTDATEN]")

    # Google-Drive-Backup beim Start. Ohne vorherige OAuth-Freigabe wird
    # still protokolliert und der normale Programmstart nicht blockiert.
    if not SANDBOX_MODE:
        root.after(1000, lambda: google_drive_startup_backup(root))

    nb = ttk.Notebook(root)
    nb.pack(fill="both", expand=True, padx=12, pady=12)



    scan_tab = ttk.Frame(nb, padding=16)
    cards_tab = ttk.Frame(nb, padding=16)
    inv_tab = ttk.Frame(nb, padding=16)
    buy_tab = ttk.Frame(nb, padding=16)
    sales_tab = ttk.Frame(nb, padding=16)
    ebay_tab = ttk.Frame(nb, padding=16)

    nb.add(scan_tab, text="📷 Scan + Pair + OCR")
    nb.add(cards_tab, text="Karten")
    nb.add(inv_tab, text="Inventar")
    nb.add(buy_tab, text="Käufe")
    nb.add(sales_tab, text="Verkäufe")
    nb.add(ebay_tab, text="eBay")

    front = tk.StringVar()
    back = tk.StringVar()
    output = tk.StringVar()
    status = tk.StringVar(value="Bereit.")
    do_ocr = tk.BooleanVar(value=True)
    do_back_ocr = tk.BooleanVar(value=True)
    rotate = tk.BooleanVar(value=True)
    quality = tk.IntVar(value=97)

    def choose_file(var):
        p = filedialog.askopenfilename(
            title="Scan auswählen",
            filetypes=[
                ("Bilder", "*.png *.jpg *.jpeg *.tif *.tiff *.bmp"),
                ("Alle Dateien", "*.*")
            ]
        )
        if p:
            var.set(p)

    def choose_dir():
        p = filedialog.askdirectory(title="Ausgabeordner auswählen")
        if p:
            output.set(p)

    def row(parent, label, var, command):
        r = ttk.Frame(parent)
        r.pack(fill="x", pady=5)
        ttk.Label(r, text=label, width=22).pack(side="left")
        ttk.Entry(r, textvariable=var).pack(
            side="left", fill="x", expand=True
        )
        ttk.Button(r, text="Auswählen…", command=command).pack(
            side="left", padx=8
        )

    box = ttk.LabelFrame(scan_tab, text="Scans", padding=14)
    box.pack(fill="x")
    row(box, "Vorderseiten-Scan:", front, lambda: choose_file(front))
    row(box, "Rückseiten-Scan:", back, lambda: choose_file(back))
    row(box, "Ausgabeordner:", output, choose_dir)

    ttk.Label(
        box,
        text=(
            "Beide Scans müssen dasselbe 3×3-Layout haben. "
            "Die Karten werden automatisch 001↔001 bis 009↔009 gepaart."
        )
    ).pack(anchor="w", pady=8)

    opt = ttk.LabelFrame(scan_tab, text="Optionen", padding=12)
    opt.pack(fill="x", pady=12)
    ttk.Checkbutton(
        opt, text="OCR für Kartennamen", variable=do_ocr
    ).grid(row=0, column=0, sticky="w", padx=8)
    ttk.Checkbutton(
        opt, text="OCR Rückseite", variable=do_back_ocr
    ).grid(row=0, column=1, sticky="w", padx=8)
    ttk.Checkbutton(
        opt, text="Karten um 180° drehen", variable=rotate
    ).grid(row=0, column=2, sticky="w", padx=20)

    ocr_info = tk.StringVar(value="OCR wird geprüft …")
    ttk.Label(opt, textvariable=ocr_info).grid(
        row=1, column=0, columnspan=4, sticky="w", padx=8, pady=(8, 0)
    )
    ttk.Label(opt, text="JPG-Qualität:").grid(
        row=0, column=2, sticky="w", padx=8
    )
    ttk.Spinbox(
        opt, from_=90, to=100, textvariable=quality, width=6
    ).grid(row=0, column=3, sticky="w")

    def refresh():
        c = db()
        card_tree.delete(*card_tree.get_children())
        for r in c.execute(
            """
            SELECT c.card_id, c.title,
                   COALESCE(e.ebay_offer_id, '') AS ebay_offer_id,
                   COALESCE(e.status, '') AS ebay_status,
                   c.category, c.theme, c.team, c.manufacturer, c.set_name,
                   c.season_year, c.card_number, c.card_type, c.variant,
                   c.is_numbered, c.serial_number, c.print_run, c.language,
                   c.ocr_status, c.ocr_confidence, c.ocr_team, c.ocr_league,
                   c.ocr_set, c.ocr_card_type, c.ocr_card_number,
                   c.ocr_serial_number, c.ocr_print_run, c.ocr_variant
             FROM cards c
             LEFT JOIN ebay_listings e ON e.card_id = c.card_id
             ORDER BY c.card_id ASC
            """
        ):
            card_tree.insert("", "end", values=r)

        inv_tree.delete(*inv_tree.get_children())
        for r in c.execute(
            """
            SELECT i.inventory_id, i.card_id, c.title, i.quantity,
                   i.condition, i.location, i.notes
            FROM inventory i JOIN cards c ON c.card_id=i.card_id
            ORDER BY i.inventory_id ASC
            """
        ):
            inv_tree.insert("", "end", values=r)

        buy_tree.delete(*buy_tree.get_children())
        for r in c.execute(
            """
            SELECT purchase_id, purchase_date, platform, seller,
                   card_count, purchase_price, shipping, total_price
            FROM purchases ORDER BY purchase_id ASC
            """
        ):
            buy_tree.insert("", "end", values=r)

        sales_tree.delete(*sales_tree.get_children())
        for r in c.execute(
            """
            SELECT s.sale_id, s.sale_date, s.card_id,
                   COALESCE(c.title, ''), s.ebay_item_id, s.ebay_order_id,
                   s.quantity, s.gross_price, s.shipping_charged,
                   s.ebay_fees, s.net_amount, s.status
            FROM ebay_sales s
            LEFT JOIN cards c ON c.card_id=s.card_id
            ORDER BY s.sale_id ASC
            """
        ):
            sales_tree.insert("", "end", values=r)

        ebay_tree.delete(*ebay_tree.get_children())
        for r in c.execute(
            """
            SELECT e.listing_id, e.card_id, c.title, e.title, e.description,
                   e.condition, e.price, e.listing_format, e.status,
                   COALESCE(e.ebay_offer_id, '') AS ebay_offer_id
            FROM ebay_listings e
            JOIN cards c ON c.card_id=e.card_id
            ORDER BY e.listing_id ASC
            """
        ):
            ebay_tree.insert("", "end", values=r)
        c.close()

    def sync_google_sheets():
        try:
            from google_sheets_sync import (
                sync_sqlite_to_sheets,
                load_config,
                ensure_google_packages
            )

            missing = ensure_google_packages()
            if missing:
                messagebox.showwarning(
                    APP,
                    "Für Google Sheets fehlen noch Python-Pakete:\n\n"
                    + "\n".join(missing)
                    + "\n\n"
                    "Bitte einmal „Google Sheets einrichten“ ausführen "
                    "oder setup_google_sheets.bat starten.",
                    parent=root
                )
                return

            cfg = load_config(BASE)
            spreadsheet_id = cfg.get("spreadsheet_id")

            if not spreadsheet_id:
                spreadsheet_id = simpledialog.askstring(
                    "Google Sheets synchronisieren",
                    "Google-Sheets-ID eingeben:\n\n"
                    "Die ID steht in der URL der Google-Tabelle.",
                    parent=root
                )
                if not spreadsheet_id:
                    return

            status.set("Google Sheets wird synchronisiert …")
            root.update_idletasks()

            sync_sqlite_to_sheets(
                BASE,
                DB,
                spreadsheet_id.strip()
            )

            status.set("Google Sheets erfolgreich synchronisiert.")
            messagebox.showinfo(
                APP,
                "Google Sheets wurde erfolgreich aktualisiert.\n\n"
                "Quelle: SQLite\n"
                "Richtung: SQLite → Google Sheets",
                parent=root
            )

        except Exception as exc:
            status.set("Google-Sheets-Synchronisation fehlgeschlagen.")
            messagebox.showerror(
                APP,
                "Google-Sheets-Synchronisation fehlgeschlagen:\n\n"
                + str(exc),
                parent=root
            )


    def do_project_backup():
        try:
            path=create_project_backup()
            messagebox.showinfo("Backup erstellt",
                                f"Projekt-Backup erfolgreich erstellt:\n\n{path}",
                                parent=root)
        except Exception as exc:
            messagebox.showerror("Backup-Fehler",str(exc),parent=root)

    def do_restore():
        path=filedialog.askopenfilename(
            parent=root,title="DCardLabs-Backup auswählen",initialdir=str(BACKUP_ROOT),
            filetypes=[("DCardLabs Backups","*.zip *.db"),("Alle Dateien","*.*")]
        )
        if not path:return
        if not messagebox.askyesno(
            "Backup wiederherstellen",
            "Das Backup wird wiederhergestellt. Vorher wird automatisch ein "
            "Sicherheits-Backup des aktuellen Standes erstellt.\n\nFortfahren?",
            parent=root): return
        try:
            emergency=restore_backup(path)
            refresh()
            messagebox.showinfo("Wiederherstellung erfolgreich",
                                f"Backup wurde wiederhergestellt.\n\n"
                                f"Notfall-Backup:\n{emergency}",parent=root)
        except Exception as exc:
            messagebox.showerror("Wiederherstellungsfehler",str(exc),parent=root)

    def do_image_check():
        try:
            missing,changed=check_image_references()
            if not missing and not changed:
                messagebox.showinfo("Bilder prüfen",
                    "Alle referenzierten Bilder sind vorhanden und unverändert.",
                    parent=root)
                return
            lines=[f"Fehlend: {len(missing)}",f"Verändert: {len(changed)}",""]
            lines += [f"Karte #{a} – {b} – {c}" for a,b,c,d in (missing+changed)[:25]]
            messagebox.showwarning("Bilder prüfen","\n".join(lines),parent=root)
        except Exception as exc:
            messagebox.showerror("Bildprüfung fehlgeschlagen",str(exc),parent=root)

    def start_scan():
        if not front.get() or not back.get():
            messagebox.showwarning(
                APP, "Bitte Vorder- und Rückseitenscan auswählen."
            )
            return

        engine = BASE / "scanner" / "scanner_v0_8_dynamic.py"
        current_hash = hashlib.sha256(
            engine.read_bytes()
        ).hexdigest() if engine.exists() else ""
        if not engine.exists():
            LOGGER.error("Dynamic-Grid-Engine fehlt: %s", engine)
            messagebox.showerror(
                APP,
                "Die Dynamic-Grid-Engine fehlt im Programmordner.\n\n"
                f"Erwartet: {engine}\n\n"
                f"Details: {LOG_FILE}"
            )
            return
        if current_hash != SCANNER_HASH:
            LOGGER.warning(
                "Dynamic-Grid-Engine Hash-Abweichung: erwartet=%s aktuell=%s",
                SCANNER_HASH, current_hash
            )

        base = Path(output.get()) if output.get() else BASE / "Scan_Projekte"
        project = base / f"Scan_{datetime.now():%Y%m%d_%H%M%S}"
        front_dir = project / "Vorderseite"
        back_dir = project / "Rueckseite"
        pair_dir = project / "Paare"
        for d in (front_dir, back_dir, pair_dir):
            d.mkdir(parents=True, exist_ok=True)

        c = db()
        cur = c.execute(
            """
            INSERT INTO scan_batches(
                created_at, front_scan, back_scan, status
            ) VALUES(?,?,?,?)
            """,
            (
                datetime.now().isoformat(timespec="seconds"),
                str(Path(front.get()).resolve()),
                str(Path(back.get()).resolve()),
                "läuft"
            )
        )
        batch_id = cur.lastrowid
        c.commit()
        c.close()

        try:
            status.set("1/4 Vorderseite – Dynamic Grid …")
            root.update_idletasks()
            front_files = scan_one(
                front.get(), front_dir, int(quality.get()), rotate.get()
            )

            status.set("2/4 Rückseite – Dynamic Grid …")
            root.update_idletasks()
            back_files = scan_one(
                back.get(), back_dir, int(quality.get()), rotate.get()
            )

            status.set("3/4 Paarung + verbesserte OCR …")
            root.update_idletasks()
            pairs = pair_and_ocr(
                front_files, back_files, pair_dir, do_ocr.get(), do_back_ocr.get()
            )
            write_pair_exports(pairs, pair_dir)

            status.set("4/4 Datenbank aktualisieren …")
            root.update_idletasks()
            insert_cards(pairs, batch_id)

            refresh()
            status.set("Fertig – 9 Kartenpaare verarbeitet.")
            messagebox.showinfo(
                APP,
                "Scan abgeschlossen.\n\n"
                "✓ 9 Vorderseiten\n"
                "✓ 9 Rückseiten\n"
                "✓ 9 Kartenpaare\n"
                f"✓ OCR: {'aktiv' if do_ocr.get() else 'deaktiviert'}\n"
                "✓ Datenbank aktualisiert\n\n"
                f"Projektordner:\n{project}"
            )
        except Exception as e:
            c = db()
            c.execute(
                "UPDATE scan_batches SET status=? WHERE batch_id=?",
                (f"Fehler: {e}", batch_id)
            )
            c.commit()
            c.close()
            status.set("Fehler – Karten-Datenbank unverändert.")
            messagebox.showerror(APP, f"{type(e).__name__}:\n{e}")

    def test_ocr():
        ok, msg = ocr_setup_status()
        ocr_info.set(msg)
        if ok:
            messagebox.showinfo(APP, msg)
        else:
            messagebox.showerror(APP, msg)

    ttk.Button(
        scan_tab,
        text="OCR-Verbindung testen",
        command=test_ocr
    ).pack(anchor="w", pady=(4, 6))

    ttk.Button(
        scan_tab,
        text="▶  VORDERSEITE + RÜCKSEITE SCANNEN / PAAREN / OCR",
        command=start_scan
    ).pack(fill="x", pady=8, ipady=7)
    ttk.Label(scan_tab, textvariable=status).pack(anchor="w", pady=4)

    def make_tree(parent, cols, widths):
        frame = ttk.Frame(parent)
        frame.pack(fill="both", expand=True)
        t = ttk.Treeview(frame, columns=cols, show="headings")
        for col, width in zip(cols, widths):
            t.heading(col, text=col)
            t.column(col, width=width, minwidth=50)
        y = ttk.Scrollbar(frame, orient="vertical", command=t.yview)
        x = ttk.Scrollbar(frame, orient="horizontal", command=t.xview)
        t.configure(yscrollcommand=y.set, xscrollcommand=x.set)
        t.grid(row=0, column=0, sticky="nsew")
        y.grid(row=0, column=1, sticky="ns")
        x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        return t

    ttk.Label(
        cards_tab,
        text=(
            "Doppelklick auf eine Karte öffnet die Stammdaten. "
            "OCR-Status und Konfidenz werden separat angezeigt."
        )
    ).pack(anchor="w", pady=(0, 8))

    def show_card_field_help():
        messagebox.showinfo(
            "Karten – Datenfelder",
            "Thema / Franchise\n"
            "• Übergeordnete Marke, Serie, Welt oder Franchise der Karte.\n"
            "• Beispiele: Pokémon, Marvel, Star Wars, Bundesliga.\n"
            "• Für eBay wird dieses Feld typischerweise als Franchise verwendet.\n\n"
            "Team / Verein\n"
            "• Konkretes Team, Verein oder Club auf der Karte.\n"
            "• Beispiele: 1. FC Köln, FC Bayern München, Real Madrid.\n"
            "• Für eBay wird es als Team verwendet.\n\n"
            "Für eBay besonders relevant\n"
            "• Name / Titel → eBay-Titel bzw. Spieler/Sportler\n"
            "• Hersteller → Hersteller\n"
            "• Thema / Franchise → Franchise\n"
            "• Team / Verein → Team\n"
            "• Set / Serie → Edition\n"
            "• Saison → Saison\n"
            "• Kartennummer → Kartennummer\n"
            "• Variante → Parallel/Variante\n"
            "• Sprache → Sprache\n\n"
            "Sportart wird NICHT aus Kategorie erraten. Sie kommt bei der eBay-Angebotsvorlage aus dem gewählten Template (z. B. Fußball)."
        )
    ttk.Button(cards_tab, text="ℹ Erklärung zu Datenfeldern / eBay", command=show_card_field_help).pack(anchor="e", pady=(0,8))

    ttk.Button(
        cards_tab,
        text="＋ Karte manuell hinzufügen",
        command=lambda: open_manual_card_dialog(root)
    ).pack(anchor="e", pady=(0, 8))

    ttk.Button(
        cards_tab,
        text="📥 Aus Handy-Ordner importieren",
        command=lambda: open_handy_import_dialog(root)
    ).pack(anchor="e", pady=(0, 8))

    card_cols = [
        "ID", "Name / Titel", "eBay Offer-ID", "eBay Status", "Kategorie",
        "Thema / Franchise", "Team", "Hersteller", "Set / Serie",
        "Saison", "Kartennr.", "Typ", "Variante", "Numbered",
        "Seriennr.", "Print Run", "Sprache", "OCR", "Konfidenz",
        "OCR Team", "OCR Liga", "OCR Set", "OCR Typ",
        "OCR Nr.", "OCR Serial", "OCR Print Run", "OCR Variante"
    ]
    ttk.Button(
        cards_tab,
        text="🗑 Karte löschen",
        command=lambda: (
            confirm_and_delete(
                root, "card",
                int(card_tree.item(card_tree.selection()[0], "values")[0]),
                on_deleted=refresh
            ) if card_tree.selection() else None
        )
    ).pack(anchor="e", pady=(0, 8))

    card_tree = make_tree(
        cards_tab, card_cols,
        [50,220,155,130,100,150,150,120,150,80,90,90,120,90,
         90,90,80,90,80,130,110,110,100,90,110,110,110]
    )


    def _load_pil_preview(path, max_size):
        """Load a fully detached PIL image so Windows keeps no file handle open."""
        with Image.open(path) as source:
            img = source.convert("RGB")
            img.thumbnail(max_size, Image.LANCZOS)
            return img.copy()

    def show_image_preview(parent, ref, title="Kartenbild"):
        path = image_path_from_ref(ref)
        if not path:
            messagebox.showinfo(
                "Bildvorschau",
                "Für diese Seite ist kein vorhandenes Bild hinterlegt.",
                parent=parent
            )
            return

        if Image is None or ImageTk is None:
            messagebox.showerror(
                "Bildvorschau",
                "Für die Bildvorschau fehlt Pillow.\n"
                "Bitte 'pip install Pillow' ausführen.",
                parent=parent
            )
            return

        try:
            img = _load_pil_preview(path, (520, 700))
            preview = tk.Toplevel(parent)
            preview.title(title)
            preview.geometry("580x780")
            preview.transient(parent)

            frame = ttk.Frame(preview, padding=12)
            frame.pack(fill="both", expand=True)

            photo = ImageTk.PhotoImage(img)
            label = ttk.Label(frame, image=photo)
            label.image = photo
            label.pack(expand=True)

            ttk.Label(
                frame, text=str(path), wraplength=540
            ).pack(fill="x", pady=(8, 0))

            ttk.Button(
                frame, text="Schließen", command=preview.destroy
            ).pack(pady=(10, 0))

        except Exception as exc:
            messagebox.showerror(
                "Bildvorschau fehlgeschlagen",
                str(exc),
                parent=parent
            )

    def open_image_library(parent, variable, title):
        """DCardLabs image browser with preview and selection."""
        if Image is None or ImageTk is None:
            messagebox.showerror(
                "Bildbibliothek",
                "Für die Bildbibliothek fehlt Pillow.\n"
                "Bitte 'pip install Pillow' ausführen.",
                parent=parent
            )
            return

        files = sorted(
            [p for p in IMAGE_ROOT.rglob("*")
             if p.is_file() and p.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}],
            key=lambda p: p.name.lower()
        )
        if not files:
            messagebox.showinfo(
                "Bildbibliothek",
                "In der DCardLabs-Bildbibliothek sind noch keine Bilder vorhanden.",
                parent=parent
            )
            return

        win = tk.Toplevel(parent)
        win.title(title)
        win.geometry("820x620")
        maximize_window(win)
        win.transient(parent)

        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(0, weight=1)

        left = ttk.Frame(outer)
        left.grid(row=0, column=0, sticky="nsw", padx=(0, 12))
        ttk.Label(left, text="Gespeicherte Bilder").pack(anchor="w")

        listbox = tk.Listbox(left, width=34, height=28)
        listbox.pack(side="left", fill="y")
        scroll = ttk.Scrollbar(left, orient="vertical", command=listbox.yview)
        scroll.pack(side="right", fill="y")
        listbox.configure(yscrollcommand=scroll.set)

        right = ttk.Frame(outer)
        right.grid(row=0, column=1, sticky="nsew")
        right.rowconfigure(0, weight=1)
        right.columnconfigure(0, weight=1)

        preview_label = ttk.Label(right, text="Bild auswählen", anchor="center")
        preview_label.grid(row=0, column=0, sticky="nsew")

        path_label = ttk.Label(right, text="", wraplength=450)
        path_label.grid(row=1, column=0, sticky="ew", pady=(8, 0))

        state = {"photo": None, "path": None}

        for f in files:
            listbox.insert("end", f.name)

        def preview_selected(event=None):
            idx = listbox.curselection()
            if not idx:
                return
            path = files[idx[0]]
            state["path"] = path
            try:
                img = _load_pil_preview(path, (500, 500))
                photo = ImageTk.PhotoImage(img)
                state["photo"] = photo
                preview_label.configure(image=photo, text="")
                path_label.configure(text=str(path))
            except Exception as exc:
                preview_label.configure(
                    image="", text=f"Vorschau fehlgeschlagen:\n{exc}"
                )

        def choose():
            if state["path"] is None:
                return
            variable.set(str(state["path"]))
            win.destroy()

        listbox.bind("<<ListboxSelect>>", preview_selected)
        listbox.bind("<Double-1>", lambda e: choose())

        buttons = ttk.Frame(outer)
        buttons.grid(row=1, column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(buttons, text="Auswählen", command=choose).pack(side="left", padx=5)
        ttk.Button(buttons, text="Abbrechen", command=win.destroy).pack(side="left")

        listbox.selection_set(0)
        listbox.see(0)
        preview_selected()

    def image_panel(parent, row, label, variable, current_ref, column=0):
        """Image panel; returns a refresh function for record navigation."""
        box = ttk.LabelFrame(parent, text=label, padding=8)
        box.grid(
            row=row, column=column, sticky="nsew",
            padx=(0 if column == 0 else 6, 6 if column == 0 else 0),
            pady=6
        )
        parent.columnconfigure(column, weight=1)
        box.columnconfigure(1, weight=1)

        thumb = ttk.Label(box, text="Kein Bild", anchor="center")
        thumb.grid(row=0, column=0, rowspan=3, sticky="nsew", padx=(0, 12))
        thumb.configure(width=22)

        path_label = ttk.Label(
            box, text=variable.get() or "Kein Bild hinterlegt",
            wraplength=380
        )
        path_label.grid(row=0, column=1, sticky="w")

        def refresh_preview():
            ref = variable.get()
            path = image_path_from_ref(ref)
            if path and Image is not None and ImageTk is not None:
                try:
                    img = _load_pil_preview(path, (150, 190))
                    photo = ImageTk.PhotoImage(img)
                    thumb.configure(image=photo, text="")
                    thumb.image = photo
                except Exception:
                    thumb.configure(image="", text="Vorschau\nnicht möglich")
                    thumb.image = None
            else:
                thumb.configure(image="", text="Kein Bild")
                thumb.image = None
            path_label.configure(text=ref or "Kein Bild hinterlegt")

        def choose_file():
            path = filedialog.askopenfilename(
                parent=parent.winfo_toplevel(),
                title=f"{label} auswählen",
                filetypes=[
                    ("Bilddateien", "*.jpg *.jpeg *.png *.webp"),
                    ("Alle Dateien", "*.*")
                ]
            )
            if path:
                variable.set(path)
                refresh_preview()

        def choose_library():
            open_image_library(
                parent.winfo_toplevel(), variable,
                f"{label} aus DCardLabs-Bildbibliothek"
            )
            refresh_preview()

        def remove():
            variable.set("")
            refresh_preview()

        ttk.Button(
            box, text="Bild anzeigen",
            command=lambda: show_image_preview(
                parent.winfo_toplevel(), variable.get(), label
            )
        ).grid(row=1, column=1, sticky="w", pady=4)

        actions = ttk.Frame(box)
        actions.grid(row=2, column=1, sticky="w", pady=(2, 0))
        ttk.Button(
            actions, text="Datei ersetzen…", command=choose_file
        ).pack(side="left", padx=(0, 5))
        ttk.Button(
            actions, text="Aus Bibliothek…", command=choose_library
        ).pack(side="left", padx=5)
        ttk.Button(
            actions, text="Entfernen", command=remove
        ).pack(side="left", padx=5)

        refresh_preview()
        return refresh_preview


    def edit_card(event=None):
        sel = card_tree.selection()
        if not sel:
            return

        # Work with the actual table order so "Vorherige/Nächste" follows
        # exactly the records currently visible in KARTEN.
        card_ids = [
            int(card_tree.item(item, "values")[0])
            for item in card_tree.get_children("")
        ]
        if not card_ids:
            return

        selected_id = int(card_tree.item(sel[0], "values")[0])
        try:
            current_index = card_ids.index(selected_id)
        except ValueError:
            current_index = 0

        win = tk.Toplevel(root)
        win.title("Karte bearbeiten")
        fit_dialog(win, 1100, 920, min_width=900, min_height=650)

        outer = ttk.Frame(win, padding=12)
        outer.pack(fill="both", expand=True)

        header = ttk.Frame(outer)
        header.pack(fill="x", pady=(0, 8))

        record_label = ttk.Label(header, text="", font=("", 12, "bold"))
        record_label.pack(side="left")

        nav = ttk.Frame(header)
        nav.pack(side="right")

        canvas = tk.Canvas(outer, highlightthickness=0)
        scrollbar = ttk.Scrollbar(outer, orient="vertical", command=canvas.yview)
        canvas.configure(yscrollcommand=scrollbar.set)
        canvas.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        frm = ttk.Frame(canvas)
        window_id = canvas.create_window((0, 0), window=frm, anchor="nw")
        frm.columnconfigure(0, weight=1)
        frm.columnconfigure(1, weight=1)

        def on_frame_configure(event):
            canvas.configure(scrollregion=canvas.bbox("all"))

        def on_canvas_configure(event):
            canvas.itemconfigure(window_id, width=event.width)

        frm.bind("<Configure>", on_frame_configure)
        canvas.bind("<Configure>", on_canvas_configure)

        fields = [
            ("Kategorie", 1), ("Thema / Franchise", 2),
            ("Team / Verein", 3), ("Hersteller", 4),
            ("Set / Serie", 5), ("Name / Titel", 6),
            ("Saison / Jahr", 7), ("Kartennummer", 8),
            ("Kartentyp", 9), ("Variante", 10),
            ("Numbered (0/1)", 11), ("Seriennummer", 12),
            ("Print Run", 13), ("Sprache", 14),
        ]

        entries = {}
        for row, (label, idx) in enumerate(fields):
            ttk.Label(frm, text=label).grid(
                row=row, column=0, sticky="w", padx=5, pady=4
            )
            e = ttk.Entry(frm)
            e.grid(
                row=row, column=1, columnspan=1,
                sticky="ew", padx=5, pady=4
            )
            entries[idx] = e

        image_area = ttk.Frame(frm)
        image_area.grid(
            row=len(fields), column=0, columnspan=2,
            sticky="ew", pady=(8, 4)
        )
        image_area.columnconfigure(0, weight=1)
        image_area.columnconfigure(1, weight=1)

        front_var = tk.StringVar()
        back_var = tk.StringVar()
        front_refresh = image_panel(
            image_area, 0, "Vorderseite", front_var, "", column=0
        )
        back_refresh = image_panel(
            image_area, 0, "Rückseite", back_var, "", column=1
        )

        status_label = ttk.Label(
            frm, text="", wraplength=900
        )
        status_label.grid(
            row=len(fields)+1, column=0, columnspan=2,
            sticky="w", padx=5, pady=(4, 8)
        )

        # Read-only technical database view. This intentionally lives below the
        # normal card fields so the everyday editor stays uncluttered.
        tech_box = ttk.LabelFrame(frm, text="Technische Daten / Datenbank (nur Anzeige)", padding=8)
        tech_box.grid(row=len(fields)+2, column=0, columnspan=2, sticky="ew", padx=5, pady=(4, 8))
        tech_box.columnconfigure(1, weight=1)
        tech_vars = {}
        tech_fields = [
            ("card_id", "Karten-ID"), ("created_at", "Erstellt am"),
            ("ocr_status", "OCR-Status"), ("ocr_confidence", "OCR-Konfidenz"),
            ("ocr_raw", "OCR-Rohdaten"), ("ocr_name", "OCR Name"),
            ("ocr_team", "OCR Team"), ("ocr_league", "OCR Liga"),
            ("ocr_set", "OCR Set"), ("ocr_card_type", "OCR Kartentyp"),
            ("ocr_card_number", "OCR Kartennummer"), ("ocr_serial_number", "OCR Seriennummer"),
            ("ocr_print_run", "OCR Print Run"), ("ocr_variant", "OCR Variante"),
            ("squad_number", "Kadernummer"), ("position", "Position"),
            ("club_debut_season", "Vereinsdebüt-Saison"),
            ("back_ocr_raw", "Rückseiten-OCR"), ("back_ocr_confidence", "Rückseiten-OCR-Konfidenz"),
            ("back_year", "Rückseite Jahr"), ("back_card_number", "Rückseite Kartennummer"),
            ("back_serial_number", "Rückseite Seriennummer"), ("back_print_run", "Rückseite Print Run"),
            ("front_image_sha256", "Vorderseite SHA-256"), ("back_image_sha256", "Rückseite SHA-256"),
            ("ebay_template_key", "eBay Vorlage"), ("ebay_status", "eBay Status"),
            ("ebay_sku", "eBay SKU"), ("ebay_exported_at", "eBay exportiert am"),
            ("ebay_scheduled_at", "eBay geplant für"), ("ebay_item_id", "eBay Item ID"),
            ("ebay_sold_at", "eBay verkauft am"), ("ebay_offer_price", "eBay Angebotspreis"),
            ("ebay_offer_id", "eBay Offer ID"), ("ebay_listing_id", "eBay Listing ID"),
            ("ebay_sale_price", "eBay Verkaufspreis"),
            ("ebay_fees", "eBay Gebühren"), ("ebay_order_id", "eBay Order ID"),
        ]
        for r, (key, label) in enumerate(tech_fields):
            ttk.Label(tech_box, text=label).grid(row=r, column=0, sticky="w", padx=4, pady=2)
            var = tk.StringVar()
            tech_vars[key] = var
            ttk.Entry(tech_box, textvariable=var, state="readonly").grid(row=r, column=1, sticky="ew", padx=4, pady=2)

        def load_technical_data(card_id):
            # sqlite3.Connection has no .description; column metadata belongs
            # to the cursor that executed the SELECT. Keep both queries on a
            # cursor so the technical fields refresh reliably when navigating
            # between cards.
            c = db()
            cur = c.cursor()
            try:
                card_row = cur.execute(
                    "SELECT * FROM cards WHERE card_id=?", (card_id,)
                ).fetchone()
                card_cols = [d[0] for d in cur.description] if card_row else []

                ebay_row = cur.execute(
                    "SELECT * FROM ebay_listings WHERE card_id=?", (card_id,)
                ).fetchone()
                ebay_cols = [d[0] for d in cur.description] if ebay_row else []
            finally:
                c.close()

            data = dict(zip(card_cols, card_row)) if card_row else {}
            ebay = dict(zip(ebay_cols, ebay_row)) if ebay_row else {}
            for key, var in tech_vars.items():
                source_key = key
                if key.startswith("ebay_"):
                    source_key = key[5:]
                value = ebay.get(source_key, "") if key.startswith("ebay_") else data.get(source_key, "")
                if key == "ebay_template_key":
                    value = ebay.get("template_key", "")
                elif key == "ebay_offer_id":
                    # DB column keeps the explicit ebay_ prefix; do not strip it.
                    value = ebay.get("ebay_offer_id", "")
                elif key == "ebay_listing_id":
                    value = ebay.get("listing_id", "")
                elif key == "ebay_offer_price":
                    value = ebay.get("price", "")
                    if value not in ("", None):
                        try:
                            value = f"{float(value):.2f}".replace(".", ",") + " €"
                        except (TypeError, ValueError):
                            value = str(value)
                elif key == "ebay_sale_price":
                    # 0.0 is the database default, not an actual sale price.
                    try:
                        value = float(ebay.get("sale_price", 0) or 0)
                        value = "" if value <= 0 else f"{value:.2f}".replace(".", ",") + " €"
                    except (TypeError, ValueError):
                        value = ""
                var.set("" if value is None else str(value))

        def load_card(index, preserve_selection=True):
            nonlocal current_index

            if index < 0 or index >= len(card_ids):
                return

            card_id = card_ids[index]
            c = db()
            row = c.execute(
                """SELECT category, theme, team, manufacturer, set_name, title,
                          season_year, card_number, card_type, variant,
                          is_numbered, serial_number, print_run, language,
                          front_image, back_image
                   FROM cards WHERE card_id=?""",
                (card_id,)
            ).fetchone()
            c.close()

            if not row:
                messagebox.showerror(
                    APP, f"Karte #{card_id} wurde nicht gefunden.", parent=win
                )
                return

            current_index = index

            for idx, e in entries.items():
                value = row[idx-1] if idx-1 < 14 else ""
                e.delete(0, "end")
                e.insert(0, "" if value is None else str(value))

            front_var.set(row[14] or "")
            back_var.set(row[15] or "")
            front_refresh()
            back_refresh()
            load_technical_data(card_id)

            title = row[5] or "Unbenannte Karte"
            record_label.configure(
                text=f"Karte #{card_id}  •  {title}  •  "
                     f"{current_index+1} von {len(card_ids)}"
            )

            has_prev = current_index > 0
            has_next = current_index < len(card_ids)-1
            prev_btn.configure(state="normal" if has_prev else "disabled")
            next_btn.configure(state="normal" if has_next else "disabled")

            status_label.configure(
                text="Vorder- und Rückseite werden direkt nebeneinander angezeigt. "
                     "Änderungen mit „Speichern“ übernehmen."
            )

            if preserve_selection:
                item = card_tree.get_children("")[current_index]
                card_tree.selection_set(item)
                card_tree.focus(item)
                card_tree.see(item)

            canvas.yview_moveto(0)

        def save():
            nonlocal current_index
            values = [entries[idx].get().strip() for idx in range(1, 15)]
            empty_values = ("", "none", "null", "-", "—")

            values[10] = 1 if values[10].lower() in (
                "1", "ja", "yes", "true"
            ) else 0

            try:
                values[11] = (
                    None if values[11].lower() in empty_values
                    else int(values[11])
                )
                values[12] = (
                    None if values[12].lower() in empty_values
                    else int(values[12])
                )
            except ValueError:
                messagebox.showerror(
                    APP,
                    "Seriennummer und Print Run müssen Zahlen sein "
                    "oder leer bleiben.",
                    parent=win
                )
                return

            card_id = card_ids[current_index]
            try:
                update_card(
                    card_id,
                    values,
                    front_var.get(),
                    back_var.get()
                )
                refresh()

                # Re-read current refs after save, then retain the current record.
                card_ids[:] = [
                    int(card_tree.item(item, "values")[0])
                    for item in card_tree.get_children("")
                ]
                if card_id in card_ids:
                    current_index = card_ids.index(card_id)

                messagebox.showinfo(
                    "Gespeichert",
                    f"Karte #{card_id} wurde gespeichert.",
                    parent=win
                )
                load_card(current_index)
            except Exception as exc:
                messagebox.showerror(APP, str(exc), parent=win)

        def go_previous():
            if current_index > 0:
                load_card(current_index - 1)

        def go_next():
            if current_index < len(card_ids)-1:
                load_card(current_index + 1)

        prev_btn = ttk.Button(
            nav, text="◀ Vorherige", command=go_previous
        )
        prev_btn.pack(side="left", padx=4)

        next_btn = ttk.Button(
            nav, text="Nächste ▶", command=go_next
        )
        next_btn.pack(side="left", padx=4)

        # Fixed action bar OUTSIDE the scrollable canvas so the buttons remain
        # reachable even when the dialog is smaller than the content.
        bottom = ttk.Frame(outer, padding=(5, 8))
        bottom.pack(side="bottom", fill="x")

        ttk.Button(
            bottom, text="Speichern", command=save
        ).pack(side="right", padx=5)
        ttk.Button(
            bottom, text="Schließen", command=win.destroy
        ).pack(side="right", padx=5)

        # Keyboard navigation while the editor is open.
        win.bind("<Alt-Left>", lambda e: go_previous())
        win.bind("<Alt-Right>", lambda e: go_next())

        load_card(current_index)

    card_tree.bind("<Double-1>", edit_card)

    ttk.Button(
        inv_tab,
        text="＋ Neuer Inventareintrag",
        command=lambda: open_manual_inventory_dialog(root, on_saved=refresh)
    ).pack(anchor="e", pady=(0, 8))

    ttk.Button(
        inv_tab,
        text="🗑 Inventareintrag löschen",
        command=lambda: (
            confirm_and_delete(
                root, "inventory",
                int(inv_tree.item(inv_tree.selection()[0], "values")[0]),
                on_deleted=refresh
            ) if inv_tree.selection() else None
        )
    ).pack(anchor="e", pady=(0, 8))

    def edit_selected_inventory():
        sel=inv_tree.selection()
        if not sel:
            messagebox.showinfo("Inventar","Bitte zuerst einen Inventareintrag auswählen.",parent=root); return
        iid=int(inv_tree.item(sel[0],"values")[0]); open_inventory_editor(root,iid,on_saved=refresh)
    ttk.Button(inv_tab,text="✎ Inventareintrag bearbeiten",command=edit_selected_inventory).pack(anchor="e",pady=(0,8))

    inv_tree = make_tree(
        inv_tab,
        ["ID", "Karten-ID", "Karte", "Menge", "Zustand", "Lagerort", "Notizen"],
        [60,80,300,80,100,180,300]
    )
    inv_tree.bind("<Double-1>", lambda e: edit_selected_inventory())
    ttk.Button(
        buy_tab,
        text="＋ Kauf manuell hinzufügen",
        command=lambda: open_manual_purchase_dialog(root, on_saved=refresh)
    ).pack(anchor="e", pady=(0, 8))

    ttk.Button(
        buy_tab,
        text="🗑 Kauf löschen",
        command=lambda: (
            confirm_and_delete(
                root, "purchase",
                int(buy_tree.item(buy_tree.selection()[0], "values")[0]),
                on_deleted=refresh
            ) if buy_tree.selection() else None
        )
    ).pack(anchor="e", pady=(0, 8))

    def edit_selected_purchase():
        sel=buy_tree.selection()
        if not sel:
            messagebox.showinfo("Käufe","Bitte zuerst einen Kauf auswählen.",parent=root); return
        pid=int(buy_tree.item(sel[0],"values")[0]); open_purchase_editor(root,pid,on_saved=refresh)
    ttk.Button(buy_tab,text="✎ Kauf bearbeiten",command=edit_selected_purchase).pack(anchor="e",pady=(0,8))

    buy_tree = make_tree(
        buy_tab,
        ["ID", "Datum", "Plattform", "Verkäufer", "Karten",
         "Kaufpreis", "Versand", "Gesamt"],
        [60,100,130,180,80,110,100,110]
    )
    buy_tree.bind("<Double-1>", lambda e: edit_selected_purchase())

    # ---------------- VERKÄUFE ----------------
    sales_top = ttk.Frame(sales_tab)
    sales_top.pack(fill="x", pady=(0, 8))

    ttk.Label(
        sales_top,
        text="Verkäufe erfassen und bearbeiten. Doppelklick öffnet die Detailansicht."
    ).pack(side="left")

    sales_actions = ttk.Frame(sales_top)
    sales_actions.pack(side="right")

    def add_sale():
        open_manual_sale_dialog(root, on_saved=refresh)

    def delete_sale():
        sel = sales_tree.selection()
        if not sel:
            messagebox.showinfo("Verkäufe", "Bitte zuerst einen Verkauf auswählen.", parent=root)
            return
        sale_id = int(sales_tree.item(sel[0], "values")[0])
        confirm_and_delete(root, "ebay_sales", sale_id, on_deleted=refresh)

    ttk.Button(sales_actions, text="＋ Verkauf hinzufügen", command=add_sale).pack(side="left", padx=4)
    ttk.Button(sales_actions, text="🗑 Verkauf löschen", command=delete_sale).pack(side="left", padx=4)
    ttk.Button(sales_actions, text="↻ Aktualisieren", command=refresh).pack(side="left", padx=4)

    sales_tree = make_tree(
        sales_tab,
        ["ID", "Datum", "Karten-ID", "Karte", "eBay Item ID", "Order ID",
         "Menge", "Brutto", "Versand", "eBay-Gebühren", "Netto", "Status"],
        [60,110,80,260,130,130,60,100,90,110,100,110]
    )

    def edit_sale(event=None):
        sel = sales_tree.selection()
        if not sel:
            return
        items = list(sales_tree.get_children(""))
        item = sel[0]
        if item not in items:
            return
        index = items.index(item)
        sale_ids = [int(sales_tree.item(i, "values")[0]) for i in items]
        open_sale_editor(root, sale_ids, index, on_saved=refresh)

    sales_tree.bind("<Double-1>", edit_sale)
    sales_tree.bind("<Delete>", lambda e: delete_sale())

    # ---------------- eBAY ----------------
    ebay_top = ttk.Frame(ebay_tab)
    ebay_top.pack(fill="x", pady=(0, 8))

    ttk.Label(
        ebay_top,
        text="Karte auswählen und daraus einen bearbeitbaren eBay-Entwurf erstellen."
    ).pack(side="left")

    ebay_settings = ebay_get_settings()
    settings_box = ttk.LabelFrame(ebay_tab, text="eBay Stammdaten", padding=8)
    settings_box.pack(fill="x", pady=(0, 8))
    ebay_cat_name_var = tk.StringVar(value=ebay_settings["category_name"])
    ebay_cat_id_var = tk.StringVar(value=ebay_settings["category_id"])
    ebay_ungraded_var = tk.StringVar(value=ebay_settings["condition_ungraded_id"])
    ebay_graded_var = tk.StringVar(value=ebay_settings["condition_graded_id"])
    for col, (label, var, width) in enumerate([
        ("Kategorie", ebay_cat_name_var, 28),
        ("Category ID", ebay_cat_id_var, 12),
        ("Ungraded ID", ebay_ungraded_var, 12),
        ("Graded ID", ebay_graded_var, 12),
    ]):
        ttk.Label(settings_box, text=label).grid(row=0, column=col, sticky="w", padx=4)
        ttk.Entry(settings_box, textvariable=var, width=width).grid(row=1, column=col, sticky="ew", padx=4)
    def save_ebay_settings_ui():
        values = [ebay_cat_id_var.get(), ebay_ungraded_var.get(), ebay_graded_var.get()]
        if not all(v.strip().isdigit() for v in values):
            messagebox.showerror("eBay Stammdaten", "Category ID und Condition IDs müssen numerisch sein.", parent=root)
            return
        ebay_save_settings(ebay_cat_name_var.get(), *values)
        messagebox.showinfo("eBay Stammdaten", "eBay-Stammdaten gespeichert.", parent=root)
    ttk.Button(settings_box, text="💾 eBay-Stammdaten speichern", command=save_ebay_settings_ui).grid(row=1, column=4, padx=8)

    ebay_selected = tk.StringVar(value="Keine Karte ausgewählt")

    def open_ebay_editor(card_id=None):
        """Open an eBay draft editor with previous/next draft navigation."""
        # If opened from the draft list, determine current position.
        draft_card_ids = []
        for item in ebay_tree.get_children(""):
            vals = ebay_tree.item(item, "values")
            if len(vals) >= 2:
                try:
                    draft_card_ids.append(int(vals[1]))
                except (TypeError, ValueError):
                    pass

        if card_id is None:
            sel = ebay_tree.selection()
            if sel:
                try:
                    card_id = int(ebay_tree.item(sel[0], "values")[1])
                except (TypeError, ValueError):
                    card_id = None

        if not card_id:
            messagebox.showinfo(
                "eBay",
                "Bitte zuerst einen eBay-Entwurf auswählen.",
                parent=root
            )
            return

        if card_id in draft_card_ids:
            draft_index = draft_card_ids.index(card_id)
        else:
            draft_index = -1

        card = ebay_get_card(card_id)
        if not card:
            messagebox.showerror(
                "eBay", f"Karte #{card_id} wurde nicht gefunden.", parent=root
            )
            return

        c = db()
        existing = c.execute(
            """SELECT title,description,condition,price,listing_format,category,sku,status
               FROM ebay_listings WHERE card_id=?""", (card_id,)
        ).fetchone()
        c.close()

        if not existing:
            # Create the first draft on the "Karte auswählen / Entwurf erstellen" path.
            ebay_cfg = ebay_get_settings()
            cond0 = ebay_cfg["condition_ungraded_id"]
            title0 = ebay_generate_title(card)
            desc0 = ebay_generate_description(card, card.get("condition") or "NM")
            price0 = 0
            fmt0 = "Festpreis"
            cat0 = ebay_cfg["category_id"]
            sku0 = f"DC-{card_id:06d}"
            status0 = "Entwurf"
            try:
                ebay_save_draft(
                    card_id, title0, desc0, cond0, price0,
                    fmt0, cat0, sku0
                )
                refresh()
                draft_card_ids = [
                    int(ebay_tree.item(item, "values")[1])
                    for item in ebay_tree.get_children("")
                ]
                draft_index = (
                    draft_card_ids.index(card_id)
                    if card_id in draft_card_ids else -1
                )
                LOGGER.info(
                    "eBay-Entwurf für Karte #%s automatisch angelegt.",
                    card_id
                )
            except Exception as exc:
                log_exception(
                    f"eBay-Entwurf für Karte #{card_id} konnte nicht angelegt werden",
                    exc
                )
                messagebox.showerror(
                    "eBay-Entwurf",
                    f"Der eBay-Entwurf konnte nicht angelegt werden:\n{exc}",
                    parent=root
                )
                return
        else:
            title0, desc0, cond0, price0, fmt0, cat0, sku0, status0 = existing
            cfg = ebay_get_settings()
            if not str(cat0 or "").isdigit():
                cat0 = cfg["category_id"]

        win = tk.Toplevel(root)
        win.title(f"eBay-Entwurf – Karte #{card_id}")
        fit_dialog(win, 1180, 900, min_width=980, min_height=680)
        win.transient(root)

        outer = ttk.Frame(win, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)
        outer.rowconfigure(2, weight=0)

        header = ttk.Frame(outer)
        header.grid(row=0, column=0, columnspan=2, sticky="ew", pady=(0, 10))
        header.columnconfigure(1, weight=1)

        record_label = ttk.Label(
            header,
            text="",
            font=("", 13, "bold")
        )
        record_label.grid(row=0, column=0, sticky="w")

        nav = ttk.Frame(header)
        nav.grid(row=0, column=2, sticky="e")

        imgbox = ttk.LabelFrame(outer, text="Bilder", padding=10)
        imgbox.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        imgbox.columnconfigure(0, weight=1)
        imgbox.columnconfigure(1, weight=1)

        editor_frame = ttk.Frame(outer)
        editor_frame.grid(row=1, column=1, sticky="nsew")
        editor_frame.columnconfigure(1, weight=1)
        editor_frame.rowconfigure(9, weight=1)

        title_var = tk.StringVar()
        template_key_state = {"value": "football"}
        cond_var = tk.StringVar()
        price_var = tk.StringVar()
        fmt_var = tk.StringVar()
        cat_var = tk.StringVar()
        sku_var = tk.StringVar()
        status_var = tk.StringVar()

        ttk.Label(editor_frame, text="Titel (max. 80 Zeichen)").grid(
            row=0, column=0, sticky="w", pady=4
        )
        ttk.Entry(editor_frame, textvariable=title_var).grid(
            row=0, column=1, sticky="ew", pady=4
        )
        title_count = ttk.Label(editor_frame, text="")
        title_count.grid(row=0, column=2, sticky="e", padx=5)

        def update_title_count(*_):
            title_count.configure(text=f"{len(title_var.get())}/80")

        title_var.trace_add("write", update_title_count)

        ttk.Label(editor_frame, text="eBay Zustand").grid(
            row=1, column=0, sticky="w", pady=4
        )
        ttk.Combobox(
            editor_frame, textvariable=cond_var,
            values=["4000 – Ungraded", "2750 – Graded"],
            state="readonly"
        ).grid(row=1, column=1, sticky="w", pady=4)

        ttk.Label(editor_frame, text="Preis (€)").grid(
            row=2, column=0, sticky="w", pady=4
        )
        ttk.Entry(editor_frame, textvariable=price_var, width=18).grid(
            row=2, column=1, sticky="w", pady=4
        )

        ttk.Label(editor_frame, text="Angebotsformat").grid(
            row=3, column=0, sticky="w", pady=4
        )
        ttk.Combobox(
            editor_frame, textvariable=fmt_var,
            values=["Festpreis", "Auktion"],
            state="readonly"
        ).grid(row=3, column=1, sticky="w", pady=4)

        ttk.Label(editor_frame, text="eBay Kategorie-ID").grid(
            row=4, column=0, sticky="w", pady=4
        )
        ttk.Entry(editor_frame, textvariable=cat_var, state="readonly").grid(
            row=4, column=1, sticky="ew", pady=4
        )

        ttk.Label(editor_frame, text="SKU / Lagerkennung").grid(
            row=5, column=0, sticky="w", pady=4
        )
        ttk.Entry(editor_frame, textvariable=sku_var).grid(
            row=5, column=1, sticky="ew", pady=4
        )

        ttk.Label(editor_frame, text="Status").grid(
            row=6, column=0, sticky="w", pady=4
        )
        ttk.Combobox(
            editor_frame, textvariable=status_var,
            values=["Entwurf", "Bereit", "Offer erstellt", "Eingestellt", "Verkauft", "Beendet"],
            state="readonly"
        ).grid(row=6, column=1, sticky="w", pady=4)

        ttk.Label(editor_frame, text="eBay Vorlage").grid(row=7, column=0, sticky="w", pady=4)
        template_names = {k: v["name"] for k,v in _ebay_template_catalog().items()}
        template_name_var = tk.StringVar(value=template_names["football"])
        template_combo = ttk.Combobox(editor_frame, textvariable=template_name_var,
                                      values=list(template_names.values()), state="readonly")
        template_combo.grid(row=7, column=1, sticky="w", pady=4)
        def on_template_change(*_):
            reverse_templates = {v: k for k, v in template_names.items()}
            template_key_state["value"] = reverse_templates.get(template_name_var.get(), "football")
            current_card = ebay_get_card(card_id) or {}
            rebuild_required_panel(current_card, template_key_state["value"])
        template_combo.bind("<<ComboboxSelected>>", on_template_change)

        # Required-field panel: only fields the user must complete before export.
        #  Template-specific required aspects are read from
        # the actual eBay CSV, so football/non-sport stay in sync with eBay.
        required_box = ttk.LabelFrame(editor_frame, text="eBay-Pflichtfelder", padding=8)
        required_box.grid(row=10, column=0, columnspan=3, sticky="ew", pady=(8, 6))
        required_box.columnconfigure(0, weight=1)

        required_status = ttk.Label(required_box, text="Prüfung läuft…", font=("", 10, "bold"))
        required_status.grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 6))

        required_scroll_frame = ttk.Frame(required_box)
        required_scroll_frame.grid(row=1, column=0, sticky="nsew")
        required_scroll_frame.columnconfigure(0, weight=1)
        required_scroll_frame.rowconfigure(0, weight=1)

        required_canvas = tk.Canvas(required_scroll_frame, height=140, highlightthickness=0)
        required_canvas.grid(row=0, column=0, sticky="nsew")
        required_scroll = ttk.Scrollbar(required_scroll_frame, orient="vertical", command=required_canvas.yview)
        required_scroll.grid(row=0, column=1, sticky="ns")
        required_canvas.configure(yscrollcommand=required_scroll.set)

        required_inner = ttk.Frame(required_canvas)
        required_window = required_canvas.create_window((0, 0), window=required_inner, anchor="nw")

        def _required_on_frame_configure(_event=None):
            required_canvas.configure(scrollregion=required_canvas.bbox("all"))

        def _required_on_canvas_configure(event):
            required_canvas.itemconfigure(required_window, width=event.width)

        required_inner.bind("<Configure>", _required_on_frame_configure)
        required_canvas.bind("<Configure>", _required_on_canvas_configure)

        def _required_mousewheel(event):
            required_canvas.yview_scroll(int(-1 * (event.delta / 120)), "units")

        required_canvas.bind("<MouseWheel>", _required_mousewheel)
        required_inner.bind("<MouseWheel>", _required_mousewheel)

        required_rows = []

        ttk.Label(editor_frame, text="Beschreibung").grid(
            row=8, column=0, sticky="nw", pady=4
        )
        desc_frame = ttk.Frame(editor_frame)
        desc_frame.grid(row=9, column=0, columnspan=3, sticky="nsew", pady=4)
        desc_frame.rowconfigure(0, weight=1)
        desc_frame.columnconfigure(0, weight=1)

        desc = tk.Text(desc_frame, wrap="word", height=9)
        desc.grid(row=0, column=0, sticky="nsew")
        desc_scroll = ttk.Scrollbar(desc_frame, orient="vertical", command=desc.yview)
        desc_scroll.grid(row=0, column=1, sticky="ns")
        desc.configure(yscrollcommand=desc_scroll.set)
        desc.bind("<KeyRelease>", lambda e: validate_required_fields())

        image_refs = {"photos": []}

        def load_images(current_card):
            for child in imgbox.winfo_children():
                child.destroy()
            image_refs["photos"].clear()

            for col, (label, ref) in enumerate((
                ("Vorderseite", current_card.get("front_image", "")),
                ("Rückseite", current_card.get("back_image", ""))
            )):
                box = ttk.Frame(imgbox)
                box.grid(row=0, column=col, sticky="nsew", padx=5)
                ttk.Label(box, text=label, font=("", 10, "bold")).pack()

                image_label = ttk.Label(box, text="Kein Bild", anchor="center")
                image_label.pack(fill="both", expand=True, pady=8)

                path = image_path_from_ref(ref)
                if path and Image is not None and ImageTk is not None:
                    try:
                        img = _load_pil_preview(path, (240, 360))
                        photo = ImageTk.PhotoImage(img)
                        image_label.configure(image=photo, text="")
                        image_label.image = photo
                        image_refs["photos"].append(photo)
                    except Exception:
                        image_label.configure(text="Vorschau nicht möglich")

                ttk.Label(
                    box, text=ref or "Kein Bild hinterlegt", wraplength=220
                ).pack()

        def rebuild_required_panel(current_card, template_key):
            for child in required_inner.winfo_children():
                child.destroy()
            required_rows.clear()
            checks = [
                ("Titel", lambda: title_var.get()),
                ("Zustand", lambda: cond_var.get()),
                ("Preis", lambda: price_var.get()),
                ("Beschreibung", lambda: desc.get("1.0", "end").strip()),
                ("Format", lambda: fmt_var.get()),
                ("Bilder", lambda: current_card.get("front_image") or current_card.get("back_image")),
                ("Menge", lambda: _ebay_inventory_quantity(int(current_card.get("card_id", 0) or 0))),
            ]
            ttk.Label(required_inner, text="Vor dem eBay-Export erforderlich:", font=("",10,"bold")).grid(row=1,column=0,columnspan=2,sticky="w",padx=4,pady=(0,4))
            for r,(label,getter) in enumerate(checks,start=2):
                lab=ttk.Label(required_inner,text="🔴 "+label); lab.grid(row=r,column=0,sticky="w",padx=4,pady=1)
                state=ttk.Label(required_inner,text="–"); state.grid(row=r,column=1,sticky="e",padx=4,pady=1)
                required_rows.append((label,getter,lab,state))
            auto_row=len(checks)+3
            ttk.Label(required_inner,text="Automatisch beim Export:",font=("",10,"bold")).grid(row=auto_row,column=0,columnspan=2,sticky="w",padx=4,pady=(6,2))
            cfg=_ebay_template_catalog().get(template_key) or _ebay_template_catalog()["football"]
            auto_items=[
                ("Sportart",cfg.get("sport") or "– (bei Non-Sport nicht gesetzt)"),
                ("Category",cat_var.get() or ebay_get_settings()["category_id"]),
                ("Duration","GTC"),("StartPrice","aus Preis"),("Quantity","aus Inventar"),
                ("Location","Köln"),("DispatchTimeMax","3 Tage"),("ReturnsAcceptedOption","ReturnsNotAccepted"),
                ("PicURL","aus Kartenbildern / Google Drive"),
            ]
            for r,(label,value) in enumerate(auto_items,start=auto_row+1):
                ttk.Label(required_inner,text="⚙ "+label).grid(row=r,column=0,sticky="w",padx=4,pady=1)
                ttk.Label(required_inner,text=str(value),wraplength=420).grid(row=r,column=1,sticky="e",padx=4,pady=1)
            validate_required_fields()

        def validate_required_fields():
            missing = []
            for label, getter, lab, state in required_rows:
                try:
                    value = getter()
                    ok = bool(str(value or "").strip())
                    if label == "Preis":
                        try: ok = float(str(value).replace(",", ".")) > 0
                        except Exception: ok = False
                    if label == "Menge":
                        try: ok = int(value or 0) > 0
                        except Exception: ok = False
                except Exception:
                    ok = False
                lab.configure(text=("🟢 " if ok else "🔴 ") + label)
                state.configure(text="OK" if ok else "FEHLT")
                if not ok:
                    missing.append(label)
            if required_rows:
                required_status.configure(
                    text=(f"✅ {len(required_rows)-len(missing)} Pflichtfelder vollständig" if not missing else
                          f"⚠ {len(required_rows)-len(missing)} vollständig · {len(missing)} fehlen: {', '.join(missing)}")
                )
            else:
                required_status.configure(text="Keine zusätzlichen Pflichtmerkmale aus der Vorlage erkannt.")
            return missing

        for var in (title_var, cond_var, price_var, fmt_var, cat_var):
            var.trace_add("write", lambda *_: validate_required_fields())

        def load_draft(new_card_id):
            nonlocal card_id, draft_index

            c = db()
            row = c.execute(
                """SELECT title,description,condition,price,listing_format,category,sku,status,template_key
                   FROM ebay_listings WHERE card_id=?""", (new_card_id,)
            ).fetchone()
            c.close()

            current_card = ebay_get_card(new_card_id)
            if not row or not current_card:
                return False

            card_id = new_card_id
            if card_id in draft_card_ids:
                draft_index = draft_card_ids.index(card_id)

            t, d, co, pr, fm, ca, sk, st, template_key = row
            title_var.set(t or "")
            cfg = ebay_get_settings()
            cond_id = str(co or cfg["condition_ungraded_id"])
            if cond_id == str(cfg["condition_graded_id"]):
                cond_var.set(f"{cfg['condition_graded_id']} – Graded")
            else:
                cond_var.set(f"{cfg['condition_ungraded_id']} – Ungraded")
            price_var.set("" if pr in (None, 0) else str(pr).replace(".", ","))
            fmt_var.set(fm or "Festpreis")
            cat_var.set(ca or cfg["category_id"])
            sku_var.set(sk or "")
            status_var.set(st or "Entwurf")
            desc.delete("1.0", "end")
            desc.insert("1.0", d or "")

            current_card["card_id"] = card_id
            template_key_state["value"] = template_key or "football"
            template_name_var.set(template_names.get(template_key_state["value"], template_names["football"]))
            load_images(current_card)
            rebuild_required_panel(current_card, template_key_state["value"])

            record_label.configure(
                text=f"Entwurf für Karte #{card_id} • "
                     f"{current_card.get('title') or 'Unbenannt'} • "
                     f"{draft_index + 1} von {len(draft_card_ids)}"
            )

            prev_btn.configure(
                state="normal" if draft_index > 0 else "disabled"
            )
            next_btn.configure(
                state="normal"
                if draft_index >= 0 and draft_index < len(draft_card_ids) - 1
                else "disabled"
            )

            # Keep the main table selection in sync.
            for item in ebay_tree.get_children(""):
                vals = ebay_tree.item(item, "values")
                if len(vals) >= 2 and str(vals[1]) == str(card_id):
                    ebay_tree.selection_set(item)
                    ebay_tree.focus(item)
                    ebay_tree.see(item)
                    break

            update_title_count()
            initial_values["data"] = current_values()
            return True

        def save(silent=False):
            missing = validate_required_fields()
            if len(title_var.get()) > 80:
                messagebox.showerror(
                    "eBay",
                    "Der Titel darf maximal 80 Zeichen lang sein.",
                    parent=win
                )
                return False

            try:
                price = float((price_var.get() or "0").replace(",", "."))
            except ValueError:
                messagebox.showerror(
                    "eBay", "Bitte einen gültigen Preis eingeben.", parent=win
                )
                return False

            cfg = ebay_get_settings()
            cond_text = cond_var.get().strip()
            cond_id = cfg["condition_graded_id"] if cond_text.startswith(str(cfg["condition_graded_id"])) else cfg["condition_ungraded_id"]
            ebay_save_draft(
                card_id,
                title_var.get().strip(),
                desc.get("1.0", "end").strip(),
                cond_id,
                price,
                fmt_var.get(),
                cat_var.get().strip() or cfg["category_id"],
                sku_var.get().strip(),
                status_var.get() or "Entwurf",
                template_key_state["value"]
            )
            refresh()
            initial_values["data"] = current_values()

            if not silent:
                messagebox.showinfo(
                    "eBay",
                    f"eBay-Entwurf für Karte #{card_id} gespeichert.",
                    parent=win
                )
            return True

        def current_values():
            return (
                title_var.get(),
                desc.get("1.0", "end").strip(),
                cond_var.get(),
                price_var.get(),
                fmt_var.get(),
                cat_var.get(),
                sku_var.get(),
                status_var.get(),
                template_key_state["value"],
            )

        initial_values = {"data": None}

        def has_unsaved_changes():
            return (
                initial_values["data"] is not None
                and current_values() != initial_values["data"]
            )

        def navigate(direction):
            if not draft_card_ids or draft_index < 0:
                return

            target = draft_index + direction
            if target < 0 or target >= len(draft_card_ids):
                return

            if has_unsaved_changes():
                answer = messagebox.askyesnocancel(
                    "Ungespeicherte Änderungen",
                    "Du hast Änderungen vorgenommen, die noch nicht gespeichert wurden.\n\n"
                    "Ja = speichern und wechseln\n"
                    "Nein = Änderungen verwerfen und wechseln\n"
                    "Abbrechen = im aktuellen Entwurf bleiben",
                    parent=win
                )
                if answer is None:
                    return
                if answer and not save(silent=True):
                    return

            load_draft(draft_card_ids[target])

        def regenerate():
            current_card = ebay_get_card(card_id)
            if not current_card:
                return
            title_var.set(ebay_generate_title(current_card))
            desc.delete("1.0", "end")
            desc.insert(
                "1.0",
                ebay_generate_description(current_card, cond_var.get())
            )

        def copy_text(value):
            root.clipboard_clear()
            root.clipboard_append(value)
            root.update()

        prev_btn = ttk.Button(
            nav, text="◀ Vorheriger Entwurf",
            command=lambda: navigate(-1)
        )
        prev_btn.pack(side="left", padx=4)

        next_btn = ttk.Button(
            nav, text="Nächster Entwurf ▶",
            command=lambda: navigate(1)
        )
        next_btn.pack(side="left", padx=4)

        # Fixed action row below the editor content.
        actions = ttk.Frame(outer, padding=(0, 6))
        actions.grid(
            row=2, column=0, columnspan=2, sticky="ew", pady=(8, 0)
        )
        actions_right = ttk.Frame(actions)
        actions_right.pack(side="right")
        ttk.Button(
            actions_right, text="♻ Neu generieren", command=regenerate
        ).pack(side="left", padx=4)
        ttk.Button(
            actions_right, text="Titel kopieren",
            command=lambda: copy_text(title_var.get())
        ).pack(side="left", padx=4)
        ttk.Button(
            actions_right, text="Beschreibung kopieren",
            command=lambda: copy_text(desc.get("1.0", "end").strip())
        ).pack(side="left", padx=4)
        def create_sandbox_offer():
            if not save(silent=True):
                return
            try:
                result = ebay_sandbox_create_offer(
                    card_id,
                    title_var.get().strip(),
                    desc.get("1.0", "end").strip(),
                    cond_var.get().strip(),
                    float((price_var.get() or "0").replace(",", ".")),
                    fmt_var.get(),
                    cat_var.get().strip(),
                    sku_var.get().strip(),
                )
                offer_id = result["offer"]["offer_id"]
                status_var.set("Offer erstellt")
                refresh()
                if result.get("existing"):
                    msg = (
                        "Das eBay-Offer existierte bereits.\n\n"
                        f"Vorhandene Offer-ID: {offer_id}\n\n"
                        "Die Offer-ID wurde jetzt in DCardsLab übernommen.\n"
                        "Das Angebot wurde noch NICHT veröffentlicht."
                    )
                else:
                    msg = (
                        "Offer erfolgreich erstellt.\n\n"
                        f"Offer ID: {offer_id}\n\n"
                        "Das Angebot wurde noch NICHT veröffentlicht."
                    )
                messagebox.showinfo("eBay Sandbox", msg, parent=win)
            except Exception as exc:
                log_exception(f"eBay Sandbox Offer für Karte #{card_id} fehlgeschlagen", exc)
                messagebox.showerror("eBay Sandbox", str(exc), parent=win)

        sandbox_btn = ttk.Button(
            actions_right, text="🧪 eBay Sandbox: Offer erstellen",
            command=create_sandbox_offer
        )
        sandbox_btn.pack(side="left", padx=4)

        def refresh_sandbox_offer():
            try:
                c = db()
                row = c.execute("SELECT ebay_offer_id FROM ebay_listings WHERE card_id=?", (int(card_id),)).fetchone()
                c.close()
                offer_id = str(row[0] or "").strip() if row else ""
                if not offer_id:
                    messagebox.showinfo("eBay Sandbox", "Für diese Karte ist noch keine Offer-ID gespeichert.", parent=win)
                    return
                result = ebay_sandbox_get_offer(offer_id)
                offer = result.get("offer") or {}
                offer_status = str(offer.get("status") or offer.get("listingStatus") or "Offer vorhanden")
                c = db()
                try:
                    c.execute("UPDATE ebay_listings SET status=?, updated_at=? WHERE card_id=?",
                              ("Offer: " + offer_status, datetime.now().isoformat(timespec="seconds"), int(card_id)))
                    c.commit()
                finally:
                    c.close()
                status_var.set("Offer: " + offer_status)
                refresh()
                messagebox.showinfo(
                    "eBay Sandbox",
                    f"Offer-ID: {offer_id}\n\nStatus: {offer_status}",
                    parent=win,
                )
            except Exception as exc:
                log_exception(f"eBay Sandbox Offer für Karte #{card_id} konnte nicht aktualisiert werden", exc)
                messagebox.showerror("eBay Sandbox", str(exc), parent=win)

        ttk.Button(
            actions_right, text="↻ eBay Offer prüfen", command=refresh_sandbox_offer
        ).pack(side="left", padx=4)

        def publish_check():
            try:
                c = db()
                row = c.execute("SELECT ebay_offer_id FROM ebay_listings WHERE card_id=?", (int(card_id),)).fetchone()
                c.close()
                offer_id = str(row[0] or "").strip() if row else ""
                checks = ebay_publish_check(
                    card_id, title_var.get(), desc.get("1.0", "end"),
                    cond_var.get(), price_var.get(), fmt_var.get(),
                    cat_var.get(), sku_var.get(), offer_id
                )
                lines = []
                for ok, label, detail in checks:
                    prefix = "✓" if ok is True else ("!" if ok is None else "✗")
                    lines.append(f"{prefix} {label}: {detail}")
                ready = all(ok is not False for ok, _, _ in checks)
                lines.insert(0, "Bereit für Publish: JA" if ready else "Bereit für Publish: NEIN")
                messagebox.showinfo("eBay Publish-Check", "\n".join(lines), parent=win)
            except Exception as exc:
                log_exception(f"eBay Publish-Check für Karte #{card_id} fehlgeschlagen", exc)
                messagebox.showerror("eBay Publish-Check", str(exc), parent=win)

        ttk.Button(
            actions_right, text="✓ eBay Publish-Check", command=publish_check
        ).pack(side="left", padx=4)

        def publish_offer():
            try:
                c = db()
                row = c.execute(
                    "SELECT ebay_offer_id, ebay_listing_id FROM ebay_listings WHERE card_id=?",
                    (int(card_id),)
                ).fetchone()
                c.close()
                offer_id = str(row[0] or "").strip() if row else ""
                existing_listing_id = str(row[1] or "").strip() if row else ""
                if not offer_id:
                    messagebox.showwarning(
                        "eBay Publish",
                        "Für diese Karte ist noch keine Offer-ID vorhanden.\n\n"
                        "Bitte zuerst das Offer erstellen.",
                        parent=win,
                    )
                    return
                if existing_listing_id:
                    messagebox.showinfo(
                        "eBay Publish",
                        f"Diese Karte ist bereits veröffentlicht.\n\nListing-ID: {existing_listing_id}",
                        parent=win,
                    )
                    return

                checks = ebay_publish_check(
                    card_id, title_var.get(), desc.get("1.0", "end"),
                    cond_var.get(), price_var.get(), fmt_var.get(),
                    cat_var.get(), sku_var.get(), offer_id
                )
                failed = [(label, detail) for ok, label, detail in checks if ok is False]
                if failed:
                    lines = ["Das Angebot ist noch nicht bereit:", ""]
                    lines.extend(f"✗ {label}: {detail}" for label, detail in failed)
                    messagebox.showwarning("eBay Publish", "\n".join(lines), parent=win)
                    return

                answer = messagebox.askyesno(
                    "eBay Sandbox veröffentlichen",
                    "Das Angebot wird jetzt in der eBay SANDBOX veröffentlicht.\n\n"
                    f"Karte: {card_id}\nOffer-ID: {offer_id}\nPreis: {price_var.get()} €\n\n"
                    "Es wird KEIN echtes Produktivangebot erstellt.\n\nJetzt veröffentlichen?",
                    parent=win,
                )
                if not answer:
                    return

                result, listing_id = ebay_sandbox_publish_offer(offer_id)
                now = datetime.now().isoformat(timespec="seconds")
                c = db()
                try:
                    c.execute(
                        "UPDATE ebay_listings SET ebay_listing_id=?, ebay_item_id=?, status=?, updated_at=? WHERE card_id=?",
                        (listing_id, listing_id, "Eingestellt", now, int(card_id)),
                    )
                    c.commit()
                finally:
                    c.close()
                status_var.set("Eingestellt")
                refresh()
                messagebox.showinfo(
                    "eBay Sandbox",
                    "Angebot erfolgreich veröffentlicht.\n\n"
                    f"Offer-ID: {offer_id}\nListing-ID: {listing_id}\n\n"
                    "Das Angebot ist jetzt als aktives Sandbox-Listing vorhanden.",
                    parent=win,
                )
            except Exception as exc:
                log_exception(f"eBay Sandbox Publish für Karte #{card_id} fehlgeschlagen", exc)
                messagebox.showerror("eBay Publish", str(exc), parent=win)

        publish_btn = ttk.Button(
            actions_right, text="🚀 eBay Angebot veröffentlichen", command=publish_offer
        )
        publish_btn.pack(side="left", padx=4)

        export_btn = ttk.Button(
            actions_right, text="▶ Angebotsdatei erstellen",
            command=lambda: ebay_export_offer_from_template(root, template_key=template_key_state["value"])
        )
        export_btn.pack(side="left", padx=4)

        def update_export_button(*_):
            export_btn.configure(state="normal" if not validate_required_fields() else "disabled")

        for var in (title_var, cond_var, price_var, fmt_var, cat_var):
            var.trace_add("write", update_export_button)
        desc.bind("<KeyRelease>", update_export_button, add="+")

        ttk.Button(
            actions_right, text="💾 Entwurf speichern",
            command=lambda: save(silent=False)
        ).pack(side="left", padx=4)
        ttk.Button(
            actions_right, text="Schließen", command=win.destroy
        ).pack(side="left", padx=4)

        win.bind("<Alt-Left>", lambda e: navigate(-1))
        win.bind("<Alt-Right>", lambda e: navigate(1))

        load_draft(card_id)

    def select_card_for_ebay():
        """Open a proper card picker with live front/back preview."""
        picker = tk.Toplevel(root)
        picker.title("Karte für eBay auswählen")
        fit_dialog(picker, 1050, 700, min_width=850, min_height=540)
        picker.transient(root)

        outer = ttk.Frame(picker, padding=12)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.columnconfigure(1, weight=1)
        outer.rowconfigure(1, weight=1)

        ttk.Label(
            outer,
            text="Karte auswählen",
            font=("", 13, "bold")
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(0, 8))

        tree_frame = ttk.Frame(outer)
        tree_frame.grid(row=1, column=0, sticky="nsew", padx=(0, 10))
        tree_frame.rowconfigure(0, weight=1)
        tree_frame.columnconfigure(0, weight=1)

        picker_tree = ttk.Treeview(
            tree_frame,
            columns=("ID", "Karte", "Kategorie", "Set"),
            show="headings",
            selectmode="browse"
        )
        for col, width in (
            ("ID", 60), ("Karte", 260), ("Kategorie", 120), ("Set", 180)
        ):
            picker_tree.heading(col, text=col)
            picker_tree.column(col, width=width, minwidth=60)
        picker_tree.grid(row=0, column=0, sticky="nsew")
        sy = ttk.Scrollbar(tree_frame, orient="vertical", command=picker_tree.yview)
        sy.grid(row=0, column=1, sticky="ns")
        picker_tree.configure(yscrollcommand=sy.set)

        c = db()
        rows = c.execute(
            """SELECT card_id,title,category,set_name
               FROM cards ORDER BY card_id ASC"""
        ).fetchall()
        c.close()
        for row in rows:
            picker_tree.insert("", "end", values=row)

        preview_frame = ttk.LabelFrame(outer, text="Vorschau", padding=10)
        preview_frame.grid(row=1, column=1, sticky="nsew")
        preview_frame.columnconfigure(0, weight=1)
        preview_frame.columnconfigure(1, weight=1)

        preview_refs = {"photos": []}

        def update_picker_preview(event=None):
            sel = picker_tree.selection()
            if not sel:
                return
            card_id = int(picker_tree.item(sel[0], "values")[0])
            card = ebay_get_card(card_id)
            if not card:
                return

            for child in preview_frame.winfo_children():
                child.destroy()
            preview_refs["photos"].clear()

            for col, (label, ref) in enumerate((
                ("Vorderseite", card.get("front_image", "")),
                ("Rückseite", card.get("back_image", ""))
            )):
                box = ttk.Frame(preview_frame)
                box.grid(row=0, column=col, sticky="nsew", padx=5)
                ttk.Label(box, text=label, font=("", 10, "bold")).pack()

                image_label = ttk.Label(box, text="Kein Bild", anchor="center")
                image_label.pack(fill="both", expand=True, pady=8)

                path = image_path_from_ref(ref)
                if path and Image is not None and ImageTk is not None:
                    try:
                        img = _load_pil_preview(path, (240, 360))
                        photo = ImageTk.PhotoImage(img)
                        image_label.configure(image=photo, text="")
                        image_label.image = photo
                        preview_refs["photos"].append(photo)
                    except Exception:
                        image_label.configure(text="Vorschau nicht möglich")

                ttk.Label(
                    box,
                    text=ref or "Kein Bild hinterlegt",
                    wraplength=240
                ).pack()

            ttk.Label(
                preview_frame,
                text=f"Karte #{card_id} • {card.get('title') or 'Unbenannt'}",
                wraplength=500
            ).grid(row=1, column=0, columnspan=2, sticky="w", pady=(10, 0))

        picker_tree.bind("<<TreeviewSelect>>", update_picker_preview)
        picker_tree.bind("<Double-1>", lambda e: choose())

        actions = ttk.Frame(outer)
        actions.grid(row=2, column=0, columnspan=2, sticky="ew", pady=(10, 0))
        actions_right = ttk.Frame(actions)
        actions_right.pack(side="right")

        def choose():
            sel = picker_tree.selection()
            if not sel:
                messagebox.showinfo(
                    "eBay", "Bitte zuerst eine Karte auswählen.", parent=picker
                )
                return
            card_id = int(picker_tree.item(sel[0], "values")[0])
            picker.destroy()
            open_ebay_editor(card_id)

        ttk.Button(actions_right, text="Abbrechen", command=picker.destroy).pack(
            side="left", padx=4
        )
        ttk.Button(actions_right, text="Karte übernehmen", command=choose).pack(
            side="left", padx=4
        )

    ebay_top_actions = ttk.Frame(ebay_top)
    ebay_top_actions.pack(side="right")

    ttk.Button(
        ebay_top_actions,
        text="＋ Karte auswählen / Entwurf erstellen",
        command=select_card_for_ebay
    ).pack(side="top", anchor="e", pady=(0, 4))

    def delete_selected_ebay():
        sel = ebay_tree.selection()
        if not sel:
            messagebox.showinfo(
                "eBay",
                "Bitte zuerst einen eBay-Datensatz auswählen.",
                parent=root
            )
            return
        try:
            listing_id = int(ebay_tree.item(sel[0], "values")[0])
        except (TypeError, ValueError, IndexError):
            messagebox.showerror(
                "eBay",
                "Der ausgewählte eBay-Datensatz konnte nicht ermittelt werden.",
                parent=root
            )
            return
        confirm_and_delete(
            root, "ebay", listing_id, on_deleted=refresh
        )

    ttk.Button(
        ebay_top_actions,
        text="🗑 eBay-Datensatz löschen",
        command=delete_selected_ebay
    ).pack(side="top", anchor="e")

    ebay_tree = make_tree(
        ebay_tab,
        ["ID", "Karten-ID", "Karte", "eBay-Titel", "Beschreibung", "Zustand",
         "Preis", "Format", "Status", "Offer-ID"],
        [60, 80, 220, 360, 420, 90, 90, 100, 120, 150]
    )
    ebay_tree.bind("<Double-1>", lambda e: open_ebay_editor())

    ebay_actions = ttk.Frame(ebay_tab)
    ebay_actions.pack(fill="x", pady=(8, 0))

    ttk.Button(
        ebay_actions,
        text="✎ Entwurf bearbeiten",
        command=lambda: open_ebay_editor()
    ).pack(side="left")

    ttk.Button(
        ebay_actions,
        text="＋ Neue Auswahl",
        command=select_card_for_ebay
    ).pack(side="left", padx=(8, 0))

    ttk.Button(
        ebay_actions,
        text="⬇ eBay-Daten + Bilder exportieren",
        command=lambda: ebay_export_bundle(root)
    ).pack(side="left", padx=(8, 0))

    ttk.Button(
        ebay_actions,
        text="⇩ eBay-Importdatei erstellen",
        command=lambda: ebay_export_from_template(root)
    ).pack(side="left", padx=(8, 0))

    ttk.Button(
        ebay_actions,
        text="▶ Angebotsdatei (Action=Add)",
        command=lambda: ebay_export_offer_from_template(root)
    ).pack(side="left", padx=(8, 0))

    ttk.Label(
        ebay_actions,
        text="Doppelklick auf einen Entwurf öffnet ihn erneut."
    ).pack(side="left", padx=12)

    ok_ocr, msg_ocr = ocr_setup_status()
    ocr_info.set(msg_ocr)
    refresh()

    card_tree.bind(
        "<Delete>",
        lambda event: (
            confirm_and_delete(
                root, "card",
                int(card_tree.item(card_tree.selection()[0], "values")[0]),
                on_deleted=refresh
            ) if card_tree.selection() else None
        )
    )
    inv_tree.bind(
        "<Delete>",
        lambda event: (
            confirm_and_delete(
                root, "inventory",
                int(inv_tree.item(inv_tree.selection()[0], "values")[0]),
                on_deleted=refresh
            ) if inv_tree.selection() else None
        )
    )
    buy_tree.bind(
        "<Delete>",
        lambda event: (
            confirm_and_delete(
                root, "purchase",
                int(buy_tree.item(buy_tree.selection()[0], "values")[0]),
                on_deleted=refresh
            ) if buy_tree.selection() else None
        )
    )

    bottom_actions = ttk.Frame(root)
    bottom_actions.pack(fill="x", padx=12, pady=(0, 8))

    ttk.Button(bottom_actions,text="⟳ Alle Tabellen aktualisieren",
               command=refresh).pack(side="left")
    ttk.Button(bottom_actions,text="☁ Google Sheets synchronisieren",
               command=sync_google_sheets).pack(side="left",padx=(8,0))
    ttk.Button(bottom_actions,text="☁ Google Drive einrichten",
               command=lambda: google_drive_setup(root)).pack(side="left",padx=(8,0))
    ttk.Button(bottom_actions,text="☁ Backup zu Google Drive",
               command=lambda: google_drive_backup_now(root, interactive=True, reason="manuell")).pack(side="left",padx=(8,0))
    ttk.Button(bottom_actions,text="💾 Backup erstellen",
               command=do_project_backup).pack(side="left",padx=(8,0))
    ttk.Button(bottom_actions,text="♻ Backup wiederherstellen",
               command=do_restore).pack(side="left",padx=(8,0))
    ttk.Button(bottom_actions,text="🖼 Bilder prüfen",
               command=do_image_check).pack(side="left",padx=(8,0))
    ttk.Button(bottom_actions,text="📋 Fehlerprotokoll öffnen",
               command=lambda: open_log_file(root)).pack(side="left",padx=(8,0))

    def on_close():
        # Best-effort Abschluss-Backup. Fehler werden geloggt, der Programmabschluss
        # wird nicht blockiert, falls Google Drive offline/nicht autorisiert ist.
        try:
            google_drive_backup_now(parent=root, interactive=False, reason="Programmende")
        except Exception as exc:
            log_exception("Abschluss-Backup unerwartet fehlgeschlagen", exc)
        root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()

if __name__ == "__main__":
    try:
        db().close()
        main()
    except Exception as exc:
        log_exception("Unbehandelter Programmfehler", exc)
        try:
            messagebox.showerror(
                "DCardLabs – Programmfehler",
                f"Ein unerwarteter Fehler ist aufgetreten.\n\n"
                f"Details wurden in die Logdatei geschrieben:\n{LOG_FILE}\n\n{exc}"
            )
        except Exception:
            pass
        raise
