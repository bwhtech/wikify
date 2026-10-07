"""Strip repeated page headers/footers before sectionizing.

Real manuals repeat a running header/footer on every page (doc title, doc code,
"Pg X of Y", version/date). Left in, each becomes a fake heading. We remove the run of
lines at each page edge that recur at the edges of many pages, plus a few
varying-boilerplate patterns (page numbers, doc codes) that won't match exactly page-to-page.

Ported verbatim from the POC `loader/cleanup.py` (pure markdown, no I/O). Wired into
the pipeline at sectionize time (Slice 4); shipped here per the Slice 3 cleanup port.
"""

from __future__ import annotations

import re
from collections import Counter
from itertools import groupby

from wikify.engine.loader.table_stitch import (
	html_tables_to_markdown,
	merge_continuation_rows,
	stitch_cross_page_tables,
)

_NORM = re.compile(r"[#*_`>|\-\s]+")
_MARKUP = re.compile(r"(?:<br\s*/?>|[*_`|]|^[\s>#]+)")
_PAGE_OF = [
	re.compile(r"(?i)\bpg\.?\s*\d+\s*of\s*\d+"),
	re.compile(r"(?i)\bpage\s*\d+\s*of\s*\d+"),
]
_HEADER_FIELDS = [
	re.compile(r"(?i)^man/[a-z0-9/]+"),
	re.compile(r"(?i)\bver\.?\s*:"),
	re.compile(r"(?i)\bissue\s*:\s*\d"),
	re.compile(r"(?i)^\s*date\s*:"),
]
_STRUCTURAL_LINE = re.compile(r"^\s*(?:<|```|~~~|!\[)")
_IMAGE_LINE = re.compile(r"^\s*!\[[^\]]*\]\([^)]*\)\s*$")
EDGE_LINES = 4

# Approval / sign-off footer block — QMS-manual page furniture rendered as a one- or
# two-row Markdown table, e.g. `|Prepared by - Dr X|Issued by: QMC|Approved by - Dr Y|`.
# It recurs per page but as a long, pipe-laden, per-page-varying row, so the recurrence
# + 90-char boilerplate rule misses it. Matched structurally instead: a table row
# carrying >=2 distinct sign-off phrases (a lone "approved by" in a data row is kept).
_SEP_ONLY = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_SIGNOFF = ("prepared by", "issued by", "approved by", "reviewed by", "authorized by")
_SIGNOFF_LABEL = re.compile(r"(?i)\b(prepared|issued|approved|reviewed|authori[sz]ed) by\s*[:\-\u2013]")
_SIGNOFF_LINE = re.compile(r"(?i)^[*_\s]*(prepared|issued|approved|reviewed|authori[sz]ed) by\s*[:\-\u2013]")
_PAGE_NUMBER = re.compile(r"^\s*\d{1,4}\s*$")
_SENTENCE_START = re.compile(r"^[a-z]")
# A sentence cut by a page break ran to the page edge, so its last line is never a short label.
_MIN_BROKEN_LINE_LENGTH = 40
BOILERPLATE_MAX_PAGES = 10
# A numbered heading repeated as a running sub-header is still the real section start on its first
# page; the sectionizer folds the repeats, so stripping them here would lose the section itself.
_NUMBERED_HEADING = re.compile(r"^\s*#{1,6}\s+[*_]*\d+(?:\.\d+)*\.?\s*[A-Za-z]")
_LIST_ITEM = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+\S")
_ENUMERATED_TEXT = re.compile(r"^(\d+)[.)]\s")


def _plain(line: str) -> str:
	"""The line's visible text: emphasis, table pipes, <br>, blockquote and heading markers removed."""
	return " ".join(_MARKUP.sub(" ", line).split())


def _norm(line: str) -> str:
	return _NORM.sub(" ", _plain(line)).strip().lower()


def _is_signoff_footer_row(line: str) -> bool:
	s = line.strip()
	if not (s.startswith("|") and s.endswith("|")):
		return len({label.lower() for label in _SIGNOFF_LABEL.findall(s)}) >= 2
	low = s.lower()
	return sum(kw in low for kw in _SIGNOFF) >= 2


def _signoff_line_runs(lines: list[str]) -> set[int]:
	"""Sign-off labels laid out one per paragraph, e.g. "Prepared by: …" then "Issued by: …"."""
	drop: set[int] = set()
	non_blank = [index for index, line in enumerate(lines) if line.strip()]
	for is_signoff, group in groupby(non_blank, key=lambda index: bool(_SIGNOFF_LINE.match(lines[index]))):
		run = list(group)
		if is_signoff and len({_SIGNOFF_LINE.match(lines[index]).group(1).lower() for index in run}) >= 2:
			drop.update(run)
	return drop


def _strip_footer_blocks(md: str) -> str:
	"""Drop sign-off footer rows and any separator row orphaned next to them."""
	lines = md.splitlines()
	drop = _signoff_line_runs(lines)
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
	while kept and not kept[0].strip():
		kept.pop(0)
	if kept and _PAGE_NUMBER.match(_plain(kept[-1])):
		kept.pop()
	return "\n".join(kept)


