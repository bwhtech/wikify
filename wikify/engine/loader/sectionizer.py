from __future__ import annotations

import re
from collections import defaultdict
from dataclasses import dataclass

from wikify.engine.loader.toc import correct_level

MAX_TITLE_LENGTH = 140

RUNNING_HEADER_MIN_PAGES = 3
TOP_OF_PAGE_LINES = 3

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
_LEADING_NUM = re.compile(r"^(\d+)\b")
_DOUBLE_NUM = re.compile(r"^\d+\.\s+\d")
_GLUED_NUM = re.compile(r"^(\d+(?:\.\d+)+\.?)(?=[A-Z])")
_BOLD_LINE = re.compile(r"^\*\*([^*]+)\*\*$")


def _clean_title(raw: str) -> str:
	title = _GLUED_NUM.sub(r"\1 ", raw.strip().strip("*_").strip())
	if title.endswith(".") and not title.endswith(".."):
		title = title[:-1].rstrip()
	if len(title) <= MAX_TITLE_LENGTH:
		return title
	clipped = title[: MAX_TITLE_LENGTH - 1]
	head, _, _ = clipped.rpartition(" ")
	return f"{(head or clipped).rstrip()}…"


def _infer_level(title: str, fallback: int) -> int:
	m = _NUM_RE.match(title)
	if m:
		return min(6, m.group(1).count(".") + 1)
	return fallback


def _chapter_num(title: str) -> int | None:
	m = _LEADING_NUM.match(title)
	return int(m.group(1)) if m else None


def _looks_like_list_item(title: str) -> bool:
	return "*" in title or bool(_DOUBLE_NUM.match(title))


def _section_number(title: str) -> tuple[int, ...] | None:
	m = _NUM_RE.match(title)
	return tuple(int(part) for part in m.group(1).split(".")) if m else None


def _title_words(title: str) -> list[str]:
	m = _NUM_RE.match(title)
	text = title[m.end(1) :] if m else title
	return [word.removesuffix("s") for word in re.findall(r"[a-z0-9]+", text.lower())]


def _repeats_open_section(title: str, stack: list[tuple[int, str, bool]]) -> bool:
	number = _section_number(title)
	if number is None:
		return False
	words = _title_words(title)
	return any(
		numbered
		and _section_number(open_title)[: len(number)] == number
		and _title_words(open_title) == words
		for _, open_title, numbered in stack
	)


def _continues_numbering(number: tuple[int, ...], last_number: tuple[int, ...]) -> bool:
	if number == (*last_number, 1):
		return True
	depth = len(number)
	return (
		depth <= len(last_number)
		and number[:-1] == last_number[: depth - 1]
		and number[-1] == last_number[depth - 1] + 1
	)


def _promote_numbered_bold_line(line: str, last_number: tuple[int, ...]) -> str:
	bold = _BOLD_LINE.match(line.strip())
	if not (bold and last_number):
		return line
	title = bold.group(1).strip()
	number = _section_number(title)
	if number is None or not _continues_numbering(number, last_number):
		return line
	return f"{'#' * min(6, len(number))} {title}"


def running_header_titles(pages: list[tuple[int, str]]) -> set[str]:
	occurrences: dict[str, list[int]] = defaultdict(list)
	pages_seen: dict[str, set[int]] = defaultdict(set)
	for page_no, md in pages:
		for position, line in enumerate(line for line in md.splitlines() if line.strip()):
			match = _HEADING_RE.match(line)
			if not match:
				continue
			title = _clean_title(match.group(2))
			occurrences[title].append(position)
			pages_seen[title].add(page_no)
	return {
		title
		for title, positions in occurrences.items()
		if len(pages_seen[title]) >= RUNNING_HEADER_MIN_PAGES
		and sum(1 for position in positions if position < TOP_OF_PAGE_LINES) * 2 >= len(positions)
	}


@dataclass
class Section:
	title: str
	level: int
	hierarchy_path: list[str]
	page_start: int
	page_end: int
	markdown: str = ""
	section_type: str | None = None


def sectionize(pages: list[tuple[int, str]], level_map: dict[str, int] | None = None) -> list[Section]:  # noqa: C901
	level_map = level_map or {}
	sections: list[Section] = []
	stack: list[tuple[int, str, bool]] = []
	current: Section | None = None
	buf: list[str] = []
	max_chapter = 0
	capital_chapters = True
	last_list_item = (0, 0)
	last_number: tuple[int, ...] = ()
	running_headers = running_header_titles(pages)
	opened_headers: set[str] = set()

	def flush():
		if current is not None:
			current.markdown = "\n".join(buf).strip()
			sections.append(current)

	for page_no, md in pages:
		for line in md.splitlines():
			m = _HEADING_RE.match(_promote_numbered_bold_line(line, last_number))
			if m:
				title = _clean_title(m.group(2))
				if _repeats_open_section(title, stack):
					if current is not None:
						current.page_end = page_no
					continue
				if title in running_headers:
					if title in opened_headers:
						if current is not None:
							current.page_end = page_no
						continue
					opened_headers.add(title)
				flush()
				buf = []
				level = correct_level(title, _infer_level(title, len(m.group(1))), level_map)
				demoted = False
				if level == 1 and title not in level_map:
					cnum = _chapter_num(title)
					if cnum is not None:
						if (
							_looks_like_list_item(title)
							or cnum <= max_chapter
							or (last_number and cnum <= last_number[0])
							or (max_chapter and capital_chapters and not title.isupper())
							or (page_no, cnum - 1) == last_list_item
						):
							level = 2
							demoted = True
							last_list_item = (page_no, cnum)
						else:
							max_chapter = cnum
							capital_chapters = capital_chapters and title.isupper()
				if title not in level_map and (demoted or not _NUM_RE.match(title)):
					anchor = next((lvl for lvl, _, numbered in reversed(stack) if numbered), None)
					if anchor is not None:
						level = min(6, max(level, anchor + 1))
				numbered_heading = not demoted and bool(_NUM_RE.match(title))
				while stack and (stack[-1][0] >= level or (numbered_heading and not stack[-1][2])):
					stack.pop()
				stack.append((level, title, numbered_heading))
				if numbered_heading:
					last_number = _section_number(title)
				current = Section(
					title=title,
					level=level,
					hierarchy_path=[t for _, t, _ in stack],
					page_start=page_no,
					page_end=page_no,
				)
			else:
				if current is None and line.strip():
					current = Section(
						title="Preamble",
						level=1,
						hierarchy_path=["Preamble"],
						page_start=page_no,
						page_end=page_no,
					)
				if current is not None:
					current.page_end = page_no
				buf.append(line)

	flush()

	merged: list[Section] = []
	numbered: dict[str, Section] = {}
	for sec in sections:
		key = sec.title.lower()
		is_numbered = bool(_NUM_RE.match(sec.title))
		prev = merged[-1] if merged else None
		if is_numbered and key in numbered:
			tgt = numbered[key]
			tgt.page_end = max(tgt.page_end, sec.page_end)
			tgt.markdown = (tgt.markdown + "\n" + sec.markdown).strip()
		elif prev and prev.level == sec.level and prev.title.lower() == key:
			prev.page_end = max(prev.page_end, sec.page_end)
			prev.markdown = (prev.markdown + "\n" + sec.markdown).strip()
		else:
			merged.append(sec)
			if is_numbered:
				numbered[key] = sec
	return merged
