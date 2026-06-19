"""Baseline parser: pymupdf4llm. Fast, local, no API key — the floor every other
parser must beat. Ported verbatim from the POC `parsers/pymupdf_parser.py`."""

from __future__ import annotations

import pymupdf4llm

NAME = "pymupdf4llm"


def parse_page(pdf_path: str, page_no: int) -> str:
	"""Baseline markdown for a single 1-based page (pymupdf4llm uses 0-based)."""
	return pymupdf4llm.to_markdown(pdf_path, pages=[page_no - 1], show_progress=False)
