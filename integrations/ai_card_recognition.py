"""Trading-card recognition via Claude vision, replacing the old
fixed-pixel-region Tesseract OCR (scanner_v0_8_dynamic crops the 9-up scan
into individual card images; this module only reads the resulting card
photos, it never touches the crop/detection step).

Fixed pixel regions only work for the exact layout they were measured on
(one manufacturer/set). Sending the photo to a vision-capable model reads
whatever is actually printed on the card, regardless of layout, so new
sets/manufacturers work without hand-measuring new regions.

Requires the `anthropic` and `pydantic` packages and an ANTHROPIC_API_KEY
environment variable. Needs internet access at scan time. On any failure
(missing key, no internet, API error) recognize_card() returns an
all-empty result with a status message - it never raises - so a scanning
session still completes and cards can be filled in manually.
"""
import base64
import os
from pathlib import Path

EMPTY_FIELDS = {
    "title": "", "category": "", "theme": "", "manufacturer": "",
    "set_name": "", "season_year": "", "card_type": "", "variant": "",
    "team": "", "position": "", "squad_number": "", "club_debut_season": "",
    "card_number": "", "serial_number": "", "print_run": "",
    "is_numbered": 0, "confidence": 0, "raw": "",
}

_MEDIA_TYPES = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
    ".png": "image/png", ".webp": "image/webp",
}

PROMPT = (
    "Das sind Foto(s) einer einzelnen, bereits zugeschnittenen physischen "
    "Sammelkarte ({sides}). Das Kartenlayout kann je nach Hersteller/Set "
    "stark variieren (Sportkarten, Trading Cards, beliebiges Franchise) - "
    "verlasse dich nicht auf ein bestimmtes Layout, sondern lies ab, was "
    "tatsächlich auf der Karte steht. Fülle NUR Felder aus, die auf der "
    "Karte wirklich lesbar sind; lass alles andere als leeren String statt "
    "zu raten.\n\n"
    "title = Name der abgebildeten Person/des Charakters.\n"
    "category = Sportart bzw. übergeordnete Kategorie (z.B. Fußball, "
    "Basketball, Baseball, Pokémon, Marvel).\n"
    "theme = Liga/Franchise/Serie (z.B. Bundesliga).\n"
    "manufacturer = Kartenhersteller (z.B. Topps, Panini).\n"
    "set_name = Set-/Serienname.\n"
    "season_year = explizit angegebene Saison/Jahr (z.B. 2024/25) - NICHT "
    "ein Copyright-Jahr im Kleingedruckten.\n"
    "card_type = Parallel-/Variantenbezeichnung (z.B. Gold, Silver, Base, "
    "Insert).\n"
    "variant = weitere Variantenangabe, falls vorhanden und von card_type "
    "verschieden.\n"
    "team = Team/Verein.\n"
    "position = Spielposition, NUR falls explizit als \"Position\" "
    "beschriftet.\n"
    "squad_number = Trikotnummer, NUR falls explizit als \"Squad Number\" "
    "o.ä. beschriftet.\n"
    "club_debut_season = NUR falls explizit als \"Club Debut Season\" "
    "beschriftet.\n"
    "card_number = Kartennummer, NUR falls explizit als \"Card No.\"/"
    "\"Card Number\" beschriftet.\n"
    "serial_number und print_run = bei nummerierten/limitierten Karten die "
    "zwei Zahlen aus einem Bruch wie 123/199 (serial_number=123, "
    "print_run=199).\n"
    "confidence = deine ehrliche Einschätzung (0-100), wie sicher du dir "
    "beim Namen (title) insgesamt bist."
)


def _image_block(path):
    path = Path(path)
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")
    data = base64.standard_b64encode(path.read_bytes()).decode("utf-8")
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


def ai_recognition_status():
    """Mirrors ocr_setup_status()'s (ok, message) shape for the old
    Tesseract path, so callers can show a similar readiness check."""
    try:
        import anthropic  # noqa: F401
        import pydantic  # noqa: F401
    except ImportError as exc:
        return False, f"Python-Modul fehlt: {exc}"
    if not os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return False, "ANTHROPIC_API_KEY ist nicht gesetzt."
    return True, "KI-Kartenerkennung bereit."


def recognize_card(front_path=None, back_path=None):
    """Recognize a card from its front and/or back photo. Returns a dict
    shaped like the fields pair_and_ocr() needs, plus "status" (one of
    "ok", "prüfen", "nicht erkannt", "deaktiviert", or an error message).
    Never raises - any failure comes back as an empty result + status."""
    if not front_path and not back_path:
        return dict(EMPTY_FIELDS, status="deaktiviert")

    ok, message = ai_recognition_status()
    if not ok:
        return dict(EMPTY_FIELDS, status=f"KI-Erkennung nicht verfügbar: {message}")

    import anthropic
    from pydantic import BaseModel

    class CardRecognition(BaseModel):
        title: str = ""
        category: str = ""
        theme: str = ""
        manufacturer: str = ""
        set_name: str = ""
        season_year: str = ""
        card_type: str = ""
        variant: str = ""
        team: str = ""
        position: str = ""
        squad_number: str = ""
        club_debut_season: str = ""
        card_number: str = ""
        serial_number: str = ""
        print_run: str = ""
        confidence: float = 0

    sides = []
    content = []
    if front_path:
        content.append(_image_block(front_path))
        sides.append("Vorderseite")
    if back_path:
        content.append(_image_block(back_path))
        sides.append("Rückseite")
    content.append({"type": "text", "text": PROMPT.format(sides=" und ".join(sides))})

    try:
        # Explicit bounded timeout: the SDK default (10 minutes) would leave
        # the caller (a synchronous, single-threaded scan loop over up to 9
        # cards) looking hung for a very long time on a stalled connection
        # instead of failing this one card gracefully.
        client = anthropic.Anthropic(timeout=60.0)
        response = client.messages.parse(
            model="claude-opus-5",
            max_tokens=2048,
            messages=[{"role": "user", "content": content}],
            output_format=CardRecognition,
        )
    except Exception as exc:
        return dict(EMPTY_FIELDS, status=f"KI-Erkennung fehlgeschlagen: {exc}")

    data = response.parsed_output.model_dump()
    data["is_numbered"] = 1 if data.get("serial_number") and data.get("print_run") else 0
    data["raw"] = ""
    if data.get("confidence", 0) >= 65:
        data["status"] = "ok"
    elif data.get("title"):
        data["status"] = "prüfen"
    else:
        data["status"] = "nicht erkannt"
    return data
