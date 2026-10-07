from __future__ import annotations

import re
from bisect import bisect_right
from collections import defaultdict
from dataclasses import dataclass
from itertools import pairwise

from wikify.engine.loader.cleanup import _SEP_ONLY
from wikify.engine.loader.toc import correct_level

MAX_TITLE_LENGTH = 140
PREAMBLE_TITLE = "Preamble"

RUNNING_HEADER_MIN_PAGES = 3
TOP_OF_PAGE_LINES = 3

_HEADING_RE = re.compile(r"^(#{1,6})\s+(.*\S)\s*$")
_NUM_RE = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+\S")
_LEADING_NUM = re.compile(r"^(\d+)\b")
_DOUBLE_NUM = re.compile(r"^\d+\.\s+\d")
_GLUED_NUM = re.compile(r"^(\d+(?:\.\d+)+\.?)(?=[A-Z])")
_BOLD_LINE = re.compile(r"^\*\*([^*]+)\*\*$")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_HTML_TAG = re.compile(r"</?[A-Za-z][^>]*>")
_LETTERED_NUM = re.compile(r"^(\d+(?:\.\d+)+)\.[a-z](?![a-z])\s*")
_TOC_NUMBERED = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.*?)(?:\s*\.{3,})?(?:\s+\d{1,4})?$")
_CONTENTS_TITLE = re.compile(r"(?i)^(?:table of )?contents$|^index$")
_TOC_ENTRY = re.compile(
	r"^(?:[-*+]\s+)?\|?\s*\d+(?:\.\d+)*\.?\s*\|?\s*[A-Za-z(].*?(?:\.{3,}|\s[-\u2013\u2014]\s|\||<br>|\s)\s*(\d{1,4})\s*\|?$"
)
_TOC_LEADER = re.compile(r"[A-Za-z].*\.{4,}\s*(\d{1,4})$")
TOC_MIN_ENTRIES = 5
TOC_MIN_ENTRY_SHARE = 0.6
TOC_MIN_ASCENDING_SHARE = 0.75


def _clean_title(raw: str) -> str:
	text = _HTML_TAG.sub("", raw).replace("**", "").strip().strip("*_").strip()
	title = _GLUED_NUM.sub(r"\1 ", _LINK.sub(r"\1", text).strip())
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


def _is_label(title: str, level_map: dict[str, int]) -> bool:
	return (
		title not in level_map and not _NUM_RE.match(title) and (title[:1].islower() or title.endswith(":"))
	)


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


def extends_number(number: tuple[int, ...] | None, prefix: tuple[int, ...]) -> bool:
	return bool(number) and len(number) > len(prefix) and number[: len(prefix)] == prefix


def dotted_heading_positions(pages: list[tuple[int, str]]) -> list[tuple[int, int, tuple[int, ...]]]:
	positions = []
	for page_index, (_, md) in enumerate(pages):
		for line_index, line in enumerate(md.splitlines()):
			match = _HEADING_RE.match(line)
			number = _section_number(_clean_title(match.group(2))) if match else None
			if number and len(number) > 1:
				positions.append((page_index, line_index, number))
	return positions


def opens_next_chapter(
	chapter: int, last_number: tuple[int, ...], next_dotted: tuple[int, ...] | None, title: str
) -> bool:
	"""Inside a numbered sub-section, "4." is a list item unless it is the next chapter: the next dotted
	heading no longer belongs to the current chapter, or, with none left to read, it is set in capitals
	like a chapter title."""
	if chapter != last_number[0] + 1:
		return False
	return next_dotted[0] >= chapter if next_dotted else title.isupper()


def adopt_preceding_descendants(
	sections: list[Section],
	current: Section | None,
	stack: list[tuple[int, str, bool]],
	title: str,
	level: int,
	page_no: int,
) -> None:
	"""A parent heading that resurfaces after its sub-sections were read (an excerpt that starts
	mid-section) becomes their group instead of a new section after them."""
	number = _section_number(title)

	def descends(section: Section) -> bool:
		return any(
			extends_number(_section_number(path_title), number) for path_title in section.hierarchy_path
		)

	run = [*sections, current] if current else list(sections)
	start = len(run)
	while start and descends(run[start - 1]):
		start -= 1
	if start == len(run):
		return
	for section in run[start:]:
		depth = next(
			index
			for index, path_title in enumerate(section.hierarchy_path)
			if extends_number(_section_number(path_title), number)
		)
		section.hierarchy_path.insert(depth, title)
	group_path = run[start].hierarchy_path[: run[start].hierarchy_path.index(title) + 1]
	sections.insert(
		start,
		Section(
			title=title,
			level=level,
			hierarchy_path=list(group_path),
			page_start=run[start].page_start,
			page_end=page_no,
		),
	)
	stack_index = next(
		(
			index
			for index, (_, open_title, numbered) in enumerate(stack)
			if numbered and extends_number(_section_number(open_title), number)
		),
		len(stack),
	)
	stack.insert(stack_index, (level, title, True))


