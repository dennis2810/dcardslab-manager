"""Pillow + ffmpeg Reel-Generator fuer die Social-Media-Seite - rendert je
ausgewaehlter Karte ein Bild mit Titel-/Preis-Overlay (optional Team/Set/
Zustand-Zeile, Badges wie Rookie/Auto/Numbered/Refractor und die Rueckseite
als zweites Bild) und haengt die Bilder per ffmpeg (zoompan-Ken-Burns-Effekt
je Bild, dann concat) zu einem stummen, vertikalen (9:16) MP4 aneinander -
optional gefolgt von einer Outro-Textkarte (z.B. "Jetzt auf eBay").

Bewusst OHNE Tonspur: Instagram/TikTok bieten beim Hochladen eigene
lizenzfreie Musikbibliotheken an - das umgeht die Musiklizenzfrage
vollstaendig, statt selbst Musik einzubetten (siehe Backlog-Eintrag).

UNVERIFIZIERT: ffmpeg ist eine Systemabhaengigkeit (siehe Dockerfile), im
lokalen Entwicklungssandbox dieser Session nicht verfuegbar - die
Filtergraph-Syntax (zoompan) folgt der dokumentierten ffmpeg-Referenz, wurde
aber noch nicht gegen ein echtes ffmpeg getestet."""
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

FRAME_SIZE = (1080, 1920)  # 9:16, Instagram/TikTok Reel-Format
SECONDS_PER_CARD = 3
OUTRO_SECONDS = 4
FPS = 30
MAX_CARDS = 10
_BADGE_COLOR = (230, 57, 70)


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


def _render_frame(image_bytes, title, price_text, subtitle_text="", badges=None):
    photo = Image.open(io.BytesIO(image_bytes))
    photo = ImageOps.exif_transpose(photo).convert("RGB")

    frame = Image.new("RGB", FRAME_SIZE, color=(15, 15, 15))
    max_w, max_h = FRAME_SIZE[0] - 80, int(FRAME_SIZE[1] * 0.60)
    photo.thumbnail((max_w, max_h), Image.LANCZOS)
    x = (FRAME_SIZE[0] - photo.width) // 2
    y = 90
    frame.paste(photo, (x, y))

    draw = ImageDraw.Draw(frame)
    center_x = FRAME_SIZE[0] // 2
    banner_top = y + photo.height + 40
    draw.rectangle([(0, banner_top - 20), (FRAME_SIZE[0], FRAME_SIZE[1])], fill=(0, 0, 0))

    cursor_y = banner_top + 25
    _draw_centered_text(draw, title, _load_font(58), center_x, cursor_y, fill=(255, 255, 255))
    cursor_y += 80

    if badges:
        cursor_y = _draw_badges(draw, badges, center_x, cursor_y) + 24

    if subtitle_text:
        _draw_centered_text(draw, subtitle_text, _load_font(38), center_x, cursor_y, fill=(200, 200, 200))
        cursor_y += 66

    _draw_centered_text(draw, price_text, _load_font(56), center_x, cursor_y, fill=(255, 215, 0))
    return frame


def _render_outro_frame(text):
    frame = Image.new("RGB", FRAME_SIZE, color=(15, 15, 15))
    draw = ImageDraw.Draw(frame)
    font = _load_font(64)
    lines = _wrap_text(draw, text, font, FRAME_SIZE[0] - 160)
    line_height = 84
    start_y = (FRAME_SIZE[1] - len(lines) * line_height) // 2
    for i, line in enumerate(lines):
        _draw_centered_text(draw, line, font, FRAME_SIZE[0] // 2, start_y + i * line_height, fill=(255, 255, 255))
    return frame


def _render_segment(frame_path, segment_path, duration=None):
    duration = duration or SECONDS_PER_CARD
    duration_frames = int(duration * FPS)
    # zoompan: langsamer, stetiger "Ken Burns"-Zoom auf das Standbild statt
    # eines statischen Fotos - z steigt pro Frame minimal bis max. 1.2x.
    zoompan = f"zoompan=z='min(zoom+0.0015,1.2)':d={duration_frames}:s={FRAME_SIZE[0]}x{FRAME_SIZE[1]}:fps={FPS}"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", str(frame_path),
            "-vf", zoompan, "-t", str(duration),
            "-pix_fmt", "yuv420p", str(segment_path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise VideoGenerationError(f"ffmpeg-Rendern fehlgeschlagen: {result.stderr[-2000:]}")


def build_reel(cards, output_path, outro_text=None):
    """cards: Liste von Dicts mit 'image_bytes', 'title', 'price_text' und
    optional 'subtitle_text'/'badges' - ein Frame je Listeneintrag (bei
    Vorder-+Rueckseite also zwei Eintraege je Karte). outro_text: optionale
    Textkarte, die als letztes Segment angehaengt wird (z.B. ein
    Call-to-Action wie "Jetzt auf eBay"). Schreibt ein stummes, vertikales
    (9:16) MP4 nach output_path. Wirft VideoGenerationError, wenn ffmpeg
    fehlt oder ein Schritt fehlschlaegt."""
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

    with tempfile.TemporaryDirectory(prefix="dcardslab_reel_") as tmp_str:
        tmp = Path(tmp_str)
        segment_paths = []
        for index, card in enumerate(cards):
            frame_path = tmp / f"frame_{index}.jpg"
            _render_frame(
                card["image_bytes"], card["title"], card["price_text"],
                card.get("subtitle_text", ""), card.get("badges"),
            ).save(frame_path, "JPEG", quality=90)
            segment_path = tmp / f"seg_{index}.mp4"
            _render_segment(frame_path, segment_path)
            segment_paths.append(segment_path)

        if outro_text:
            outro_frame_path = tmp / "outro.jpg"
            _render_outro_frame(outro_text).save(outro_frame_path, "JPEG", quality=90)
            outro_segment_path = tmp / "seg_outro.mp4"
            _render_segment(outro_frame_path, outro_segment_path, duration=OUTRO_SECONDS)
            segment_paths.append(outro_segment_path)

        list_path = tmp / "concat_list.txt"
        list_path.write_text("".join(f"file '{p.name}'\n" for p in segment_paths), encoding="utf-8")

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)],
            cwd=tmp, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise VideoGenerationError(f"ffmpeg-Zusammenfuegen fehlgeschlagen: {result.stderr[-2000:]}")
