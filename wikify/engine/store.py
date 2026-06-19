"""Persistence seam — replaces the POC's `loader/graph.py` (SQLite) with the
Frappe ORM. The pipeline calls these helpers instead of touching DocTypes
directly, keeping the ported logic diff-minimal against the POC.

Slice 1b surface: `create_document`, `add_page`, `set_page_count`. Scoring,
sections, and canonical selection land in later slices.
"""

from __future__ import annotations

import frappe
from frappe.utils.file_manager import save_file


def create_document(
	title: str,
	import_name: str | None = None,
	pdf_url: str | None = None,
	parser: str | None = None,
) -> str:
	"""Create a Source Document and return its name."""
	doc = frappe.new_doc("Source Document")
	doc.title = title
	doc.set("import", import_name)  # 'import' is a Python keyword — set by string
	doc.pdf = pdf_url
	doc.parser_used = parser
	doc.status = "Parsed"
	doc.insert(ignore_permissions=True)
	return doc.name


def add_page(
	source_document: str,
	page_no: int,
	kind: str,
	png_bytes: bytes,
	baseline_markdown: str,
) -> str:
	"""Create a Source Page row + attach its rendered PNG as a private File."""
	page = frappe.new_doc("Source Page")
	page.source_document = source_document
	page.page_no = page_no
	page.kind = kind
	page.baseline_markdown = baseline_markdown
	page.insert(ignore_permissions=True)

	file_doc = save_file(
		f"page-{page_no:04d}.png",
		png_bytes,
		"Source Page",
		page.name,
		df="image",
		is_private=1,
	)
	page.db_set("image", file_doc.file_url)
	return page.name


def set_page_count(source_document: str, count: int) -> None:
	frappe.db.set_value("Source Document", source_document, "page_count", count)
