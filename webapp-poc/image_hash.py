"""Perceptual image hashing (difference hash / dHash) for photo-based
duplicate detection when scanning cards - complements the existing exact
title/set/card_number text match (see db.find_duplicate_card()), which
misses duplicates where OCR/AI recognition misread a field. Pure logic, no
DB/HTTP - easy to unit test, same style as ebay_listing.py."""
from PIL import Image

HASH_SIZE = 8


def compute_hash(image_path):
    # Standard dHash: shrink to (HASH_SIZE+1) x HASH_SIZE greyscale, then for
    # each row compare each pixel to its right neighbour - a bit per
    # comparison gives HASH_SIZE*HASH_SIZE = 64 bits, robust to resizing/
    # recompression (unlike a pixel-exact hash) since it only encodes
    # relative brightness gradients, not absolute pixel values.
    img = Image.open(image_path).convert("L").resize((HASH_SIZE + 1, HASH_SIZE), Image.LANCZOS)
    pixels = list(img.getdata())
    width = HASH_SIZE + 1
    value = 0
    for row in range(HASH_SIZE):
        row_pixels = pixels[row * width:(row + 1) * width]
        for col in range(HASH_SIZE):
            value = (value << 1) | (1 if row_pixels[col] > row_pixels[col + 1] else 0)
    return format(value, f"0{HASH_SIZE * HASH_SIZE // 4}x")


def hamming_distance(hash_a, hash_b):
    return bin(int(hash_a, 16) ^ int(hash_b, 16)).count("1")
