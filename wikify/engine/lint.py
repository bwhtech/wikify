"""Deterministic markdown lint (0.6) — structure checks, no LLM, no frappe imports.

Detects the breakage measured on real imports (broken tables above all): markdown that
*saves* fine but *renders* mangled on the generated wiki page. One implementation,
three consumers — the Source Section write funnel (`store.set_section_markdown` + the
controller), the page-verify artifact patterns (`verify/deterministic.py`), and the
pipeline auto-fix (`sectionize.py`).

Codes: `missing_separator` (header with no |---| row — GFM renders the block as plain
text), `ragged_row` (cell count ≠ header's), `lone_pipe_row` (orphaned |…| fragment),
`unclosed_fence` (odd fence count — everything after renders as code). Only
`missing_separator` has a mechanically-safe fix; the rest are flag-only.
"""

from __future__ import annotations

import re

# Keep issue lists bounded — the count drives badges; nobody reads 200 entries.
MAX_ISSUES = 8

_FENCE_RE = re.compile(r"^\s*(```|~~~)")
# A separator row: only pipes/dashes/colons/whitespace, with at least one dash.
_SEPARATOR_RE = re.compile(r"^\s*\|?[\s:|-]*-[\s:|-]*\|?\s*$")
_UNESCAPED_PIPE_RE = re.compile(r"(?<!\\)\|")


def _cell_count(line: str) -> int:
	s = line.strip()
	if s.startswith("|"):
		s = s[1:]
	if s.endswith("|") and not s.endswith("\\|"):
		s = s[:-1]
	return len(_UNESCAPED_PIPE_RE.split(s))


def _is_table_row(line: str) -> bool:
	return line.strip().startswith("|")


def _table_blocks(lines: list[str]):
	"""Yield (start_index, block_lines) for each run of consecutive table rows,
	skipping anything inside code fences (markdown examples aren't tables)."""
	in_fence = False
	i = 0
	while i < len(lines):
		if _FENCE_RE.match(lines[i]):
			in_fence = not in_fence
			i += 1
			continue
		if not in_fence and _is_table_row(lines[i]):
			start = i
			while i < len(lines) and _is_table_row(lines[i]):
				i += 1
			yield start, lines[start:i]
		else:
			i += 1


def lint_markdown(markdown: str) -> list[dict]:  # noqa: C901
	"""Structural issues as [{code, line, message}] (1-based lines), capped at
	MAX_ISSUES. Empty list for clean (or empty) markdown."""
	issues: list[dict] = []
	lines = (markdown or "").split("\n")

	def add(code: str, line_no: int, message: str) -> bool:
		issues.append({"code": code, "line": line_no, "message": message})
		return len(issues) >= MAX_ISSUES

	for start, block in _table_blocks(lines):
		if len(issues) >= MAX_ISSUES:
			break
		if len(block) == 1:
			if add("lone_pipe_row", start + 1, "orphaned table row (no surrounding table)"):
				break
			continue
		has_separator = _SEPARATOR_RE.match(block[1])
		if not has_separator:
			if add("missing_separator", start + 1, "table missing separator row"):
				break
			# The whole block already renders broken — per-row raggedness is noise.
			continue
		header_cells = _cell_count(block[0])
		for j, row in enumerate(block[2:], start=2):
			if _SEPARATOR_RE.match(row):
				continue
			cells = _cell_count(row)
			if cells != header_cells and add(
				"ragged_row",
				start + j + 1,
				f"table row has {cells} cells, header has {header_cells}",
			):
				break

	if len(issues) < MAX_ISSUES:
		fence_count = sum(1 for ln in lines if _FENCE_RE.match(ln))
		if fence_count % 2:
			add("unclosed_fence", 0, "unclosed code fence — content after it renders as code")

	return issues


def table_artifacts(markdown: str) -> list[str]:
	"""Distinct human-readable table-breakage names, for `verify.parser_artifacts`."""
	names = {
		"missing_separator": "table missing separator row",
		"ragged_row": "ragged table rows",
		"lone_pipe_row": "orphaned table row",
	}
	seen: list[str] = []
	for issue in lint_markdown(markdown):
		label = names.get(issue["code"])
		if label and label not in seen:
			seen.append(label)
	return seen