def _continues_numbering(number: tuple[int, ...], last_number: tuple[int, ...]) -> bool:
	if number == (*last_number, 1):
		return True
	depth = len(number)
	return (
		depth <= len(last_number)
		and number[:-1] == last_number[: depth - 1]
		and number[-1] == last_number[depth - 1] + 1
	)


def _promote_numbered_bold_line(
	line: str, last_number: tuple[int, ...], next_dotted: tuple[int, ...] | None
) -> str:
	bold = _BOLD_LINE.match(line.strip())
	if not (bold and last_number):
		return line
	title = bold.group(1).strip()
	number = _section_number(title)
	if number is None or not _continues_numbering(number, last_number):
		return line
	if (
		len(number) == 1
		and len(last_number) > 1
		and not opens_next_chapter(number[0], last_number, next_dotted, title)
	):
		return line
	return f"{'#' * min(6, len(number))} {title}"


def next_dotted_number(
	dotted_headings: list[tuple[int, int, tuple[int, ...]]], page_index: int, line_index: int
) -> tuple[int, ...] | None:
	index = bisect_right(dotted_headings, (page_index, line_index), key=lambda heading: heading[:2])
	return dotted_headings[index][2] if index < len(dotted_headings) else None


def correct_chapter_typo(title: str, last_number: tuple[int, ...]) -> str:
	"""A deep number whose chapter jumps while its tail carries on the current numbering ("1.5.3.1.5"
	after 6.5.3.1.4) is a misprint of the current chapter."""
	match = _NUM_RE.match(title)
	if not (match and last_number):
		return title
	number = tuple(int(part) for part in match.group(1).split("."))
	corrected = (last_number[0], *number[1:])
	if len(number) < 3 or number[0] == last_number[0] or not _continues_numbering(corrected, last_number):
		return title
	return ".".join(str(part) for part in corrected) + title[match.end(1) :]


def toc_section_titles(pages: list[tuple[int, str]]) -> dict[tuple[int, ...], str]:
	titles: dict[tuple[int, ...], str] = {}
	for _, md in pages:
		lines = md.splitlines()
		for line in lines[: toc_end_line(lines) + 1]:
			text = " ".join(_HTML_TAG.sub(" ", line).replace("|", " ").split()).lstrip("-*+ ")
			entry = _TOC_NUMBERED.match(text)
			if entry and entry.group(2):
				number = tuple(int(part) for part in entry.group(1).split("."))
				titles.setdefault(number, _clean_title(f"{entry.group(1)} {entry.group(2)}"))
	return titles


def recurring_section_titles(pages: list[tuple[int, str]]) -> dict[tuple[int, ...], list[str]]:
	"""Numbered heading titles printed on more than one page, in reading order: running headers that
	name a section even where the body never opens it."""
	pages_seen: dict[str, set[int]] = defaultdict(set)
	for page_no, md in pages:
		for line in md.splitlines():
			match = _HEADING_RE.match(line)
			if match:
				pages_seen[_clean_title(match.group(2))].add(page_no)
	recurring: dict[tuple[int, ...], list[str]] = defaultdict(list)
	for title, seen in pages_seen.items():
		number = _section_number(title)
		if number and len(seen) > 1:
			recurring[number].append(title)
	return recurring


def repeats_running_header(
	title: str, stack: list[tuple[int, str, bool]], recurring: dict[tuple[int, ...], list[str]]
) -> bool:
	"""A lettered number ("6.2.1.b POLICIES…") under its open section 6.2.1 is that section's running
	header "6.2.1 POLICIES…" printed with a suffix."""
	lettered = _LETTERED_NUM.match(title)
	if not lettered:
		return False
	base = tuple(int(part) for part in lettered.group(1).split("."))
	words = _title_words(title[lettered.end() :])
	return any(numbered and _section_number(open_title) == base for _, open_title, numbered in stack) and any(
		_title_words(running) == words for running in recurring.get(base, ())
	)


def missing_ancestors(
	number: tuple[int, ...], stack: list[tuple[int, str, bool]], last_at_depth: dict[int, tuple[int, ...]]
) -> list[tuple[int, ...]]:
	"""Numeric ancestors of a heading that the body never opened, where the numbering shows they belong:
	the last number read at that depth is the ancestor's predecessor (6.1 after chapter 5)."""
	deepest = max(
		(len(_section_number(open_title)) for _, open_title, numbered in stack if numbered), default=0
	)
	missing = []
	for depth in range(deepest + 1, len(number)):
		prefix = number[:depth]
		previous = last_at_depth.get(depth)
		if previous and previous[:-1] == prefix[:-1] and previous[-1] + 1 == prefix[-1]:
			missing.append(prefix)
	return missing


def ancestor_title(
	prefix: tuple[int, ...],
	recurring: dict[tuple[int, ...], list[str]],
	toc_titles: dict[tuple[int, ...], str],
	child_title: str,
) -> str:
	if recurring.get(prefix):
		return recurring[prefix][0]
	if prefix in toc_titles:
		return toc_titles[prefix]
	label = ".".join(str(part) for part in prefix)
	child = _NUM_RE.match(child_title)
	if _section_number(child_title) == (*prefix, 0):
		return label + child_title[child.end(1) :]
	return label


