"""Strip repeated page headers/footers before sectionizing.

Real manuals repeat a running header/footer on every page (doc title, doc code,
"Pg X of Y", version/date). Left in, each becomes a fake heading. We remove lines
that recur across many pages, plus a few varying-boilerplate patterns (page
numbers, doc codes) that won't match exactly page-to-page.

Ported verbatim from the POC `loader/cleanup.py` (pure markdown, no I/O). Wired into
the pipeline at sectionize time (Slice 4); shipped here per the Slice 3 cleanup port.
"""

from __future__ import annotations

import re
from collections import Counter

_NORM = re.compile(r"[#*_`>\-\s]+")
_VARYING = [
	re.compile(r"(?i)\bpg\.?\s*\d+\s*of\s*\d+"),
	re.compile(r"(?i)\bpage\s*\d+\s*of\s*\d+"),
	re.compile(r"(?i)^man/[a-z0-9/]+"),
	re.compile(r"(?i)\bver\.?\s*:"),
	re.compile(r"(?i)\bissue\s*:\s*\d"),
	re.compile(r"(?i)^\s*date\s*:"),
]

# Approval / sign-off footer block — QMS-manual page furniture rendered as a one- or
# two-row Markdown table, e.g. `|Prepared by - Dr X|Issued by: QMC|Approved by - Dr Y|`.
# It recurs per page but as a long, pipe-laden, per-page-varying row, so the recurrence
# + 90-char boilerplate rule misses it. Matched structurally instead: a table row
# carrying >=2 distinct sign-off phrases (a lone "approved by" in a data row is kept).
_SEP_ONLY = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_SIGNOFF = ("prepared by", "issued by", "approved by", "reviewed by", "authorized by")
_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
_SENTENCE_START = re.compile(r"^[a-z]")
# A sentence cut by a page break ran to the page edge, so its last line is never a short label.
_MIN_BROKEN_LINE_LENGTH = 40


def _norm(line: str) -> str:
	return _NORM.sub(" ", line).strip().lower()


def _is_signoff_footer_row(line: str) -> bool:
	s = line.strip()
	if not (s.startswith("|") and s.endswith("|")):
		return False
	low = s.lower()
	return sum(kw in low for kw in _SIGNOFF) >= 2


def _strip_footer_blocks(md: str) -> str:
	"""Drop sign-off footer rows and any separator row orphaned next to them."""
	lines = md.splitlines()
	drop: set[int] = set()
	for i, line in enumerate(lines):
		if _is_signoff_footer_row(line):
			drop.add(i)
			for j in (i - 1, i + 1):  # absorb the |---|---| separator above/below it
				if 0 <= j < len(lines) and _SEP_ONLY.match(lines[j]):
					drop.add(j)
	if not drop:
		return md
	return "\n".join(line for k, line in enumerate(lines) if k not in drop)


def _is_table_row(line: str) -> bool:
	return line.strip().startswith("|")


def _strip_page_residue(md: str) -> str:
	"""Drop separator rows left headless by a stripped header box, and the closing page number."""
	lines = md.splitlines()
	kept = [
		line
		for index, line in enumerate(lines)
		if not (_SEP_ONLY.match(line) and "-" in line and not (index and _is_table_row(lines[index - 1])))
	]
	while kept and not kept[-1].strip():
		kept.pop()
	if kept and _PAGE_NUMBER.match(kept[-1]):
		kept.pop()
	return "\n".join(kept)


def find_boilerplate(pages: list[tuple[int, str]]) -> set[str]:
	"""Normalized lines that recur on a large fraction of pages."""
	counts: Counter[str] = Counter()
	for _, md in pages:
		for nl in {_norm(line) for line in md.splitlines() if _norm(line)}:
			counts[nl] += 1
	threshold = max(3, int(0.30 * len(pages)))
	return {line for line, c in counts.items() if c >= threshold and len(line) <= 90}


def _is_varying(line: str) -> bool:
	return any(p.search(line) for p in _VARYING)


def strip_boilerplate(pages: list[tuple[int, str]], boilerplate: set[str]) -> list[tuple[int, str]]:
	out: list[tuple[int, str]] = []
	for pno, md in pages:
		kept = [
			line
			for line in md.splitlines()
			if not (_norm(line) and _norm(line) in boilerplate) and not _is_varying(line)
		]
		out.append((pno, _strip_page_residue(_strip_footer_blocks("\n".join(kept)))))
	return out


def _ends_mid_sentence(line: str) -> bool:
	text = line.strip()
	return (
		len(text) >= _MIN_BROKEN_LINE_LENGTH
		and text[0] not in "#|<"
		and (text[-1].isalnum() or text[-1] == ",")
	)


def join_page_breaks(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
	"""Rejoin a sentence the PDF split across a page break onto the page it started on."""
	page_lines = [md.splitlines() for _, md in pages]
	for index in range(len(pages) - 1):
		if pages[index + 1][0] != pages[index][0] + 1:
			continue
		lines, next_lines = page_lines[index], page_lines[index + 1]
		last = next((i for i in range(len(lines) - 1, -1, -1) if lines[i].strip()), None)
		first = next((i for i, line in enumerate(next_lines) if line.strip()), None)
		if last is None or first is None:
			continue
		if _ends_mid_sentence(lines[last]) and _SENTENCE_START.match(next_lines[first].strip()):
			lines[last] = f"{lines[last].rstrip()} {next_lines.pop(first).strip()}"
	return [(page_no, "\n".join(lines)) for (page_no, _), lines in zip(pages, page_lines, strict=True)]


def clean_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
	return join_page_breaks(strip_boilerplate(pages, find_boilerplate(pages)))


def strip_outer_markdown_fence(text: str) -> str:
	"""Unwrap a reply an LLM fenced as one ```markdown … ``` block (with optional
	commentary around it), returning just the inner markdown.

	Models sometimes ignore "no code fences" and fence the whole page — often adding a
	trailing "The table is part of…" note — so real tables/headings render as a literal
	code block. We unwrap only when the first non-blank line opens a markdown/md (or
	untagged) fence and the block has no nested fence, so a genuine ```mermaid diagram
	is left untouched.
	"""
	lines = text.strip().splitlines()
	start = next((i for i, line in enumerate(lines) if line.strip()), None)
	if start is None or not lines[start].startswith("```"):
		return text
	if lines[start][3:].strip().lower() not in ("", "markdown", "md"):
		return text
	close = next((i for i in range(start + 1, len(lines)) if set(lines[i].strip()) == {"`"}), None)
	if close is None:
		return text
	# A nested fence inside the block (e.g. ```mermaid) means unwrapping could corrupt it.
	if any(lines[i].lstrip().startswith("```") for i in range(start + 1, close)):
		return text
	return "\n".join(lines[start + 1 : close]).strip()
