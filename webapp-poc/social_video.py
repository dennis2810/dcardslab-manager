"""Pillow + ffmpeg Reel-Generator fuer die Social-Media-Seite - rendert je
ausgewaehlter Karte ein Bild mit Titel-/Preis-Overlay (optional Team/Set/
Zustand-Zeile, Badges wie Rookie/Auto/Numbered/Refractor und die Rueckseite
als zweites Bild) und haengt die Bilder per ffmpeg (zoompan-Ken-Burns-Effekt
je Karten-Bild, abwechselnd rein-/rauszoomend, dann concat) zu einem stummen,
vertikalen (9:16) MP4 aneinander - optional umrahmt von einer Intro- und
einer Outro-Textkarte (z.B. "Jetzt auf eBay"). Intro-/Outro-Textkarten
bewusst OHNE Zoom (siehe _render_segment(static=True)): zoompan zoomt ohne
eigene x/y-Angabe von der linken oberen Ecke aus, wodurch eine mittig
platzierte Grafik/Text waehrend des Zoomens aus dem sichtbaren Ausschnitt
wandert, obwohl der einzelne gerenderte Frame korrekt aussieht (Nutzer-
Bugreport). Alle Texte bleiben innerhalb
einer "Safe Zone" oberhalb des unteren Bildrands, da Instagram/TikTok dort
beim Abspielen eigene UI-Elemente (Bildunterschrift, Kontoname, Audio-Titel)
ueberlagern. Statt eines flachen Schwarz-Hintergrunds ein dezenter dunkler
Verlauf. Auf Karten-Frames ein kleines Logo-Wasserzeichen unten rechts, auf
Intro-/Outro-Textkarten eine grosse, zentrierte Logo-Plakette - jeweils auf
einer hellen "Sticker"-Plakette, da das Logo selbst einen fast schwarzen
Hintergrund hat und sonst auf dunklem Grund untergehen wuerde.

Bewusst OHNE Tonspur: Instagram/TikTok bieten beim Hochladen eigene
lizenzfreie Musikbibliotheken an - das umgeht die Musiklizenzfrage
vollstaendig, statt selbst Musik einzubetten (siehe Backlog-Eintrag).

UNVERIFIZIERT: ffmpeg ist eine Systemabhaengigkeit (siehe Dockerfile), im
lokalen Entwicklungssandbox dieser Session nicht verfuegbar - die
Filtergraph-Syntax (zoompan, inkl. der rauszoomenden Variante ueber die
"on"-Frame-Nummer-Variable) folgt der dokumentierten ffmpeg-Referenz, wurde
aber noch nicht gegen ein echtes ffmpeg getestet."""
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

FRAME_SIZE = (1080, 1920)  # 9:16, Instagram/TikTok Reel-Format
# Instagram/TikTok legen beim Abspielen eigene UI-Elemente (Bildunterschrift,
# Kontoname, Audio-Titel, Aktions-Icons) ueber den unteren Rand des Videos -
# kein Text darf unterhalb dieser Marke landen, sonst wird er verdeckt.
SAFE_BOTTOM = 260
PHOTO_TOP = 90
PHOTO_MAX_H = 900

SECONDS_PER_CARD = 3
MIN_SECONDS_PER_CARD = 1.5
# Zielwert fuer die Gesamtlaenge der Karten-Segmente (ohne Intro/Outro) -
# bei vielen ausgewaehlten Karten wird die Zeit je Karte automatisch
# verkuerzt (bis zur Untergrenze), damit das Reel nicht unnoetig lang wird;
# kurze Reels haben auf Instagram tendenziell eine hoehere Abschlussrate.
TOTAL_CARDS_SECONDS_TARGET = 24
INTRO_SECONDS = 2
OUTRO_SECONDS = 4
FPS = 30
MAX_CARDS = 10
_BADGE_COLOR = (230, 57, 70)
_LOGO_PATH = Path(__file__).resolve().parent / "static" / "assets" / "dcardslab-logo.png"
_LOGO_SIZE = 72
_LOGO_MARGIN = 24
_LOGO_HERO_SIZE = 168
# Das Logo hat selbst einen fast schwarzen Hintergrund (kein Alphakanal) -
# ohne eigene helle "Sticker"-Plakette dahinter geht es auf einem dunklen
# Frame optisch unter. Gilt fuer die kleine Ecke-Wasserzeichen-Variante wie
# fuer die grosse Intro/Outro-Variante.
_LOGO_PLATE_PAD = 16
_LOGO_PLATE_FILL = (255, 255, 255, 235)
# Sanfter Verlauf statt Flat-Schwarz als Hintergrund - wirkt hochwertiger/
# zeitgemaesser als ein einfarbiger schwarzer Hintergrund, bleibt aber dunkel
# genug fuer guten Kontrast mit weissem Text.
_BG_TOP = (18, 18, 24)
_BG_BOTTOM = (48, 14, 20)