def _edge_lines(md: str) -> list[str]:
	content = [line for line in md.splitlines() if _norm(line) and not _STRUCTURAL_LINE.match(line)]
	return content[:EDGE_LINES] + content[-EDGE_LINES:]


def find_boilerplate(pages: list[tuple[int, str]]) -> set[str]:
	"""Normalized lines that recur at the top or bottom edge of a large fraction of pages."""
	counts: Counter[str] = Counter()
	for _, md in pages:
		for nl in {_norm(line) for line in _edge_lines(md)}:
			counts[nl] += 1
	# Capped so a header that alternates between styles across a long document still counts.
	threshold = max(3, min(int(0.30 * len(pages)), BOILERPLATE_MAX_PAGES))
	return {line for line, c in counts.items() if c >= threshold and len(line) <= 90}


def _is_page_of(line: str) -> bool:
	plain = _plain(line)
	return any(pattern.search(plain) for pattern in _PAGE_OF)


def _is_page_furniture(line: str, boilerplate: set[str], at_top: bool) -> bool:
	plain = _plain(line)
	return (
		(_norm(line) in boilerplate and not _NUMBERED_HEADING.match(line))
		or any(pattern.search(plain) for pattern in _HEADER_FIELDS)
		or bool(_PAGE_NUMBER.match(plain))
		or (at_top and "-" in line and bool(_SEP_ONLY.match(line)))
	)


def _edge_furniture(lines: list[str], boilerplate: set[str]) -> set[int]:
	"""The unbroken header/footer run at each page edge; a label that also recurs mid-page survives."""
	drop: set[int] = set()
	for at_top, indexes in ((True, range(len(lines))), (False, range(len(lines) - 1, -1, -1))):
		for index in indexes:
			line = lines[index]
			if not line.strip() or _IMAGE_LINE.match(line):
				continue
			if not _is_page_furniture(line, boilerplate, at_top):
				break
			drop.add(index)
	return drop


def strip_boilerplate(pages: list[tuple[int, str]], boilerplate: set[str]) -> list[tuple[int, str]]:
	out: list[tuple[int, str]] = []
	for pno, md in pages:
		lines = [line for line in _strip_footer_blocks(md).splitlines() if not _is_page_of(line)]
		drop = _edge_furniture(lines, boilerplate)
		kept = [line for index, line in enumerate(lines) if index not in drop]
		out.append((pno, _strip_page_residue("\n".join(kept))))
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


def list_kind(marker: str) -> str:
	return "bullet" if marker in "-*+" else "ordered"


def last_list_item(lines: list[str]) -> re.Match | None:
	for line in reversed(lines):
		if not line.strip():
			continue
		item = _LIST_ITEM.match(line)
		if item or not line[0].isspace():
			return item
	return None


def continuation_prefix(item: re.Match, first_line: str) -> str:
	"""What a list cut by a page break needs in front of its items on the next page, where it restarts
	at the margin: the nesting it had, or the bullet it had when the bullets carried their own numbers."""
	follow = _LIST_ITEM.match(first_line)
	if not follow or follow.group(1):
		return ""
	indent, marker = item.group(1), item.group(2)
	if indent and list_kind(marker) == list_kind(follow.group(2)):
		return indent
	numbered_text = _ENUMERATED_TEXT.match(item.string[item.end(2) :].lstrip())
	if (
		list_kind(marker) == "bullet"
		and list_kind(follow.group(2)) == "ordered"
		and numbered_text
		and int(follow.group(2)[:-1]) == int(numbered_text.group(1)) + 1
	):
		return f"{indent}{marker} "
	return ""


def reindent_list_continuations(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
	"""Put a list continued across a page break back under the item it belongs to, up to the first
	block that is not part of the list."""
	page_lines = [md.splitlines() for _, md in pages]
	for index in range(1, len(pages)):
		if pages[index][0] != pages[index - 1][0] + 1:
			continue
		item = last_list_item(page_lines[index - 1])
		lines = page_lines[index]
		first = next((line for line in lines if line.strip()), "")
		prefix = continuation_prefix(item, first) if item else ""
		if not prefix:
			continue
		kind = list_kind(_LIST_ITEM.match(first).group(2))
		for line_index, line in enumerate(lines):
			if not line.strip():
				continue
			if line[0].isspace():
				lines[line_index] = " " * len(prefix) + line
				continue
			follow = _LIST_ITEM.match(line)
			if not follow or list_kind(follow.group(2)) != kind:
				break
			lines[line_index] = prefix + line
	return [(page_no, "\n".join(lines)) for (page_no, _), lines in zip(pages, page_lines, strict=True)]


def clean_pages(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
	pages = [(page_no, html_tables_to_markdown(md)) for page_no, md in pages]
	stitched = stitch_cross_page_tables(strip_boilerplate(pages, find_boilerplate(pages)))
	joined = join_page_breaks([(page_no, merge_continuation_rows(md)) for page_no, md in stitched])
	return reindent_list_continuations(joined)


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
