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
import io
import os
from pathlib import Path

_MAX_EDGE = 1568  # Anthropic's documented vision "sweet spot" - larger images
                   # are downscaled server-side anyway before the model sees
                   # them, so resizing client-side only cuts upload time and
                   # payload size, it does not lose recognition quality.

EMPTY_FIELDS = {
    "title": "", "category": "", "theme": "", "manufacturer": "",
    "set_name": "", "season_year": "", "card_type": "", "variant": "",
    "language": "", "team": "", "position": "", "squad_number": "",
    "club_debut_season": "", "card_number": "", "serial_number": "", "print_run": "",
    # Weitere optionale eBay-Item-Specifics (siehe db.CARD_FIELDS) - werden
    # nur befuellt, wenn wirklich auf der Karte lesbar (siehe PROMPT).
    "manufacturing_year": "", "origin_country": "", "material": "",
    "card_size": "", "card_stock": "", "edition": "", "special_features": "",
    "product_type": "", "insert_set": "", "vintage": "", "reprint_status": "",
    "custom_made": "", "autograph_authentication": "", "signed_by": "",
    "autograph_type": "", "autograph_auth_number": "", "tv_series": "",
    "movie": "", "genre": "", "illustrator": "", "age_recommendation": "",
    "is_numbered": 0, "is_rookie": 0, "is_autograph": 0,
    "confidence": 0, "raw": "",
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
    "theme = Liga/Wettbewerb/Serie, NUR wenn deren Name selbst irgendwo auf "
    "der Karte gedruckt steht (z.B. \"Bundesliga\", \"Ligue 1\", \"UEFA "
    "Champions League\"). Wiederhole hier NIEMALS den Team-/Vereinsnamen - "
    "wenn keine separate Liga-/Wettbewerbsbezeichnung zu sehen ist, lass "
    "theme leer, auch wenn du die Liga aus dem Verein erschliessen "
    "könntest.\n"
    "manufacturer = Kartenhersteller (z.B. Topps, Panini).\n"
    "set_name = Set-/Serienname.\n"
    "season_year = Saison/Jahr, wenn irgendwo auf der Karte (meist "
    "Rückseite, oft klein) ein Jahr oder eine Saison im Format wie "
    "\"2024\", \"2024/25\" oder \"24/25\" zu sehen ist - auch ohne "
    "vorangestelltes Wort wie \"Saison\". Nimm NICHT ein Copyright-/"
    "Herausgabejahr des Herstellers (oft bei ©, meist im allerkleinsten "
    "Fließtext zusammen mit Firmennamen/Lizenzhinweisen).\n"
    "card_type = Parallel-/Variantenbezeichnung, so wie sie auf der Karte "
    "steht (z.B. \"Gold\", \"Silver\", \"Base\", \"Insert\", \"Refractor\", "
    "\"Prizm\"). Ist nur EIN solcher Begriff auf der Karte zu sehen, trage "
    "ihn HIER ein, nicht in variant.\n"
    "variant = ein ZWEITER, davon verschiedener Zusatzbegriff, NUR falls er "
    "zusaetzlich zu card_type auf derselben Karte steht (z.B. card_type= "
    "\"Prizm\", variant=\"Blue\" fuer eine \"Blue Prizm\"-Parallele). Steht "
    "nur ein einzelner Begriff auf der Karte, lass variant leer statt "
    "denselben Begriff wie card_type einzutragen.\n"
    "language = die Sprache des gedruckten Karten-/Fliesstexts (z.B. "
    "\"Deutsch\", \"Englisch\", \"Japanisch\"), NUR falls sich das aus dem "
    "Text auf der Karte eindeutig ablesen laesst - bei reinen Zahlen/Namen "
    "ohne erkennbaren Fliesstext leer lassen statt zu raten.\n"
    "team = Team/Verein, so wie er auf der Karte steht (Vereinsname, "
    "Vereinslogo-Beschriftung oder Vereinswappen-Text) - unabhängig davon, "
    "ob eine Liga (theme) erkennbar ist oder nicht.\n"
    "position = Spielposition, NUR falls explizit als \"Position\" "
    "beschriftet.\n"
    "squad_number = Trikotnummer, NUR falls explizit als \"Squad Number\" "
    "o.ä. beschriftet.\n"
    "club_debut_season = NUR falls explizit als \"Club Debut Season\" "
    "beschriftet.\n"
    "card_number = Kartennummer, wenn irgendwo auf der Karte eine Nummer "
    "erkennbar als Kartennummer beschriftet ist - egal ob mit \"Card No.\", "
    "\"Card Number\", \"Nr.\", \"No.\" oder \"#\" davor, oder als eigenständige "
    "kleine Zahl/Zahlenkombination (z.B. \"12\", \"BL-12\") an einer für "
    "Kartennummern typischen Stelle (meist Rückseite, Ecke oder Rand). Nur "
    "leer lassen, wenn wirklich keine erkennbare Kartennummer vorhanden "
    "ist.\n"
    "serial_number und print_run = bei nummerierten/limitierten Karten die "
    "zwei Zahlen aus einem Bruch wie 123/199 (serial_number=123, "
    "print_run=199).\n"
    "Die folgenden Felder NUR ausfuellen, wenn explizit und eindeutig so "
    "auf der Karte beschriftet - bei jedem Zweifel leer lassen statt zu "
    "raten (die meisten Karten werden fuer die meisten dieser Felder leer "
    "bleiben, das ist normal):\n"
    "manufacturing_year = Herstellungsjahr, falls separat vom "
    "Saison-/Erscheinungsjahr (season_year) angegeben.\n"
    "origin_country = Herstellungsland (z.B. \"Made in USA\").\n"
    "material = Kartenmaterial (z.B. \"Karton\", \"Kunststoff\", \"Metall\").\n"
    "card_size = Kartengroesse/-format, falls vom Standardformat abweichend "
    "angegeben.\n"
    "card_stock = Kartenstaerke in Punkt (pt), falls angegeben.\n"
    "edition = Editionsbezeichnung (z.B. \"1st Edition\").\n"
    "special_features = sonstige explizit aufgedruckte Besonderheiten.\n"
    "product_type = Produktart, falls von der Standard-Sammelkarte "
    "abweichend beschriftet.\n"
    "insert_set = Name des Insert-/Themen-Subsets, falls angegeben.\n"
    "vintage = Vintage-Einordnung, falls angegeben.\n"
    "reprint_status = \"Original\" oder \"Lizenzierter Nachdruck\", falls "
    "explizit angegeben.\n"
    "custom_made = \"Ja\", falls die Karte explizit als maßgefertigt "
    "gekennzeichnet ist.\n"
    "autograph_authentication = Authentifizierungsstelle des Autogramms "
    "(z.B. \"PSA/DNA\"), nur bei erkennbarem Autogramm relevant.\n"
    "signed_by = Name der signierenden Person, falls explizit angegeben "
    "und vom Titel/Charakter abweichend.\n"
    "autograph_type = Art des Autogramms (z.B. \"Hard Signed\", \"Sticker "
    "Auto\"), nur bei erkennbarem Autogramm relevant.\n"
    "autograph_auth_number = Zertifizierungsnummer des Autogramms, falls "
    "aufgedruckt.\n"
    "tv_series = TV-Serie, der die Karte entstammt (nur bei Non-Sport-"
    "Karten relevant, z.B. Serien-Merchandise).\n"
    "movie = Film, dem die Karte entstammt (nur bei Non-Sport-Karten "
    "relevant).\n"
    "genre = Genre-Einordnung (nur bei Non-Sport-Karten relevant).\n"
    "illustrator = Zeichner/Illustrator der Karte, falls namentlich "
    "genannt (nur bei Non-Sport-Karten relevant).\n"
    "age_recommendation = Altersempfehlung, falls aufgedruckt (nur bei "
    "Non-Sport-Karten relevant).\n"
    "is_rookie = true, NUR wenn auf der Karte explizit \"RC\", \"Rookie "
    "Card\" oder \"Rookie\" aufgedruckt ist - nicht aus Season/Debütjahr "
    "erschliessen.\n"
    "is_autograph = true, NUR wenn auf der Karte explizit \"AUTO\", "
    "\"Autograph\" o.ä. aufgedruckt ist ODER eine echte, handschriftliche "
    "Unterschrift auf der Karte selbst zu sehen ist (nicht nur ein "
    "gedrucktes Foto/Logo).\n"
    "confidence = deine ehrliche Einschätzung (0-100), wie sicher du dir "
    "beim Namen (title) insgesamt bist.\n\n"
    "Antworte AUSSCHLIESSLICH mit einem einzigen JSON-Objekt, ohne "
    "Markdown-Codeblock und ohne weiteren Text davor oder danach, mit "
    "genau diesen Schlüsseln (alles Strings ausser confidence, das eine "
    "Zahl ist, sowie is_rookie/is_autograph, die jeweils true/false sind; "
    "unbekannte Textfelder als leerer String \"\"):\n"
    '{{"title": "", "category": "", "theme": "", "manufacturer": "", '
    '"set_name": "", "season_year": "", "card_type": "", "variant": "", '
    '"language": "", "team": "", "position": "", "squad_number": "", '
    '"club_debut_season": "", "card_number": "", "serial_number": "", '
    '"print_run": "", "manufacturing_year": "", "origin_country": "", '
    '"material": "", "card_size": "", "card_stock": "", "edition": "", '
    '"special_features": "", "product_type": "", "insert_set": "", '
    '"vintage": "", "reprint_status": "", "custom_made": "", '
    '"autograph_authentication": "", "signed_by": "", "autograph_type": "", '
    '"autograph_auth_number": "", "tv_series": "", "movie": "", "genre": "", '
    '"illustrator": "", "age_recommendation": "", "is_rookie": false, '
    '"is_autograph": false, "confidence": 0}}'
)


