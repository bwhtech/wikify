"""Sectionize pass (Slice 4) — build a doc's Source Section tree from its pages.

Ported from the POC `pipeline._store_sections`: take each page's canonical markdown,
strip cross-page boilerplate (running headers/footers that would read as fake
headings), split into hierarchical sections honoring the embedded PDF outline, and
rebuild the `Source Section` NestedSet tree.

Run at the end of both parse (canonical == baseline there) and remediate (canonical ==
adopted output), so the tree always reflects the best available markdown — the
remediate rebuild never reverts to empty/pre-cleanup text.

0.3 Slice 18 adds the **incremental** path: `rebuild_section_markdown` re-derives one
section's markdown from its pages' canonical without touching the tree, so a page-scoped
re-parse can propagate to the section the wiki preview renders.
"""

from __future__ import annotations

from collections.abc import Callable

import frappe

from wikify.engine import store
from wikify.engine.classify import classify_document
from wikify.engine.lint import fix_table_separators
from wikify.engine.loader.cleanup import clean_pages
from wikify.engine.loader.sectionizer import sectionize
from wikify.engine.loader.toc import toc_level_map


def sectionize_document(source_document: str, pdf_path: str) -> int:
	"""Rebuild the Source Section tree from the doc's canonical pages. Returns the count."""
	level_map = toc_level_map(str(pdf_path))
	pages = clean_pages(store.get_canonical_pages(source_document))
	sections = sectionize(pages, level_map)
	# 0.6 auto-fix boundary: repair separator-less tables in the assembled section
	# product, pre-review. Page canonical stays untouched — pages are evidence.
	for sec in sections:
		sec.markdown = fix_table_separators(sec.markdown)
	return store.replace_sections(source_document, sections)


def rebuild_and_classify(
	source_document: str,
	pdf_path: str,
	stage_cb: Callable[[str], None] | None = None,
	project_context: str = "",
) -> int:
	"""Rebuild the section tree, then classify each fresh section. Returns the count.

	Shared tail of the parse and remediate passes (the rebuild assigns new section
	names, so types are always re-derived). `stage_cb` streams the two post-page-loop
	phase labels so the progress bar doesn't look pinned at the last page.
	`project_context` steers the eager classify (blank = v0.1 behavior).
	"""
	if stage_cb:
		stage_cb("Building section tree")
	count = sectionize_document(source_document, pdf_path)
	if stage_cb:
		stage_cb("Classifying sections")
	classify_document(
		source_document,
		progress_cb=(lambda done, total, title, type_: stage_cb(f"Classifying sections ({done}/{total})"))
		if stage_cb
		else None,
		project_context=project_context,
	)
	return count


# --- 0.3 Slice 18: incremental page → section propagation ------------------------------


def sections_covering_page(source_document: str, page_no: int) -> list[dict]:
	"""The deepest Source Sections whose page range covers `page_no`.

	Ancestor groups span their children's ranges, so a page is "covered" by its whole
	ancestry; only the deepest covering nodes (no covering descendant of their own) are
	candidates for propagation. One result = unambiguous owner; several = the page is a
	boundary page shared between sections.
	"""
	rows = frappe.get_all(
		"Source Section",
		filters={
			"source_document": source_document,
			"page_start": ["<=", page_no],
			"page_end": [">=", page_no],
		},
		fields=["name", "title", "page_start", "page_end", "lft", "rgt"],
		order_by="lft asc",
	)
	return [r for r in rows if not any(o.lft > r.lft and o.rgt < r.rgt for o in rows)]


def overlapping_sections(section_name: str) -> list[dict]:
	"""Non-ancestor, non-descendant sections whose page range intersects this one's.

	These are the neighbors that share a boundary page — a whole-page-range rebuild of
	this section would also absorb content that belongs to them.
	"""
	sec = frappe.db.get_value(
		"Source Section",
		section_name,
		["source_document", "page_start", "page_end", "lft", "rgt"],
		as_dict=True,
	)
	if not sec or not (sec.page_start and sec.page_end):
		return []
	rows = frappe.get_all(
		"Source Section",
		filters={
			"source_document": sec.source_document,
			"name": ["!=", section_name],
			"page_start": ["<=", sec.page_end],
			"page_end": [">=", sec.page_start],
		},
		fields=["name", "title", "page_start", "page_end", "lft", "rgt"],
		order_by="lft asc",
	)
	return [
		r
		for r in rows
		if not (r.lft < sec.lft and r.rgt > sec.rgt)  # ancestor
		and not (r.lft > sec.lft and r.rgt < sec.rgt)  # descendant
	]


def rebuild_section_markdown(section_name: str) -> dict:
	"""Re-derive ONE section's markdown from its pages' canonical, tree untouched.

	Adopts the whole `page_start`-`page_end` canonical slice (boilerplate-stripped, same
	`clean_pages` pass full sectionize uses). Boundary pages shared with neighboring
	sections are reported in `overlaps` — the caller decides whether whole-page adoption
	is acceptable or a surgical `edit_section_content` is the better fix.
	"""
	sec = frappe.db.get_value(
		"Source Section",
		section_name,
		["source_document", "title", "page_start", "page_end"],
		as_dict=True,
	)
	if not sec:
		raise ValueError(f"Section {section_name} not found.")
	if not (sec.page_start and sec.page_end):
		raise ValueError(f"Section '{sec.title}' has no page range to rebuild from.")

	pages = [
		(n, md)
		for n, md in store.get_canonical_pages(sec.source_document)
		if sec.page_start <= n <= sec.page_end
	]
	if not pages:
		raise ValueError(f"No pages found in range {sec.page_start}-{sec.page_end}.")

	cleaned = clean_pages(pages)
	markdown = fix_table_separators("\n\n".join(md.strip() for _, md in cleaned if md.strip()))
	overlaps = overlapping_sections(section_name)
	store.set_section_markdown(section_name, markdown)
	return {
		"section": section_name,
		"title": sec.title,
		"chars": len(markdown),
		"pages": (sec.page_start, sec.page_end),
		"overlaps": [{"name": o.name, "title": o.title} for o in overlaps],
	}
