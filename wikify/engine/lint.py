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


def lint_markdown(markdown: str) -> list[dict]:
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
