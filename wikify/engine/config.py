"""Pipeline constants ported from the POC `config.py`.

Slice 1b only needs the render + page-classification tunables. Model ids,
thresholds, and weights move into `Wikify Settings` (Single) in Slice 2.
"""

from __future__ import annotations

# Page render resolution — the snapshot the models see.
RENDER_DPI = 150

# Heuristic page classification (POC `pdf_utils.classify_page`):
# a page is "visual" when its extractable text is too sparse to trust as ground
# truth AND it carries images or many vector drawings.
VISUAL_MIN_CHARS = 250
VISUAL_MIN_DRAWINGS = 40
