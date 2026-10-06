from __future__ import annotations

import fitz
import frappe

from wikify.engine import pdf_utils, store
from wikify.engine.sectionize import sections_covering_page
from wikify.engine.tags import find_tag_spans

# Sharper than the cached page thumbnail (settings.render_dpi) so a small figure isn't a blurry upscale.
_CROP_DPI = 400
_MIN_CROP_POINTS = 10


def _clip_rect(page_rect: fitz.Rect, bbox: dict) -> fitz.Rect:
	x0 = max(0.0, min(1.0, bbox["x0"])) * page_rect.width
	y0 = max(0.0, min(1.0, bbox["y0"])) * page_rect.height
	x1 = max(0.0, min(1.0, bbox["x1"])) * page_rect.width
	y1 = max(0.0, min(1.0, bbox["y1"])) * page_rect.height
	return fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))


def crop_page_figure(source_document: str, page_no: int, caption: str, occurrence: int, bbox: dict) -> dict:
	page = store.get_page(source_document, page_no)
	if not page:
		raise ValueError(f"Page {page_no} of {source_document} not found.")
	old_markdown = page.canonical_markdown or page.baseline_markdown or ""
	spans = find_tag_spans(old_markdown, caption)
	if occurrence < 0 or occurrence >= len(spans):
		raise ValueError(
			f"Couldn't find image tag '{caption}' (occurrence {occurrence}) on this page — "
			"it may have changed. Reload the page and try again."
		)
	start, end = spans[occurrence]

	pdf_path = store.get_import_pdf_path(source_document)
	if not pdf_path:
		raise RuntimeError(f"Couldn't locate the source PDF for {source_document}.")

	with fitz.open(pdf_path) as pdf_document:
		if page_no < 1 or page_no > pdf_document.page_count:
			raise ValueError(
				f"Page {page_no} is out of range for {source_document} ({pdf_document.page_count} pages)."
			)
		pdf_page = pdf_document[page_no - 1]
		clip = _clip_rect(pdf_page.rect, bbox)
		if clip.width < _MIN_CROP_POINTS or clip.height < _MIN_CROP_POINTS:
			raise ValueError("That crop is too small to be a real figure.")
		crop_png = pdf_utils.render_png(pdf_page, dpi=_CROP_DPI, clip=clip)

	file_doc = store.save_crop_file(page.name, page_no, crop_png)
	old_tag = old_markdown[start:end]
	new_tag = f"![{caption}]({file_doc.file_url})"
	store.set_canonical_markdown(
		page.name, old_markdown[:start] + new_tag + old_markdown[end:], keep_audit=True
	)

	owners = sections_covering_page(source_document, page_no)
	if len(owners) > 1:
		sections = frappe.get_all(
			"Source Section",
			filters={"name": ["in", [owner.name for owner in owners]]},
			fields=["name", "markdown"],
		)
		holders = [section for section in sections if old_tag in (section.markdown or "")]
		if len(holders) == 1 and holders[0].markdown.count(old_tag) == 1:
			store.set_section_markdown(holders[0].name, holders[0].markdown.replace(old_tag, new_tag))
	return {"page_no": page_no, "image_url": file_doc.file_url}
