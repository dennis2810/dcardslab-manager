"""Tests for webapp-poc/social_video.py. ffmpeg itself is not available in
this environment (no local install) - subprocess.run() is mocked throughout,
same approach as other unverified/external-tool integrations in this repo."""
import io
import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import social_video  # noqa: E402


def _fake_jpeg_bytes():
    buf = io.BytesIO()
    Image.new("RGB", (200, 300), color=(255, 0, 0)).save(buf, format="JPEG")
    return buf.getvalue()


class FfmpegAvailableTests(unittest.TestCase):
    def test_true_when_which_finds_it(self):
        with patch("social_video.shutil.which", return_value="/usr/bin/ffmpeg"):
            self.assertTrue(social_video.ffmpeg_available())

    def test_false_when_not_found(self):
        with patch("social_video.shutil.which", return_value=None):
            self.assertFalse(social_video.ffmpeg_available())


class RenderFrameTests(unittest.TestCase):
    def test_returns_frame_of_expected_size(self):
        frame = social_video._render_frame(_fake_jpeg_bytes(), "Test Karte", "9,99 €")
        self.assertEqual(frame.size, social_video.FRAME_SIZE)

    def test_handles_empty_price_text(self):
        frame = social_video._render_frame(_fake_jpeg_bytes(), "Test Karte", "")
        self.assertEqual(frame.size, social_video.FRAME_SIZE)

    def test_handles_subtitle_and_badges(self):
        frame = social_video._render_frame(
            _fake_jpeg_bytes(), "Test Karte", "9,99 €",
            subtitle_text="FC Bayern · Topps Chrome · NM", badges=["Rookie", "Auto", "Numbered", "Refractor"],
        )
        self.assertEqual(frame.size, social_video.FRAME_SIZE)

    def test_handles_no_badges(self):
        frame = social_video._render_frame(_fake_jpeg_bytes(), "Test Karte", "9,99 €", badges=[])
        self.assertEqual(frame.size, social_video.FRAME_SIZE)


class RenderTextFrameTests(unittest.TestCase):
    def test_returns_frame_of_expected_size(self):
        frame = social_video._render_text_frame("🛒 Karten jetzt auf eBay – Link im Profil")
        self.assertEqual(frame.size, social_video.FRAME_SIZE)

    def test_wraps_long_text_onto_multiple_lines(self):
        draw = social_video.ImageDraw.Draw(social_video.Image.new("RGB", social_video.FRAME_SIZE))
        long_text = "Ein sehr langer Abschlusstext der garantiert nicht in eine einzige Zeile passt"
        lines = social_video._wrap_text(draw, long_text, social_video._load_font(64), social_video.FRAME_SIZE[0] - 160)
        self.assertGreater(len(lines), 1)


class GradientBackgroundTests(unittest.TestCase):
    def test_top_and_bottom_rows_differ(self):
        # Verlauf statt Flat-Farbe - oberste und unterste Zeile muessen sich
        # unterscheiden (siehe _BG_TOP/_BG_BOTTOM).
        frame = social_video._gradient_background()
        self.assertEqual(frame.size, social_video.FRAME_SIZE)
        top_pixel = frame.getpixel((0, 0))
        bottom_pixel = frame.getpixel((0, frame.height - 1))
        self.assertNotEqual(top_pixel, bottom_pixel)
        self.assertEqual(top_pixel, social_video._BG_TOP)
        self.assertEqual(bottom_pixel, social_video._BG_BOTTOM)