def _extract_json_object(text):
    """Claude sometimes wraps JSON in a ```json code fence despite being
    told not to; strip that, then take the outermost {...} span."""
    text = text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if text.startswith("json"):
            text = text[4:]
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("Keine JSON-Antwort gefunden.")
    return text[start:end + 1]


def _image_block(path):
    path = Path(path)
    media_type = _MEDIA_TYPES.get(path.suffix.lower(), "image/jpeg")

    # Full-resolution scanner output (often several thousand px / multiple MB)
    # takes noticeably longer to upload than a card photo needs to be legible.
    # Resize when Pillow is available (it already is - used elsewhere in the
    # app for thumbnails); fall back to sending the original bytes untouched
    # if Pillow is missing or the image can't be decoded, so this never turns
    # a working scan into a failing one.
    try:
        from PIL import Image
        img = Image.open(path)
        img = img.convert("RGB")
        if max(img.size) > _MAX_EDGE:
            img.thumbnail((_MAX_EDGE, _MAX_EDGE), Image.LANCZOS)
        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=85)
        data = base64.standard_b64encode(buf.getvalue()).decode("utf-8")
        return {
            "type": "image",
            "source": {"type": "base64", "media_type": "image/jpeg", "data": data},
        }
    except Exception:
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
    import json
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
        language: str = ""
        team: str = ""
        position: str = ""
        squad_number: str = ""
        club_debut_season: str = ""
        card_number: str = ""
        serial_number: str = ""
        print_run: str = ""
        manufacturing_year: str = ""
        origin_country: str = ""
        material: str = ""
        card_size: str = ""
        card_stock: str = ""
        edition: str = ""
        special_features: str = ""
        product_type: str = ""
        insert_set: str = ""
        vintage: str = ""
        reprint_status: str = ""
        custom_made: str = ""
        autograph_authentication: str = ""
        signed_by: str = ""
        autograph_type: str = ""
        autograph_auth_number: str = ""
        tv_series: str = ""
        movie: str = ""
        genre: str = ""
        illustrator: str = ""
        age_recommendation: str = ""
        is_rookie: bool = False
        is_autograph: bool = False
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
        # a stalled connection looking hung for far longer than a user would
        # ever wait, instead of failing this one card gracefully.
        client = anthropic.Anthropic(timeout=90.0)
        # Plain messages.create() + manual JSON parsing instead of
        # messages.parse(output_format=...): the structured-output schema
        # compiler rejected this model's schema with a 400 "Schema is too
        # complex" even though it's flat (16 string/number fields, no
        # nesting) - the limit isn't documented, so the reliable fix is to
        # not depend on server-side schema compilation at all. The prompt
        # asks for a raw JSON object instead; Pydantic still validates and
        # fills defaults for whatever comes back.
        response = client.messages.create(
            # Sonnet, not Opus: this is a straightforward "read the printed
            # text off a photo" extraction, not a task that needs Opus-level
            # reasoning - Sonnet is noticeably faster and cheaper per card
            # with no meaningful accuracy loss for this.
            model="claude-sonnet-5",
            max_tokens=1024,
            messages=[{"role": "user", "content": content}],
        )
        text = next(b.text for b in response.content if b.type == "text")
        parsed = CardRecognition.model_validate(json.loads(_extract_json_object(text)))
    except Exception as exc:
        return dict(EMPTY_FIELDS, status=f"KI-Erkennung fehlgeschlagen: {exc}")

    data = parsed.model_dump()
    data["is_numbered"] = 1 if data.get("serial_number") and data.get("print_run") else 0
    data["is_rookie"] = 1 if data.get("is_rookie") else 0
    data["is_autograph"] = 1 if data.get("is_autograph") else 0
    data["raw"] = ""
    if data.get("confidence", 0) >= 65:
        data["status"] = "ok"
    elif data.get("title"):
        data["status"] = "prüfen"
    else:
        data["status"] = "nicht erkannt"
    return data