class VideoGenerationError(Exception):
    """ffmpeg ist nicht installiert, oder ein Renderschritt ist fehlgeschlagen."""


def ffmpeg_available():
    return shutil.which("ffmpeg") is not None


def _load_font(size):
    for candidate in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ):
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_centered_text(draw, text, font, center_x, y, fill):
    if not text:
        return
    bbox = draw.textbbox((0, 0), text, font=font)
    width = bbox[2] - bbox[0]
    draw.text((center_x - width / 2, y), text, font=font, fill=fill)


def _wrap_text(draw, text, font, max_width):
    words = text.split()
    lines, current = [], ""
    for word in words:
        candidate = f"{current} {word}".strip()
        bbox = draw.textbbox((0, 0), candidate, font=font)
        if bbox[2] - bbox[0] > max_width and current:
            lines.append(current)
            current = word
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines or [""]


def _draw_badges(draw, badges, center_x, y):
    # Zeigt so viele Badges wie in eine Zeile passen - lieber wenige gut
    # lesbare Chips als viele winzige/umgebrochene.
    font = _load_font(30)
    padding_x, gap, chip_h = 22, 14, 52
    max_row_width = FRAME_SIZE[0] - 80

    shown, total = [], -gap
    for badge in badges:
        bbox = draw.textbbox((0, 0), badge, font=font)
        chip_w = (bbox[2] - bbox[0]) + 2 * padding_x
        new_total = total + gap + chip_w
        if new_total > max_row_width and shown:
            break
        shown.append((badge, chip_w))
        total = new_total
    if not shown:
        return y

    cx = center_x - total / 2
    for badge, chip_w in shown:
        draw.rounded_rectangle([(cx, y), (cx + chip_w, y + chip_h)], radius=chip_h // 2, fill=_BADGE_COLOR)
        bbox = draw.textbbox((0, 0), badge, font=font)
        text_w = bbox[2] - bbox[0]
        draw.text((cx + (chip_w - text_w) / 2, y + 10), badge, font=font, fill=(255, 255, 255))
        cx += chip_w + gap
    return y + chip_h


def _gradient_background():
    """Sanfter vertikaler Verlauf statt Flat-Schwarz als Basis fuer jeden
    Frame - siehe _BG_TOP/_BG_BOTTOM."""
    frame = Image.new("RGB", FRAME_SIZE)
    draw = ImageDraw.Draw(frame)
    height = FRAME_SIZE[1]
    for y in range(height):
        t = y / (height - 1)
        color = tuple(int(_BG_TOP[i] + (_BG_BOTTOM[i] - _BG_TOP[i]) * t) for i in range(3))
        draw.line([(0, y), (FRAME_SIZE[0], y)], fill=color)
    return frame


def _load_logo(size):
    try:
        logo = Image.open(_LOGO_PATH).convert("RGBA")
    except OSError:
        # Fehlendes/nicht lesbares Logo darf das Reel nicht scheitern lassen -
        # Wasserzeichen/Branding ist ein Extra, kein Kernfeature.
        return None
    logo.thumbnail((size, size), Image.LANCZOS)
    return logo


def _paste_logo_on_plate(frame, logo, x, y):
    """Klebt das Logo auf eine helle, abgerundete "Sticker"-Plakette - das
    Logo selbst hat einen fast schwarzen Hintergrund (kein Alphakanal) und
    wuerde ohne diese Plakette auf einem dunklen Frame optisch untergehen."""
    pad = _LOGO_PLATE_PAD
    plate = Image.new("RGBA", (logo.width + 2 * pad, logo.height + 2 * pad), (0, 0, 0, 0))
    plate_draw = ImageDraw.Draw(plate)
    plate_draw.rounded_rectangle([(0, 0), (plate.width - 1, plate.height - 1)], radius=pad, fill=_LOGO_PLATE_FILL)
    frame.paste(plate, (x - pad, y - pad), plate)
    frame.paste(logo, (x, y), logo)


def _paste_logo_watermark(frame):
    logo = _load_logo(_LOGO_SIZE)
    if logo is None:
        return
    x = FRAME_SIZE[0] - logo.width - _LOGO_MARGIN - _LOGO_PLATE_PAD
    y = FRAME_SIZE[1] - SAFE_BOTTOM - logo.height - _LOGO_MARGIN - _LOGO_PLATE_PAD
    _paste_logo_on_plate(frame, logo, x, y)


def _draw_hero_logo(frame, center_x, top_y):
    """Grosse, zentrierte Logo-Plakette fuer Intro-/Outro-Textkarten (statt
    der kleinen Ecke-Wasserzeichen-Variante auf den Karten-Frames) - gibt
    beiden Textkarten einen klaren Absender. Gibt die y-Position direkt
    unterhalb der Plakette zurueck."""
    logo = _load_logo(_LOGO_HERO_SIZE)
    if logo is None:
        return top_y
    pad = _LOGO_PLATE_PAD
    x = center_x - logo.width // 2
    _paste_logo_on_plate(frame, logo, x, top_y + pad)
    return top_y + logo.height + 2 * pad


def _render_frame(image_bytes, title, price_text, subtitle_text="", badges=None):
    photo = Image.open(io.BytesIO(image_bytes))
    photo = ImageOps.exif_transpose(photo).convert("RGB")

    frame = _gradient_background()
    max_w = FRAME_SIZE[0] - 80
    photo.thumbnail((max_w, PHOTO_MAX_H), Image.LANCZOS)
    x = (FRAME_SIZE[0] - photo.width) // 2
    y = PHOTO_TOP
    frame.paste(photo, (x, y))

    draw = ImageDraw.Draw(frame)
    center_x = FRAME_SIZE[0] // 2
    banner_top = y + photo.height + 40
    # Halbtransparente Abdunkelung statt Flat-Schwarz-Block, damit der
    # Verlauf durchscheint und der Text trotzdem gut lesbar bleibt.
    overlay = Image.new("RGBA", FRAME_SIZE, (0, 0, 0, 0))
    ImageDraw.Draw(overlay).rectangle([(0, banner_top - 20), (FRAME_SIZE[0], FRAME_SIZE[1])], fill=(0, 0, 0, 150))
    frame = Image.alpha_composite(frame.convert("RGBA"), overlay).convert("RGB")
    draw = ImageDraw.Draw(frame)

    cursor_y = banner_top + 25
    _draw_centered_text(draw, title, _load_font(58), center_x, cursor_y, fill=(255, 255, 255))
    cursor_y += 80

    if badges:
        cursor_y = _draw_badges(draw, badges, center_x, cursor_y) + 24

    if subtitle_text:
        _draw_centered_text(draw, subtitle_text, _load_font(38), center_x, cursor_y, fill=(200, 200, 200))
        cursor_y += 66

    _draw_centered_text(draw, price_text, _load_font(56), center_x, cursor_y, fill=(255, 215, 0))
    _paste_logo_watermark(frame)
    return frame


def _render_text_frame(text):
    """Reine Textkarte (Intro/Outro) - z.B. "NEW CARDS" oder ein
    Call-to-Action wie "Jetzt auf eBay" - mit grosser, zentrierter
    Logo-Plakette als klarem Absender oberhalb des Texts."""
    frame = _gradient_background()
    draw = ImageDraw.Draw(frame)
    font = _load_font(64)
    lines = _wrap_text(draw, text, font, FRAME_SIZE[0] - 160)
    line_height = 84
    logo_block_h = _LOGO_HERO_SIZE + 2 * _LOGO_PLATE_PAD + 40
    usable_height = FRAME_SIZE[1] - SAFE_BOTTOM
    content_h = logo_block_h + len(lines) * line_height
    start_y = (usable_height - content_h) // 2
    text_start_y = _draw_hero_logo(frame, FRAME_SIZE[0] // 2, start_y) + 40
    draw = ImageDraw.Draw(frame)
    for i, line in enumerate(lines):
        _draw_centered_text(draw, line, font, FRAME_SIZE[0] // 2, text_start_y + i * line_height, fill=(255, 255, 255))
    return frame


def _render_segment(frame_path, segment_path, duration, zoom_out=False, static=False):
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", str(frame_path)]
    if static:
        # Intro-/Outro-Textkarten (Logo + Text) bewusst OHNE Zoom: zoompan
        # zoomt ohne eigene x/y-Angabe von der linken oberen Ecke aus, d.h.
        # der sichtbare Ausschnitt wandert beim Zoomen weg von der Mitte -
        # bei einer mittig platzierten Grafik/Text fuehrte das dazu, dass sie
        # waehrend des Abspielens aus dem Bild wanderte, obwohl der einzelne
        # gerenderte Frame korrekt aussah (Nutzer-Bugreport). Karten-Frames
        # (Fotos) behalten den Zoom-Effekt, da dort nichts am Bildrand klebt.
        pass
    else:
        duration_frames = int(duration * FPS)
        if zoom_out:
            # Startet bereits gezoomt (1.2x) und zoomt langsam auf 1.0x zurueck -
            # "on" ist zoompan's laufende Ausgabe-Frame-Nummer, damit der Zoom
            # nur beim allerersten Frame auf 1.2 gesetzt und danach schrittweise
            # reduziert wird (Standardmuster aus der ffmpeg-zoompan-Referenz).
            zoom_expr = "if(eq(on,1),1.2,max(1.0,zoom-0.0015))"
        else:
            # Ken-Burns-Zoom-in: startet bei 1.0x, steigt minimal bis max. 1.2x.
            zoom_expr = "min(zoom+0.0015,1.2)"
        zoompan = f"zoompan=z='{zoom_expr}':d={duration_frames}:s={FRAME_SIZE[0]}x{FRAME_SIZE[1]}:fps={FPS}"
        cmd += ["-vf", zoompan]
    cmd += ["-t", str(duration), "-pix_fmt", "yuv420p", str(segment_path)]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if result.returncode != 0:
        raise VideoGenerationError(f"ffmpeg-Rendern fehlgeschlagen: {result.stderr[-2000:]}")


def build_reel(cards, output_path, outro_text=None, intro_text=None):
    """cards: Liste von Dicts mit 'image_bytes', 'title', 'price_text' und
    optional 'subtitle_text'/'badges' - ein Frame je Listeneintrag (bei
    Vorder-+Rueckseite also zwei Eintraege je Karte). intro_text/outro_text:
    optionale Textkarten vor bzw. nach den Karten-Segmenten (z.B. "NEW
    CARDS" bzw. ein Call-to-Action wie "Jetzt auf eBay"). Schreibt ein
    stummes, vertikales (9:16) MP4 nach output_path. Wirft
    VideoGenerationError, wenn ffmpeg fehlt oder ein Schritt fehlschlaegt."""
    if not ffmpeg_available():
        raise VideoGenerationError(
            "ffmpeg ist auf diesem Server nicht installiert - der Video-Generator ist nicht verfuegbar."
        )
    if not cards:
        raise VideoGenerationError("Keine Karten ausgewaehlt.")
    if len(cards) > MAX_CARDS * 2:
        raise VideoGenerationError(
            f"Zu viele Bilder fuer ein Reel (max. {MAX_CARDS} Karten, bei Vorder+Rueckseite entsprechend weniger)."
        )

    # Bei vielen Karten die Zeit je Karte automatisch verkuerzen (bis zur
    # Untergrenze), damit die Gesamtlaenge nicht unnoetig ueber das
    # Zielbudget waechst - kuerzere Reels haben oft eine hoehere
    # Abschlussrate als sehr lange.
    duration_per_card = max(MIN_SECONDS_PER_CARD, min(SECONDS_PER_CARD, TOTAL_CARDS_SECONDS_TARGET / len(cards)))

    with tempfile.TemporaryDirectory(prefix="dcardslab_reel_") as tmp_str:
        tmp = Path(tmp_str)
        segment_paths = []

        if intro_text:
            intro_frame_path = tmp / "intro.jpg"
            _render_text_frame(intro_text).save(intro_frame_path, "JPEG", quality=90)
            intro_segment_path = tmp / "seg_intro.mp4"
            _render_segment(intro_frame_path, intro_segment_path, duration=INTRO_SECONDS, static=True)
            segment_paths.append(intro_segment_path)

        for index, card in enumerate(cards):
            frame_path = tmp / f"frame_{index}.jpg"
            _render_frame(
                card["image_bytes"], card["title"], card["price_text"],
                card.get("subtitle_text", ""), card.get("badges"),
            ).save(frame_path, "JPEG", quality=90)
            segment_path = tmp / f"seg_{index}.mp4"
            _render_segment(frame_path, segment_path, duration=duration_per_card, zoom_out=(index % 2 == 1))
            segment_paths.append(segment_path)

        if outro_text:
            outro_frame_path = tmp / "outro.jpg"
            _render_text_frame(outro_text).save(outro_frame_path, "JPEG", quality=90)
            outro_segment_path = tmp / "seg_outro.mp4"
            _render_segment(outro_frame_path, outro_segment_path, duration=OUTRO_SECONDS, static=True)
            segment_paths.append(outro_segment_path)

        list_path = tmp / "concat_list.txt"
        list_path.write_text("".join(f"file '{p.name}'\n" for p in segment_paths), encoding="utf-8")

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)],
            cwd=tmp, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise VideoGenerationError(f"ffmpeg-Zusammenfuegen fehlgeschlagen: {result.stderr[-2000:]}")
