"""Heading authority from the embedded PDF outline.

The embedded outline (`doc.get_toc`) is the author's real heading hierarchy — the
weakness every parser shares. We use it to correct heading levels when a section
title matches an outline entry.

Ported verbatim from the POC `loader/toc.py`; the only change is the import path
for `get_toc` (now `wikify.engine.pdf_utils`).
"""

from __future__ import annotations

import re

from wikify.engine.pdf_utils import get_toc


def _norm(s: str) -> str:
	return re.sub(r"\s+", " ", s).strip().lower()


def toc_level_map(pdf_path: str) -> dict[str, int]:
	"""title (normalized) -> outline level (1-based). Empty if no outline."""
	return {_norm(title): lvl for lvl, title, _page in get_toc(pdf_path)}


def correct_level(title: str, parsed_level: int, level_map: dict[str, int]) -> int:
	return level_map.get(_norm(title), parsed_level)
