from __future__ import annotations

import re


def find_tag_spans(markdown: str, caption: str) -> list[tuple[int, int]]:
	pattern = re.compile(r"!\[" + re.escape(caption) + r"\]\([^)]*\)")
	return [match.span() for match in pattern.finditer(markdown or "")]
