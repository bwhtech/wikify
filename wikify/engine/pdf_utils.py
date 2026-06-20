"""PDF rendering + page classification via PyMuPDF.

Ported from the POC `pdf_utils.py`. The one I/O change: instead of writing PNGs
to disk under `storage/pages/`, `render_png` returns the PNG bytes and `store.py`
persists them as Frappe **File** docs attached to each Source Page.
"""

from __future__ import annotations

import base64
from pathlib import Path

import fitz  # PyMuPDF

from wikify.engine import config


def png_to_data_url(png_bytes: bytes) -> str:
	"""Inline a rendered page PNG as a data URL for the judge/VLM image input.

	The POC read a PNG off disk (`image_to_data_url`); here the bytes are already in
	hand from `render_png`, so we encode them directly — no File round-trip.
	"""
	b64 = base64.b64encode(png_bytes).decode("ascii")
	return f"data:image/png;base64,{b64}"


def classify_page(
	page, min_chars: int = config.VISUAL_MIN_CHARS, min_drawings: int = config.VISUAL_MIN_DRAWINGS
) -> str:
	"""Heuristic page type. Visual = diagram/flowchart/image-dominant, where the
	extractable text is too sparse to use as ground truth. Thresholds default to the
	`engine.config` constants; the live pipeline passes the `Wikify Settings` values."""
	nchars = len(page.get_text("text").strip())
	n_images = len(page.get_images())
	try:
		n_drawings = len(page.get_drawings())
	except Exception:
		n_drawings = 0
	if nchars < min_chars and (n_images > 0 or n_drawings >= min_drawings):
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
