"""Merge tables that pymupdf split across a page boundary.

If page N ends with a Markdown table and page N+1 begins with one of the same
column count, the rows are joined into a single table (a repeated or empty header on
the continuation page is dropped; any other header is kept as a row, since the parser
often promotes the page's first data row to a header). Heuristic, but cheap and reversible.

Ported verbatim from the POC `loader/table_stitch.py` (no I/O — pure markdown).
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

_ROW = re.compile(r"^\s*\|.*\|\s*$")
_SEP = re.compile(r"^\s*\|[\s:|-]+\|\s*$")
_CELL_SPLIT = re.compile(r"(?<!\\)\|")
_EMPHASIS = re.compile(r"[*_`]")
_STEP = re.compile(r"^\d+(?:\.\d+)*\s*[.)]?(?:\s|$|[A-Za-z])")
_HTML_TABLE = re.compile(r"<table\b[^>]*>.*?</table\s*>", re.IGNORECASE | re.DOTALL)
_INLINE_MARKS = {"strong": "**", "b": "**", "em": "*", "i": "*"}
_KEPT_INLINE_TAGS = ("sup", "sub")
_WRAPPER_TAGS = ("thead", "tbody", "tfoot", "span", "p", "u")
_LIST_TAGS = ("ul", "ol")
MIN_NUMBERED_ROWS = 2
_SUB_STEP = re.compile(r"^\(?(?:[a-z]|[ivx]{1,4})[.)]?$")


class _TableReader(HTMLParser):
	def __init__(self):
		super().__init__(convert_charrefs=True)
		self.rows: list[list[str]] = []
		self.header_flags: list[list[bool]] = []
		self.cell: list[str] | None = None
		self.row_open = False
		self.saw_row_tag = False
		self.table_depth = 0
		self.convertible = True
		self.list_counters: list[int | None] = []

	def handle_starttag(self, tag, attrs):
		if tag == "table":
			self.table_depth += 1
			self.convertible = self.convertible and self.table_depth == 1
		elif tag == "tr":
			self.close_cell()
			self.saw_row_tag = True
			self.start_row()
		elif tag in ("td", "th"):
			self.start_cell(tag, attrs)
		elif tag == "br":
			self.append("<br>")
		elif tag in _INLINE_MARKS:
			self.append(_INLINE_MARKS[tag])
		elif tag in _KEPT_INLINE_TAGS:
			self.append(f"<{tag}>")
		elif tag in (*_LIST_TAGS, "li") and self.cell is not None:
			self.start_list_tag(tag)
		elif tag not in _WRAPPER_TAGS:
			self.convertible = False

	def handle_endtag(self, tag):
		if tag in ("td", "th"):
			self.close_cell()
		elif tag == "tr":
			self.close_cell()
			self.row_open = False
		elif tag in _INLINE_MARKS:
			self.append(_INLINE_MARKS[tag])
		elif tag in _KEPT_INLINE_TAGS:
			self.append(f"</{tag}>")
		elif tag in _LIST_TAGS and self.list_counters:
			self.list_counters.pop()

	def handle_data(self, data):
		if self.cell is not None:
			self.cell.append(data.replace("<", "&lt;"))
		elif data.strip():
			self.convertible = False

	def append(self, text: str):
		if self.cell is not None:
			self.cell.append(text)

	def start_cell(self, tag: str, attrs):
		if any(name in ("rowspan", "colspan") and (value or "1").strip() != "1" for name, value in attrs):
			self.convertible = False
		self.close_cell()
		if not self.row_open:
			self.start_row()
		self.cell = []
		self.header_flags[-1].append(tag == "th")

	def start_list_tag(self, tag: str):
		if tag in _LIST_TAGS:
			self.list_counters.append(0 if tag == "ol" else None)
		elif self.list_counters:
			self.start_list_item()

	def start_list_item(self):
		counter = self.list_counters[-1]
		if counter is None:
			marker = "•"
		else:
			self.list_counters[-1] = counter = counter + 1
			marker = f"{counter}."
		text = "".join(self.cell or []).rstrip()
		self.cell = [f"{text}<br>{marker} " if text else f"{marker} "]

	def start_row(self):
		self.rows.append([])
		self.header_flags.append([])
		self.row_open = True

	def close_cell(self):
		if self.cell is None:
			return
		text = " ".join("".join(self.cell).split())
		self.rows[-1].append(text.replace("|", "\\|"))
		self.cell = None


def _format_row(cells: list[str]) -> str:
	return "| " + " | ".join(cells) + " |"


def _html_table_to_markdown(html: str) -> str | None:
	reader = _TableReader()
	reader.feed(html)
	reader.close()
	reader.close_cell()
	if not reader.convertible:
		return None
	rows, header_flags = reader.rows, reader.header_flags
	if not reader.saw_row_tag:
		columns = sum(header_flags[0]) if header_flags else 0
		if not columns:
			return None
		cells = rows[0]
		rows = [cells[start : start + columns] for start in range(0, len(cells), columns)]
		header_flags = [[index < columns for index in range(len(row))] for row in rows]
	rows_with_flags = [(row, flags) for row, flags in zip(rows, header_flags, strict=True) if row]
	if not rows_with_flags:
		return None
	columns = max(len(row) for row, _ in rows_with_flags)
	padded = [row + [""] * (columns - len(row)) for row, _ in rows_with_flags]
	if all(rows_with_flags[0][1]):
		header, body = padded[0], padded[1:]
	else:
		header, body = [""] * columns, padded
	return "\n".join([_format_row(header), "|" + "---|" * columns, *(_format_row(row) for row in body)])


def html_tables_to_markdown(md: str) -> str:
	"""Rewrite flat HTML tables (no merged cells, inline content only) as pipe tables, repairing
	unclosed cells/rows on the way, so they can be stitched and cleaned like any other table."""

	def replace(match: re.Match) -> str:
		converted = _html_table_to_markdown(match.group(0))
		if not converted:
			return match.group(0)
		before, after = md[: match.start()], md[match.end() :]
		prefix = "" if not before or before.endswith("\n\n") else "\n" * (1 + (not before.endswith("\n")))
		suffix = "" if not after or after.startswith("\n\n") else "\n" * (1 + (not after.startswith("\n")))
		return f"{prefix}{converted}{suffix}"

	return _HTML_TABLE.sub(replace, md)


def _cells(row: str) -> list[str]:
	text = row.strip()
	text = text[1:] if text.startswith("|") else text
	text = text[:-1] if text.endswith("|") and not text.endswith("\\|") else text
	return [cell.strip() for cell in _CELL_SPLIT.split(text)]


def _ncols(row: str) -> int:
	return len(_cells(row))


def _plain_cell(cell: str) -> str:
	return _EMPHASIS.sub("", cell).strip()


def _header_key(row: str) -> str:
	return " ".join(_plain_cell(cell).lower() for cell in _cells(row)).strip()


def _is_numbered(rows: list[str]) -> bool:
	return sum(1 for row in rows if _STEP.match(_plain_cell(_cells(row)[0]))) >= MIN_NUMBERED_ROWS


def _continues(row: str, after_page_break: bool) -> bool:
	"""A row that carries on the previous step instead of starting one: no step number and, in the
	first non-empty cell, a lowercase start (or, right after a page break, any non-capital start)."""
	cells = [_plain_cell(cell) for cell in _cells(row)]
	if _STEP.match(cells[0]) or _SUB_STEP.match(cells[0]):
		return False
	if after_page_break:
		text = next((cell for cell in cells if cell), "")
		return bool(text) and not text[0].isupper()
	return bool(cells[0]) and cells[0][0].islower()


def _merge_rows(previous: str, row: str) -> str:
	merged = [
		" ".join(part for part in (first, second) if part)
		for first, second in zip(_cells(previous), _cells(row), strict=False)
	]
	return _format_row(merged)


def merge_continuation_rows(md: str) -> str:
	"""Fold a row a page break cut off (lowercase, unnumbered) back into the step it continues."""
	lines = md.split("\n")
	out: list[str] = []
	index = 0
	while index < len(lines):
		if not _ROW.match(lines[index]):
			out.append(lines[index])
			index += 1
			continue
		end = index
		while end < len(lines) and _ROW.match(lines[end]):
			end += 1
		block = lines[index:end]
		body_start = 2 if len(block) > 1 and _SEP.match(block[1]) else 0
		rows = block[:body_start]
		if _is_numbered(block[body_start:]):
			for row in block[body_start:]:
				if len(rows) > body_start and _ncols(row) == _ncols(rows[-1]) and _continues(row, False):
					rows[-1] = _merge_rows(rows[-1], row)
				else:
					rows.append(row)
		else:
			rows = block
		out.extend(rows)
		index = end
	return "\n".join(out)


def _trailing_table(md: str):
	lines = md.rstrip().splitlines()
	i = len(lines)
	while i > 0 and _ROW.match(lines[i - 1]):
		i -= 1
	if i == len(lines):  # nothing trailing
		return None
	return lines[:i], lines[i:]  # (before, table_lines)


def _leading_table(md: str):
	lines = md.lstrip("\n").splitlines()
	j = 0
	while j < len(lines) and _ROW.match(lines[j]):
		j += 1
	if j == 0:
		return None
	return lines[:j], lines[j:]  # (table_lines, after)


def _empty_column(table: list[str]) -> int | None:
	"""The first column with no text in any body row: the column a parser drops when a table goes on
	over a page break, since nothing on the continuation page shows it is there."""
	body = table[2:] if len(table) > 1 and _SEP.match(table[1]) else table
	rows = [_cells(row) for row in body]
	if not rows:
		return None
	return next(
		(
			column
			for column in range(_ncols(table[0]))
			if not any(_plain_cell(row[column]) for row in rows if column < len(row))
		),
		None,
	)


def _with_blank_cell(row: str, column: int) -> str:
	cells = _cells(row)
	cells.insert(column, "")
	return _format_row(cells)


def stitch_cross_page_tables(pages: list[tuple[int, str]]) -> list[tuple[int, str]]:
	out = [[pno, md] for pno, md in pages]
	target = 0
	for k in range(1, len(out)):
		if not out[k][1].strip():
			continue
		_stitch_pair(out, target, k)
		if out[k][1].strip():
			target = k
	return [(pno, md) for pno, md in out]


def _stitch_pair(out: list[list], previous: int, current: int) -> None:
	a = _trailing_table(out[previous][1])
	b = _leading_table(out[current][1])
	if not a or not b:
		return
	a_before, a_tbl = a
	b_tbl, b_after = b
	if not a_tbl or not b_tbl:
		return
	dropped_column = _empty_column(a_tbl) if _ncols(b_tbl[0]) == _ncols(a_tbl[0]) - 1 else None
	if _ncols(a_tbl[0]) != _ncols(b_tbl[0]) and dropped_column is None:
		return
	cont = b_tbl
	if len(b_tbl) >= 2 and _SEP.match(b_tbl[1]):
		repeated = _header_key(b_tbl[0]) in ("", _header_key(a_tbl[0]))
		cont = b_tbl[2:] if repeated else [b_tbl[0], *b_tbl[2:]]
	if dropped_column is not None:
		cont = [_with_blank_cell(row, dropped_column) for row in cont]
	if cont and not _SEP.match(a_tbl[-1]) and _is_numbered(a_tbl) and _continues(cont[0], True):
		a_tbl = [*a_tbl[:-1], _merge_rows(a_tbl[-1], cont[0])]
		cont = cont[1:]
	out[previous][1] = "\n".join(a_before + a_tbl + cont).strip()
	out[current][1] = "\n".join(b_after).strip()
