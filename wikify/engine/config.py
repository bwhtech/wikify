"""Pipeline constants ported from the POC `config.py`.

Model ids, thresholds, and the render/visual tunables are user-tunable and live in
`Wikify Settings` (read via `engine.settings`). What stays here are the composite
**weights** — code-side constants we don't expose for tuning (spec 02-data-model).

The `RENDER_DPI` / `VISUAL_MIN_*` constants below are kept as fallback defaults for
the headless path; the live pipeline reads them from `engine.settings`.
"""

from __future__ import annotations

# Page render resolution — the snapshot the models see.
RENDER_DPI = 150

# Heuristic page classification (POC `pdf_utils.classify_page`):
# a page is "visual" when its extractable text is too sparse to trust as ground
# truth AND it carries images or many vector drawings.
VISUAL_MIN_CHARS = 250
VISUAL_MIN_DRAWINGS = 40

# Composite weights for TEXT pages (table term dropped + renormalized when no table).
WEIGHTS = {
	"text_recall": 0.40,
	"not_hallucinated": 0.15,  # applied to (1 - extra_ratio)
	"table_score": 0.15,
	"judge_score": 0.30,
}

# VISUAL pages (diagrams/flowcharts/images): PyMuPDF text is near-empty there, so
# recall/extra are meaningless and even inverted (they reward an empty parse). The
# judge looking at the page image is the only valid arbiter.
VISUAL_WEIGHTS = {
	"judge_score": 0.85,
	"table_score": 0.15,
}
