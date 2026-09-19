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


class RenderOutroFrameTests(unittest.TestCase):
    def test_returns_frame_of_expected_size(self):
        frame = social_video._render_outro_frame("🛒 Karten jetzt auf eBay – Link im Profil")
        self.assertEqual(frame.size, social_video.FRAME_SIZE)

    def test_wraps_long_text_onto_multiple_lines(self):
        draw = social_video.ImageDraw.Draw(social_video.Image.new("RGB", social_video.FRAME_SIZE))
        long_text = "Ein sehr langer Abschlusstext der garantiert nicht in eine einzige Zeile passt"
        lines = social_video._wrap_text(draw, long_text, social_video._load_font(64), social_video.FRAME_SIZE[0] - 160)
        self.assertGreater(len(lines), 1)


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