def is_bare_heading_for(section: Section, title: str) -> bool:
	return (
		not section.markdown
		and not _NUM_RE.match(section.title)
		and _title_words(section.title) == _title_words(title)
	)


def toc_end_line(lines: list[str]) -> int:
	"""Index of the last table-of-contents entry when the page is a TOC page, else -1. TOC entries name
	real headings, so reading them as headings opens phantom sections and advances chapter numbering."""
	entries: list[tuple[int, int]] = []
	content_lines = 0
	for index, line in enumerate(lines):
		text = line.strip()
		if not text or _SEP_ONLY.match(text):
			continue
		content_lines += 1
		entry = _TOC_ENTRY.match(text) or _TOC_LEADER.search(text)
		if entry:
			entries.append((index, int(entry.group(1))))
	if len(entries) < TOC_MIN_ENTRIES or len(entries) < TOC_MIN_ENTRY_SHARE * content_lines:
		return -1
	ascending = sum(later >= earlier for (_, earlier), (_, later) in pairwise(entries))
	if ascending < TOC_MIN_ASCENDING_SHARE * (len(entries) - 1):
		return -1
	return entries[-1][0]


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
	dotted_headings = dotted_heading_positions(pages)
	recurring_titles = recurring_section_titles(pages)
	toc_titles = toc_section_titles(pages)
	last_at_depth: dict[int, tuple[int, ...]] = {}

	def flush():
		if current is not None:
			current.markdown = "\n".join(buf).strip()
			sections.append(current)

	for page_index, (page_no, md) in enumerate(pages):
		# Without a blank line, GFM reads the next page's first line as another row of a table, more
		# raw HTML, or a lazy continuation of a list item that ended the previous page.
		if buf and buf[-1].strip():
			buf.append("")
		lines = md.splitlines()
		toc_end = toc_end_line(lines)
		first_line = next((index for index, line in enumerate(lines) if line.strip()), -1)
		for line_index, line in enumerate(lines):
			in_toc = line_index <= toc_end and bool(line.strip())
			next_dotted = next_dotted_number(dotted_headings, page_index, line_index)
			m = _HEADING_RE.match(
				line if in_toc else _promote_numbered_bold_line(line, last_number, next_dotted)
			)
			title = _clean_title(m.group(2)) if m else ""
			if in_toc:
				if not (current and _CONTENTS_TITLE.match(current.title)):
					flush()
					buf = []
					contents_title = title if _CONTENTS_TITLE.match(title) else "Contents"
					stack = [(1, contents_title, False)]
					current = Section(
						title=contents_title,
						level=1,
						hierarchy_path=[contents_title],
						page_start=page_no,
						page_end=page_no,
					)
					if contents_title == title:
						continue
				m = None
			elif m and _is_label(title, level_map):
				line, m = f"**{title}**", None
			if m:
				title = correct_chapter_typo(title, last_number)
				if _repeats_open_section(title, stack) or repeats_running_header(
					title, stack, recurring_titles
				):
					if current is not None:
						current.page_end = page_no
					continue
				number = _section_number(title)
				if number and extends_number(last_number, number):
					# Numbering that steps back to an ancestor is that ancestor's running header.
					if len(number) > 1:
						if not any(_section_number(open_title) == number for _, open_title, _ in stack):
							level = correct_level(title, _infer_level(title, len(m.group(1))), level_map)
							adopt_preceding_descendants(sections, current, stack, title, level, page_no)
						if current is not None:
							current.page_end = page_no
						continue
					next_line = next((text for text in lines[line_index + 1 :] if text.strip()), "")
					if line_index == first_line and _HEADING_RE.match(next_line):
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
							or (
								len(last_number) > 1
								and (next_dotted or not max_chapter)
								and not opens_next_chapter(cnum, last_number, next_dotted, title)
							)
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
				while stack and (
					stack[-1][0] >= level
					or (
						numbered_heading
						and not (stack[-1][2] and extends_number(number, _section_number(stack[-1][1])))
					)
				):
					stack.pop()
				if numbered_heading:
					for prefix in missing_ancestors(number, stack, last_at_depth):
						group_title = ancestor_title(prefix, recurring_titles, toc_titles, title)
						group_level = max(1, level - len(number) + len(prefix))
						group_start = page_no
						if sections and is_bare_heading_for(sections[-1], group_title):
							group_start = sections.pop().page_start
						stack.append((group_level, group_title, True))
						sections.append(
							Section(
								title=group_title,
								level=group_level,
								hierarchy_path=[t for _, t, _ in stack],
								page_start=group_start,
								page_end=page_no,
							)
						)
						last_at_depth[len(prefix)] = prefix
						if len(prefix) == 1:
							max_chapter = max(max_chapter, prefix[0])
				stack.append((level, title, numbered_heading))
				if numbered_heading:
					last_number = number
					last_at_depth[len(number)] = number
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
						title=PREAMBLE_TITLE,
						level=1,
						hierarchy_path=[PREAMBLE_TITLE],
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
