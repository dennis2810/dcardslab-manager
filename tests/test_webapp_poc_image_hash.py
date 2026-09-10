"""Tests for webapp-poc/image_hash.py - perceptual image hashing used for
photo-based duplicate detection when scanning cards (complements the
existing title/set/card_number text match, which misses duplicates where
OCR/recognition misread a field)."""
import sys
import tempfile
import unittest
from pathlib import Path

from PIL import Image

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "webapp-poc"))

import image_hash  # noqa: E402


def _write_image(path, size=(400, 600), color=(200, 50, 50)):
    img = Image.new("RGB", size, color=color)
    img.save(path, format="PNG")
    return path


def _write_gradient_image(path, size=(400, 600)):
    img = Image.new("RGB", size)
    pixels = img.load()
    for x in range(size[0]):
        for y in range(size[1]):
            pixels[x, y] = (x % 256, y % 256, (x + y) % 256)
    img.save(path, format="PNG")
    return path


class ComputeHashTests(unittest.TestCase):
    def test_returns_16_char_hex_string(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_image(Path(tmp) / "a.png")
            result = image_hash.compute_hash(path)
        self.assertEqual(len(result), 16)
        int(result, 16)  # raises ValueError if not valid hex

    def test_identical_images_produce_identical_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            path_a = _write_gradient_image(Path(tmp) / "a.png")
            path_b = _write_gradient_image(Path(tmp) / "b.png")
            hash_a = image_hash.compute_hash(path_a)
            hash_b = image_hash.compute_hash(path_b)
        self.assertEqual(hash_a, hash_b)

    def test_solid_color_images_of_different_colors_hash_the_same(self):
        # dHash compares adjacent-pixel gradients, not absolute color - a
        # flat image has no gradient at all, so two different flat colors
        # both hash to the same (all-false) bit pattern. Documents this
        # known limitation rather than asserting the opposite.
        with tempfile.TemporaryDirectory() as tmp:
            path_a = _write_image(Path(tmp) / "a.png", color=(200, 50, 50))
            path_b = _write_image(Path(tmp) / "b.png", color=(10, 10, 200))
            hash_a = image_hash.compute_hash(path_a)
            hash_b = image_hash.compute_hash(path_b)
        self.assertEqual(hash_a, hash_b)

    def test_visually_different_images_produce_different_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            solid_path = _write_image(Path(tmp) / "solid.png")
            gradient_path = _write_gradient_image(Path(tmp) / "gradient.png")
            hash_solid = image_hash.compute_hash(solid_path)
            hash_gradient = image_hash.compute_hash(gradient_path)
        self.assertNotEqual(hash_solid, hash_gradient)


class HammingDistanceTests(unittest.TestCase):
    def test_identical_hashes_have_zero_distance(self):
        self.assertEqual(image_hash.hamming_distance("00ff00ff00ff00ff", "00ff00ff00ff00ff"), 0)

    def test_counts_differing_bits(self):
        self.assertEqual(image_hash.hamming_distance("0000000000000000", "0000000000000001"), 1)
        self.assertEqual(image_hash.hamming_distance("0000000000000000", "ffffffffffffffff"), 64)

    def test_is_symmetric(self):
        a, b = "1234567890abcdef", "fedcba0987654321"
        self.assertEqual(image_hash.hamming_distance(a, b), image_hash.hamming_distance(b, a))


if __name__ == "__main__":
    unittest.main()