class LogoPlateTests(unittest.TestCase):
    def test_load_logo_returns_none_when_file_missing(self):
        with patch("social_video._LOGO_PATH", Path("/does/not/exist.png")):
            self.assertIsNone(social_video._load_logo(72))

    def test_draw_hero_logo_is_a_noop_when_logo_missing(self):
        frame = social_video.Image.new("RGB", social_video.FRAME_SIZE)
        with patch("social_video._LOGO_PATH", Path("/does/not/exist.png")):
            result_y = social_video._draw_hero_logo(frame, social_video.FRAME_SIZE[0] // 2, 100)
        self.assertEqual(result_y, 100)

    def test_paste_logo_watermark_is_a_noop_when_logo_missing(self):
        frame = social_video.Image.new("RGB", social_video.FRAME_SIZE, color=(1, 2, 3))
        with patch("social_video._LOGO_PATH", Path("/does/not/exist.png")):
            social_video._paste_logo_watermark(frame)
        self.assertEqual(frame.getpixel((0, 0)), (1, 2, 3))

    def test_render_text_frame_places_hero_logo_when_logo_present(self):
        # Das echte Repo-Logo existiert im static/assets-Ordner - hier nur
        # pruefen, dass das Rendern nicht crasht und die Groesse stimmt.
        frame = social_video._render_text_frame("NEW CARDS")
        self.assertEqual(frame.size, social_video.FRAME_SIZE)


class BuildReelTests(unittest.TestCase):
    def _successful_result(self):
        result = MagicMock()
        result.returncode = 0
        result.stderr = ""
        return result

    def test_raises_when_ffmpeg_missing(self):
        with patch("social_video.ffmpeg_available", return_value=False):
            with self.assertRaises(social_video.VideoGenerationError):
                social_video.build_reel([{"image_bytes": _fake_jpeg_bytes(), "title": "A", "price_text": ""}], "out.mp4")

    def test_raises_when_no_cards(self):
        with patch("social_video.ffmpeg_available", return_value=True):
            with self.assertRaises(social_video.VideoGenerationError):
                social_video.build_reel([], "out.mp4")

    def test_raises_when_too_many_cards(self):
        # Der Frame-Limit ist MAX_CARDS*2, da Vorder-+Rueckseite je Karte
        # zwei Frames erzeugen kann.
        cards = [{"image_bytes": _fake_jpeg_bytes(), "title": "A", "price_text": ""}] * (social_video.MAX_CARDS * 2 + 1)
        with patch("social_video.ffmpeg_available", return_value=True):
            with self.assertRaises(social_video.VideoGenerationError):
                social_video.build_reel(cards, "out.mp4")

    def test_runs_ffmpeg_per_segment_then_concat(self):
        cards = [
            {"image_bytes": _fake_jpeg_bytes(), "title": "Karte 1", "price_text": "5 €"},
            {"image_bytes": _fake_jpeg_bytes(), "title": "Karte 2", "price_text": ""},
        ]
        real_tmpdir = tempfile.mkdtemp(prefix="dcardslab_test_reel_")
        self.addCleanup(shutil.rmtree, real_tmpdir, ignore_errors=True)
        with patch("social_video.ffmpeg_available", return_value=True), \
             patch("social_video.subprocess.run", return_value=self._successful_result()) as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__.return_value = real_tmpdir
            output_path = Path(real_tmpdir) / "reel.mp4"
            social_video.build_reel(cards, output_path)
        # 2 Segment-Renders (ein ffmpeg-Aufruf je Karte) + 1 finaler concat-Aufruf.
        self.assertEqual(mock_run.call_count, 3)
        concat_call = mock_run.call_args_list[-1]
        self.assertIn("concat", concat_call.args[0])

    def test_outro_text_appends_an_extra_segment(self):
        cards = [{"image_bytes": _fake_jpeg_bytes(), "title": "Karte 1", "price_text": ""}]
        real_tmpdir = tempfile.mkdtemp(prefix="dcardslab_test_reel_outro_")
        self.addCleanup(shutil.rmtree, real_tmpdir, ignore_errors=True)
        with patch("social_video.ffmpeg_available", return_value=True), \
             patch("social_video.subprocess.run", return_value=self._successful_result()) as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__.return_value = real_tmpdir
            output_path = Path(real_tmpdir) / "reel.mp4"
            social_video.build_reel(cards, output_path, outro_text="🛒 Jetzt auf eBay")
        # 1 Karten-Segment + 1 Outro-Segment + 1 finaler concat-Aufruf.
        self.assertEqual(mock_run.call_count, 3)
        list_text = (Path(real_tmpdir) / "concat_list.txt").read_text(encoding="utf-8")
        self.assertIn("seg_outro.mp4", list_text)

    def test_no_outro_text_means_no_outro_segment(self):
        cards = [{"image_bytes": _fake_jpeg_bytes(), "title": "Karte 1", "price_text": ""}]
        real_tmpdir = tempfile.mkdtemp(prefix="dcardslab_test_reel_no_outro_")
        self.addCleanup(shutil.rmtree, real_tmpdir, ignore_errors=True)
        with patch("social_video.ffmpeg_available", return_value=True), \
             patch("social_video.subprocess.run", return_value=self._successful_result()) as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__.return_value = real_tmpdir
            output_path = Path(real_tmpdir) / "reel.mp4"
            social_video.build_reel(cards, output_path, outro_text=None)
        # 1 Karten-Segment + 1 finaler concat-Aufruf, kein Outro.
        self.assertEqual(mock_run.call_count, 2)

    def test_intro_text_prepends_an_extra_segment(self):
        cards = [{"image_bytes": _fake_jpeg_bytes(), "title": "Karte 1", "price_text": ""}]
        real_tmpdir = tempfile.mkdtemp(prefix="dcardslab_test_reel_intro_")
        self.addCleanup(shutil.rmtree, real_tmpdir, ignore_errors=True)
        with patch("social_video.ffmpeg_available", return_value=True), \
             patch("social_video.subprocess.run", return_value=self._successful_result()) as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__.return_value = real_tmpdir
            output_path = Path(real_tmpdir) / "reel.mp4"
            social_video.build_reel(cards, output_path, intro_text="🔥 NEW CARDS 🔥")
        # 1 Intro-Segment + 1 Karten-Segment + 1 finaler concat-Aufruf.
        self.assertEqual(mock_run.call_count, 3)
        list_text = (Path(real_tmpdir) / "concat_list.txt").read_text(encoding="utf-8")
        self.assertTrue(list_text.startswith("file 'seg_intro.mp4'"))

    def test_no_intro_text_means_no_intro_segment(self):
        cards = [{"image_bytes": _fake_jpeg_bytes(), "title": "Karte 1", "price_text": ""}]
        real_tmpdir = tempfile.mkdtemp(prefix="dcardslab_test_reel_no_intro_")
        self.addCleanup(shutil.rmtree, real_tmpdir, ignore_errors=True)
        with patch("social_video.ffmpeg_available", return_value=True), \
             patch("social_video.subprocess.run", return_value=self._successful_result()) as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__.return_value = real_tmpdir
            output_path = Path(real_tmpdir) / "reel.mp4"
            social_video.build_reel(cards, output_path, intro_text=None)
        self.assertEqual(mock_run.call_count, 2)

    def test_alternates_zoom_in_and_zoom_out_per_card(self):
        cards = [
            {"image_bytes": _fake_jpeg_bytes(), "title": "Karte 1", "price_text": ""},
            {"image_bytes": _fake_jpeg_bytes(), "title": "Karte 2", "price_text": ""},
        ]
        real_tmpdir = tempfile.mkdtemp(prefix="dcardslab_test_reel_zoom_")
        self.addCleanup(shutil.rmtree, real_tmpdir, ignore_errors=True)
        with patch("social_video.ffmpeg_available", return_value=True), \
             patch("social_video.subprocess.run", return_value=self._successful_result()) as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__.return_value = real_tmpdir
            output_path = Path(real_tmpdir) / "reel.mp4"
            social_video.build_reel(cards, output_path)
        segment_calls = mock_run.call_args_list[:-1]
        first_vf = segment_calls[0].args[0][segment_calls[0].args[0].index("-vf") + 1]
        second_vf = segment_calls[1].args[0][segment_calls[1].args[0].index("-vf") + 1]
        self.assertIn("min(zoom+0.0015,1.2)", first_vf)
        self.assertIn("if(eq(on,1),1.2,max(1.0,zoom-0.0015))", second_vf)

    def test_duration_per_card_shrinks_towards_minimum_for_many_cards(self):
        cards = [
            {"image_bytes": _fake_jpeg_bytes(), "title": f"Karte {i}", "price_text": ""}
            for i in range(social_video.MAX_CARDS)
        ]
        real_tmpdir = tempfile.mkdtemp(prefix="dcardslab_test_reel_duration_")
        self.addCleanup(shutil.rmtree, real_tmpdir, ignore_errors=True)
        with patch("social_video.ffmpeg_available", return_value=True), \
             patch("social_video.subprocess.run", return_value=self._successful_result()) as mock_run, \
             patch("tempfile.TemporaryDirectory") as mock_tmpdir:
            mock_tmpdir.return_value.__enter__.return_value = real_tmpdir
            output_path = Path(real_tmpdir) / "reel.mp4"
            social_video.build_reel(cards, output_path)
        expected = max(
            social_video.MIN_SECONDS_PER_CARD,
            min(social_video.SECONDS_PER_CARD, social_video.TOTAL_CARDS_SECONDS_TARGET / len(cards)),
        )
        first_segment_args = mock_run.call_args_list[0].args[0]
        self.assertEqual(first_segment_args[first_segment_args.index("-t") + 1], str(expected))

    def test_raises_video_generation_error_when_segment_render_fails(self):
        failing_result = MagicMock()
        failing_result.returncode = 1
        failing_result.stderr = "boom"
        cards = [{"image_bytes": _fake_jpeg_bytes(), "title": "Karte 1", "price_text": ""}]
        with patch("social_video.ffmpeg_available", return_value=True), \
             patch("social_video.subprocess.run", return_value=failing_result):
            with self.assertRaises(social_video.VideoGenerationError):
                social_video.build_reel(cards, "out.mp4")


if __name__ == "__main__":
    unittest.main()
