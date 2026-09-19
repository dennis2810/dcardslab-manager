"""Pillow + ffmpeg Reel-Generator fuer die Social-Media-Seite - rendert je
ausgewaehlter Karte ein Bild mit Titel-/Preis-Overlay und haengt die Bilder
per ffmpeg (zoompan-Ken-Burns-Effekt je Karte, dann concat) zu einem
stummen, vertikalen (9:16) MP4 aneinander.

Bewusst OHNE Tonspur: Instagram/TikTok bieten beim Hochladen eigene
lizenzfreie Musikbibliotheken an - das umgeht die Musiklizenzfrage
vollstaendig, statt selbst Musik einzubetten (siehe Backlog-Eintrag).

UNVERIFIZIERT: ffmpeg ist eine Systemabhaengigkeit (siehe Dockerfile), im
lokalen Entwicklungssandbox dieser Session nicht verfuegbar - die
Filtergraph-Syntax (zoompan) folgt der dokumentierten ffmpeg-Referenz,
wurde aber noch nicht gegen ein echtes ffmpeg getestet."""
import io
import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

FRAME_SIZE = (1080, 1920)  # 9:16, Instagram/TikTok Reel-Format
SECONDS_PER_CARD = 3
FPS = 30
MAX_CARDS = 10


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


def _render_frame(image_bytes, title, price_text):
    photo = Image.open(io.BytesIO(image_bytes))
    photo = ImageOps.exif_transpose(photo).convert("RGB")

    frame = Image.new("RGB", FRAME_SIZE, color=(15, 15, 15))
    max_w, max_h = FRAME_SIZE[0] - 80, int(FRAME_SIZE[1] * 0.68)
    photo.thumbnail((max_w, max_h), Image.LANCZOS)
    x = (FRAME_SIZE[0] - photo.width) // 2
    y = 110
    frame.paste(photo, (x, y))

    draw = ImageDraw.Draw(frame)
    banner_top = y + photo.height + 50
    draw.rectangle([(0, banner_top - 20), (FRAME_SIZE[0], FRAME_SIZE[1])], fill=(0, 0, 0))
    _draw_centered_text(draw, title, _load_font(64), FRAME_SIZE[0] // 2, banner_top + 40, fill=(255, 255, 255))
    _draw_centered_text(
        draw, price_text, _load_font(56), FRAME_SIZE[0] // 2, banner_top + 140, fill=(255, 215, 0)
    )
    return frame


def _render_segment(frame_path, segment_path):
    duration_frames = int(SECONDS_PER_CARD * FPS)
    # zoompan: langsamer, stetiger "Ken Burns"-Zoom auf das Standbild statt
    # eines statischen Fotos - z steigt pro Frame minimal bis max. 1.2x.
    zoompan = f"zoompan=z='min(zoom+0.0015,1.2)':d={duration_frames}:s={FRAME_SIZE[0]}x{FRAME_SIZE[1]}:fps={FPS}"
    result = subprocess.run(
        [
            "ffmpeg", "-y", "-loop", "1", "-i", str(frame_path),
            "-vf", zoompan, "-t", str(SECONDS_PER_CARD),
            "-pix_fmt", "yuv420p", str(segment_path),
        ],
        capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise VideoGenerationError(f"ffmpeg-Rendern fehlgeschlagen: {result.stderr[-2000:]}")


def build_reel(cards, output_path):
    """cards: Liste von Dicts mit 'image_bytes', 'title', 'price_text'.
    Schreibt ein stummes, vertikales (9:16) MP4 nach output_path. Wirft
    VideoGenerationError, wenn ffmpeg fehlt oder ein Schritt fehlschlaegt."""
    if not ffmpeg_available():
        raise VideoGenerationError(
            "ffmpeg ist auf diesem Server nicht installiert - der Video-Generator ist nicht verfuegbar."
        )
    if not cards:
        raise VideoGenerationError("Keine Karten ausgewaehlt.")
    if len(cards) > MAX_CARDS:
        raise VideoGenerationError(f"Maximal {MAX_CARDS} Karten je Reel.")

    with tempfile.TemporaryDirectory(prefix="dcardslab_reel_") as tmp_str:
        tmp = Path(tmp_str)
        segment_paths = []
        for index, card in enumerate(cards):
            frame_path = tmp / f"frame_{index}.jpg"
            _render_frame(card["image_bytes"], card["title"], card["price_text"]).save(
                frame_path, "JPEG", quality=90
            )
            segment_path = tmp / f"seg_{index}.mp4"
            _render_segment(frame_path, segment_path)
            segment_paths.append(segment_path)

        list_path = tmp / "concat_list.txt"
        list_path.write_text("".join(f"file '{p.name}'\n" for p in segment_paths), encoding="utf-8")

        result = subprocess.run(
            ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_path), "-c", "copy", str(output_path)],
            cwd=tmp, capture_output=True, text=True, timeout=120,
        )
        if result.returncode != 0:
            raise VideoGenerationError(f"ffmpeg-Zusammenfuegen fehlgeschlagen: {result.stderr[-2000:]}")