def fix_table_separators(markdown: str) -> str:
	"""Insert the missing |---| row after table headers that lack one — the ONLY
	auto-fix (mechanically safe: pure insertion, column count from the header).
	Idempotent; single-row fragments and everything else are left untouched."""
	lines = (markdown or "").split("\n")
	out: list[str] = []
	insertions: dict[int, str] = {}
	for start, block in _table_blocks(lines):
		if len(block) >= 2 and not _SEPARATOR_RE.match(block[1]):
			insertions[start] = "|" + "---|" * _cell_count(block[0])
	if not insertions:
		return markdown
	for i, line in enumerate(lines):
		out.append(line)
		if i in insertions:
			out.append(insertions[i])
	return "\n".join(out)


_BLANK_RUN_RE = re.compile(r"(?<![\\_])_{3,}")
_RULE_LINE_RE = re.compile(r"^\s*(?:_\s*){3,}$")
_HTML_LINE_RE = re.compile(r"^\s*</?[a-zA-Z]")
_LETTER_START_RE = re.compile(r"^[a-z]{2,}")


def _prose_lines(lines: list[str]):
	"""Indexes of lines whose inline markdown is rendered: outside code fences and HTML blocks."""
	in_fence = in_html = False
	for index, line in enumerate(lines):
		if _FENCE_RE.match(line):
			in_fence = not in_fence
			continue
		if in_fence:
			continue
		if _HTML_LINE_RE.match(line):
			in_html = True
		if in_html:
			in_html = bool(line.strip())
			continue
		if "`" not in line:
			yield index


def escape_fill_in_blanks(markdown: str) -> str:
	"""Escape form blanks (runs of 3+ underscores) so markdown can't read them as emphasis and eat
	the text between two blanks. Idempotent; a line that is only underscores (a rule) is left alone."""
	lines = (markdown or "").split("\n")
	for index in _prose_lines(lines):
		if not _RULE_LINE_RE.match(lines[index]):
			lines[index] = _BLANK_RUN_RE.sub(lambda match: "\\_" * len(match.group(0)), lines[index])
	return "\n".join(lines)


def _fix_bold_span(before: str, content: str, after: str) -> tuple[str, str, str]:
	stripped = content.strip()
	if not any(char.isalnum() for char in stripped):
		return before + content, "", after
	if len(stripped) == 1 and stripped.isalpha() and after[:1].islower() and not before[-1:].isalnum():
		return before + stripped, "", after
	if len(stripped) > 2 and stripped[-2] == " " and stripped[-1].isupper() and after[:1].islower():
		stripped, after = stripped[:-2], f" {stripped[-1]}{after}"
	if content[:1].isspace() or (before[-1:].isalpha() and stripped[:1].isalpha() and len(stripped) > 1):
		before += " "
	if (content[-1:].isspace() and not after[:1].isspace()) or (
		_LETTER_START_RE.match(after) and len(stripped) > 1
	):
		after = f" {after}"
	return before, f"**{stripped}**", after


def _fix_bold_spans(text: str) -> str:
	parts = text.split("**")
	if len(parts) % 2 == 0:
		return _fix_bold_spans(text.rstrip()[:-2]) if text.rstrip().endswith("**") else text
	for index in range(1, len(parts), 2):
		parts[index - 1], parts[index], parts[index + 1] = _fix_bold_span(
			parts[index - 1], parts[index], parts[index + 1]
		)
	return "".join(parts)


def fix_glued_emphasis(markdown: str) -> str:
	"""Repair bold the PDF extraction glued to its neighbours: `**To**confirm`, `Helps**to**gain`,
	`**P**revents`, `**Post: H**ypo`, `efficiency**.**`, a stray trailing `**`. Cell by cell in tables."""
	lines = (markdown or "").split("\n")
	for index in _prose_lines(lines):
		line = lines[index]
		if "**" not in line:
			continue
		if _is_table_row(line):
			lines[index] = "|".join(_fix_bold_spans(cell) for cell in _UNESCAPED_PIPE_RE.split(line))
		else:
			lines[index] = _fix_bold_spans(line)
	return "\n".join(lines)


def repair_markdown(markdown: str) -> str:
	"""Every mechanical repair, in one pass, for the assembled section product."""
	return fix_glued_emphasis(escape_fill_in_blanks(fix_table_separators(markdown)))
