
from pathlib import Path
import json

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

GOOGLE_PACKAGES = {
    "google-api-python-client": "googleapiclient",
    "google-auth-httplib2": "google_auth_httplib2",
    "google-auth-oauthlib": "google_auth_oauthlib",
}


def ensure_google_packages():
    """Return missing Google packages without changing the system."""
    missing = []
    import importlib.util
    for package, module in GOOGLE_PACKAGES.items():
        if importlib.util.find_spec(module) is None:
            missing.append(package)
    return missing


def install_google_packages():
    """Install Google Sheets dependencies into the active Python environment."""
    import subprocess
    import sys

    missing = ensure_google_packages()
    if not missing:
        return []

    cmd = [sys.executable, "-m", "pip", "install", *missing]
    subprocess.check_call(cmd)
    return ensure_google_packages()



def _config_dir():
    """Persistent per-user Google configuration, independent of DCardLabs version folder."""
    import os
    root = Path(os.environ.get("APPDATA") or (Path.home() / ".config")) / "DCardLabs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _migrate_legacy_files(base):
    """Migrate existing project-local Google files once into persistent config."""
    import shutil
    base = Path(base)
    user = _config_dir()
    for name in ("credentials.json", "token.json", "google_sheets_config.json"):
        src = base / name
        dst = user / name
        if src.exists() and not dst.exists():
            shutil.copy2(src, dst)
    return user


def _paths(base):
    base = Path(base)
    user = _config_dir()
    return user / "google_sheets_config.json", user / "credentials.json", user / "token.json"


def load_config(base):
    _migrate_legacy_files(base)
    config_path, _, _ = _paths(Path(base))
    if not config_path.exists():
        return {}
    try:
        return json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_config(base, spreadsheet_id):
    _migrate_legacy_files(base)
    config_path, _, _ = _paths(Path(base))
    config_path.write_text(
        json.dumps({"spreadsheet_id": spreadsheet_id}, indent=2),
        encoding="utf-8"
    )


def sync_sqlite_to_sheets(base, db_path, spreadsheet_id=None):
    """
    One-way sync: SQLite is the master, Google Sheets is the reporting/editing view.
    Creates/overwrites four tabs: Karten, Inventar, Käufe, Sync_Info.
    """
    base = Path(base)
    config = load_config(base)
    spreadsheet_id = spreadsheet_id or config.get("spreadsheet_id")
    if not spreadsheet_id:
        raise ValueError("Keine Google-Sheets-ID hinterlegt.")

    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
    except ImportError as exc:
        raise RuntimeError(
            "Google-Bibliotheken fehlen. Bitte installiere die Pakete aus requirements.txt."
        ) from exc

    _, credentials_path, token_path = _paths(base)
    if not credentials_path.exists():
        raise FileNotFoundError(
            "credentials.json fehlt. Siehe GOOGLE_SHEETS_SETUP.txt."
        )

    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), SCOPES
            )
            creds = flow.run_local_server(port=0)
        token_path.write_text(creds.to_json(), encoding="utf-8")

    service = build("sheets", "v4", credentials=creds)
    sheets = service.spreadsheets()

    # Ensure the requested tabs exist.
    meta = sheets.get(spreadsheetId=spreadsheet_id).execute()
    existing = {s["properties"]["title"] for s in meta.get("sheets", [])}
    requests = []
    for title in ("Karten", "Inventar", "Käufe", "Sync_Info"):
        if title not in existing:
            requests.append({"addSheet": {"properties": {"title": title}}})
    if requests:
        sheets.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": requests}
        ).execute()

    import sqlite3
    c = sqlite3.connect(db_path)
    c.row_factory = sqlite3.Row

    cards_cols = [
        "ID", "Kategorie", "Thema / Franchise", "Team", "Hersteller",
        "Set / Serie", "Name / Titel", "Saison", "Kartennr.", "Kartentyp",
        "Variante", "Numbered", "Seriennr.", "Print Run", "Sprache",
        "OCR Status", "OCR Konfidenz", "OCR Team", "OCR Liga", "OCR Set",
        "OCR Typ", "OCR Nr.", "OCR Serial", "OCR Print Run", "OCR Variante",
        "Vorderseite", "Rückseite"
    ]
    cards_sql = """
        SELECT card_id, category, theme, team, manufacturer, set_name, title,
               season_year, card_number, card_type, variant, is_numbered,
               serial_number, print_run, language, ocr_status, ocr_confidence,
               ocr_team, ocr_league, ocr_set, ocr_card_type, ocr_card_number,
               ocr_serial_number, ocr_print_run, ocr_variant,
               front_image, back_image
        FROM cards ORDER BY card_id ASC
    """
    cards = [list(r) for r in c.execute(cards_sql)]

    inv_cols = ["Inventar-ID", "Karten-ID", "Karte", "Menge", "Zustand", "Lagerort", "Notizen"]
    inv = [list(r) for r in c.execute("""
        SELECT i.inventory_id, i.card_id, c.title, i.quantity,
               i.condition, i.location, i.notes
        FROM inventory i
        JOIN cards c ON c.card_id=i.card_id
        ORDER BY i.inventory_id ASC
    """)]

    buy_cols = ["Kauf-ID", "Kaufdatum", "Plattform", "Verkäufer", "Karten",
                "Kaufpreis", "Versand", "Gesamtpreis", "Notizen"]
    buys = [list(r) for r in c.execute("""
        SELECT purchase_id, purchase_date, platform, seller, card_count,
               purchase_price, shipping, total_price, notes
        FROM purchases ORDER BY purchase_id ASC
    """)]
    c.close()

    def write_tab(title, headers, rows):
        values = [headers] + rows
        sheets.values().clear(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A:ZZ"
        ).execute()
        sheets.values().update(
            spreadsheetId=spreadsheet_id,
            range=f"'{title}'!A1",
            valueInputOption="USER_ENTERED",
            body={"values": values}
        ).execute()
        # Freeze header row.
        meta2 = sheets.get(spreadsheetId=spreadsheet_id).execute()
        sheet_id = next(
            s["properties"]["sheetId"]
            for s in meta2["sheets"]
            if s["properties"]["title"] == title
        )
        sheets.batchUpdate(
            spreadsheetId=spreadsheet_id,
            body={"requests": [{
                "updateSheetProperties": {
                    "properties": {
                        "sheetId": sheet_id,
                        "gridProperties": {"frozenRowCount": 1}
                    },
                    "fields": "gridProperties.frozenRowCount"
                }
            }]}
        ).execute()

    write_tab("Karten", cards_cols, cards)
    write_tab("Inventar", inv_cols, inv)
    write_tab("Käufe", buy_cols, buys)
    write_tab(
        "Sync_Info",
        ["Information", "Wert"],
        [
            ["Quelle", "DCardLabs SQLite"],
            ["Synchronisiert", __import__("datetime").datetime.now().isoformat(timespec="seconds")],
            ["Richtung", "SQLite → Google Sheets"],
            ["Hinweis", "SQLite ist die Master-Datenbank; Sheets ist die externe Arbeits-/Auswertungsansicht."]
        ]
    )
    save_config(base, spreadsheet_id)
    return spreadsheet_id
