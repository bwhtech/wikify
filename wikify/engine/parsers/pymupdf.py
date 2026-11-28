"""Baseline parser: pymupdf4llm. Fast, local, no API key — the floor every other
parser must beat. Ported verbatim from the POC `parsers/pymupdf_parser.py`."""

from __future__ import annotations

import re

import pymupdf4llm

NAME = "pymupdf4llm"

_MARKER_RUN = re.compile(r"(?:\*\*|~~|`|_)+")
_MARKER = re.compile(r"\*\*|~~|`|_")
_TOKEN_END = re.compile(r"[^\s*_~`]+$")
_TOKEN_START = re.compile(r"[^\s*_~`]+")
_SPACES = re.compile(r"\s+")


def parse_page(pdf_path: str, page_no: int, page_text: str = "") -> str:
	"""Baseline markdown for a single 1-based page (pymupdf4llm uses 0-based)."""
	markdown = pymupdf4llm.to_markdown(pdf_path, pages=[page_no - 1], show_progress=False)
	return restore_cell_spaces(markdown, page_text) if page_text else markdown


def restore_cell_spaces(markdown: str, page_text: str) -> str:
	"""pymupdf4llm (2.2 and older) strips the spaces around bold and italic spans in table
	cells, so "angry **with** a" comes out as "angry**with**a". Put a space back wherever
	the page's own text has one."""
	text = _SPACES.sub(" ", page_text)
	lines = markdown.split("\n")
	for index, line in enumerate(lines):
		if line.lstrip().startswith("|"):
			lines[index] = "|".join(_restore_cell(cell, text) for cell in line.split("|"))
	return "\n".join(lines)


def _restore_cell(cell: str, text: str) -> str:
	out, last, opened = [], 0, set()
	for run in _MARKER_RUN.finditer(cell):
		markers = _MARKER.findall(run.group())
		# A run closes spans first and then opens new ones; the space goes between the two.
		closers, opening = 0, False
		for marker in markers:
			if marker in opened:
				opened.discard(marker)
				if not opening:
					closers += 1
			else:
				opened.add(marker)
				opening = True
		start, end = run.span()
		left = _TOKEN_END.search(cell[:start])
		right = _TOKEN_START.match(cell, end)
		if not (left and right):
			continue
		left, right = left.group(), right.group()
		if f"{left} {right}" in text and f"{left}{right}" not in text:
			split = start + len("".join(markers[:closers]))
			out.append(cell[last:split] + " ")
			last = split
	out.append(cell[last:])
	return "".join(out)
