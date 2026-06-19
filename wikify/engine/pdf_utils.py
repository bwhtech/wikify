"""PDF rendering + page classification via PyMuPDF.

Ported from the POC `pdf_utils.py`. The one I/O change: instead of writing PNGs
to disk under `storage/pages/`, `render_png` returns the PNG bytes and `store.py`
persists them as Frappe **File** docs attached to each Source Page.
"""

from __future__ import annotations

from pathlib import Path

import fitz  # PyMuPDF

from wikify.engine import config


def classify_page(page) -> str:
	"""Heuristic page type. Visual = diagram/flowchart/image-dominant, where the
	extractable text is too sparse to use as ground truth."""
	nchars = len(page.get_text("text").strip())
	n_images = len(page.get_images())
	try:
		n_drawings = len(page.get_drawings())
	except Exception:
		n_drawings = 0
	if nchars < config.VISUAL_MIN_CHARS and (n_images > 0 or n_drawings >= config.VISUAL_MIN_DRAWINGS):
		return "visual"
	return "text"


def render_png(page, dpi: int = config.RENDER_DPI) -> bytes:
	"""Render a page to PNG bytes at the given DPI."""
	zoom = dpi / 72.0
	matrix = fitz.Matrix(zoom, zoom)
	return page.get_pixmap(matrix=matrix).tobytes("png")


def page_count(pdf_path: str | Path) -> int:
	with fitz.open(pdf_path) as doc:
		return doc.page_count


def get_toc(pdf_path: str | Path) -> list[tuple[int, str, int]]:
	"""Embedded outline: list of (level, title, page_no). Empty if none."""
	with fitz.open(pdf_path) as doc:
		return [(lvl, title, page) for lvl, title, page in doc.get_toc()]
