"""Tests for scanner_v0_8_dynamic.py's grid-cell boundary detection.

_find_grid_components_by_cells() is the fallback path used whenever the
9 cards can't be separated by color/saturation alone - the common case for
plain white card backs. It used to split the scan into an even 3x3 grid,
which only produces correct cell boundaries when all 9 cards are spaced
with identical gaps. Real scans often aren't that uniform (e.g. wider gaps
between rows than between columns), so an even split cuts into one card's
edge while leaving a strip of background paper on the cell's other side -
this is what produced the skewed/edge-bleeding crops reported on scanned
card backs. _grid_cell_bounds() instead locates the real gaps via an
edge-density profile.
"""
import sys
import types
import unittest
from pathlib import Path

import numpy as np
import cv2

for _name in ("tkinter", "tkinter.filedialog", "tkinter.messagebox", "tkinter.ttk"):
    if _name not in sys.modules:
        sys.modules[_name] = types.ModuleType(_name)

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "scanner"))

import scanner_v0_8_dynamic as scanner  # noqa: E402


def _bordered_card(size_w, size_h, border=6):
    """A plain white 'card back' - white fill with only a thin black border,
    the same low-contrast shape a real card back's outer edge has."""
    card = np.full((size_h, size_w), 255, np.uint8)
    cv2.rectangle(card, (0, 0), (size_w - 1, size_h - 1), 0, border)
    return card


def _paste(canvas, card, x, y):
    h, w = card.shape[:2]
    canvas[y:y + h, x:x + w] = card


class FindGridDividersTests(unittest.TestCase):
    def test_finds_dividers_inside_the_real_gaps_not_at_uniform_thirds(self):
        # Three 100px-tall bands (edges at their borders) with deliberately
        # uneven gaps: 20px after band 1, 60px after band 2. A uniform
        # three-way split of the 100+20+100+60+100=380px profile would put
        # dividers at ~127 and ~253 - both landing inside band 2 or 3.
        profile = np.zeros(380, np.float64)
        profile[0:100] = 50.0
        # gap 100:120 stays 0
        profile[120:220] = 50.0
        # gap 220:280 stays 0
        profile[280:380] = 50.0

        dividers = scanner._find_grid_dividers(profile, len(profile))
        self.assertIsNotNone(dividers)
        self.assertTrue(100 <= dividers[0] <= 120, dividers)
        self.assertTrue(220 <= dividers[1] <= 280, dividers)

    def test_returns_none_when_profile_has_no_clear_gap(self):
        # Flat, featureless profile - nothing for the search windows to
        # latch onto, so callers must fall back to a uniform split instead
        # of picking an arbitrary noise minimum.
        profile = np.full(300, 10.0)
        self.assertIsNone(scanner._find_grid_dividers(profile, len(profile)))

    def test_returns_none_for_a_too_short_profile(self):
        self.assertIsNone(scanner._find_grid_dividers(np.zeros(10), 10))


class GridCellBoundsTests(unittest.TestCase):
    def test_uses_real_gaps_for_unevenly_spaced_rows_and_columns(self):
        # 3x3 grid of plain-bordered "card back" cells, each 200x260, with
        # uneven gaps: rows separated by 30px then 90px; columns by 40px
        # then 100px. A naive uniform 3-way split of the full extent would
        # place its dividers inside a card instead of in the gap.
        card_w, card_h = 200, 260
        row_gaps = [30, 90]
        col_gaps = [40, 100]
        row_starts = [0]
        for g in row_gaps:
            row_starts.append(row_starts[-1] + card_h + g)
        col_starts = [0]
        for g in col_gaps:
            col_starts.append(col_starts[-1] + card_w + g)

        H = row_starts[-1] + card_h + 20
        W = col_starts[-1] + card_w + 20
        canvas = np.full((H, W), 255, np.uint8)
        for ry in row_starts:
            for cx in col_starts:
                _paste(canvas, _bordered_card(card_w, card_h), cx, ry)

        rows, cols = scanner._grid_cell_bounds(canvas, 0, 0, W, H)

        # Each divider should fall strictly within its real gap, not at the
        # uniform-thirds position (which would land inside a card).
        gap1_row = (row_starts[0] + card_h, row_starts[1])
        gap2_row = (row_starts[1] + card_h, row_starts[2])
        self.assertTrue(gap1_row[0] <= rows[1] <= gap1_row[1], (rows, gap1_row))
        self.assertTrue(gap2_row[0] <= rows[2] <= gap2_row[1], (rows, gap2_row))

        gap1_col = (col_starts[0] + card_w, col_starts[1])
        gap2_col = (col_starts[1] + card_w, col_starts[2])
        self.assertTrue(gap1_col[0] <= cols[1] <= gap1_col[1], (cols, gap1_col))
        self.assertTrue(gap2_col[0] <= cols[2] <= gap2_col[1], (cols, gap2_col))

    def test_falls_back_to_uniform_thirds_on_a_blank_image(self):
        blank = np.full((300, 300), 255, np.uint8)
        rows, cols = scanner._grid_cell_bounds(blank, 0, 0, 300, 300)
        self.assertEqual(rows, [0, 100, 200, 300])
        self.assertEqual(cols, [0, 100, 200, 300])


if __name__ == "__main__":
    unittest.main()
