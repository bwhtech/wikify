"""Extract genuine embedded graphics from a PDF page and embed them inline.

pymupdf4llm omits every image as a `==> picture [W x H] intentionally omitted <==`
placeholder. For an *image of text* it also recovers the text (a "picture text" block,
which the cleanup pass turns into real Markdown), so those don't need the image. But a
genuine graphic (diagram, flowchart, photo, chart) has no recoverable text — the
placeholder is all that's left, and cleanup strips even that, silently losing the figure.

This replaces such genuine-graphic placeholders with an embedded image link to a private
Frappe File, so the figure survives into the section tree + wiki. Text-image placeholders
are left untouched for cleanup. Deterministic — no LLM, no key needed.
"""

from __future__ import annotations

import re

import fitz  # PyMuPDF
from frappe.utils.file_manager import save_file

# Below this pixel area an "image" is almost always a rule, divider, bullet, icon, or
# logo — not a figure worth embedding.
_MIN_AREA = 200_000

# A page whose extractable text is below this is "sparse" — when it also carries a big
# image, the content lives in the image (a flowchart/diagram pymupdf dropped), so embed it
# even without a placeholder. Text-dense pages never trip this, so we don't re-embed page
# scans whose text the parser already recovered.
_SPARSE_TEXT = 700

_OMIT_RE = re.compile(r"\*{0,2}==>\s*picture \[(\d+) x (\d+)\] intentionally omitted <==\*{0,2}")
_PICTURE_TEXT = "Start of picture text"


def _is_text_image(markdown: str, match_end: int) -> bool:
	"""A "picture text" marker right after the placeholder means pymupdf recovered the
	image's text — keep the text (cleanup handles it), don't embed the image."""
	return _PICTURE_TEXT in markdown[match_end : match_end + 200]


def _save_png(page, xref: int, source_document: str, page_no: int, idx: int) -> str | None:
	"""Extract one embedded image to PNG and host it as a private File on the document."""
	try:
		pix = fitz.Pixmap(page.parent, xref)
		if pix.n - pix.alpha >= 4:  # CMYK / DeviceN → RGB so PNG encodes
			pix = fitz.Pixmap(fitz.csRGB, pix)
		png = pix.tobytes("png")
	except Exception:
		return None
	file_doc = save_file(
		f"page-{page_no:04d}-fig-{idx + 1}.png",
		png,
		"Source Document",
		source_document,
		is_private=1,
	)
	return file_doc.file_url


def _big_images(page) -> list[int]:
	"""Substantial embedded image xrefs, de-duplicated, in page order."""
	imgs, seen = [], set()
	for im in page.get_images(full=True):
		xref = im[0]
		if xref not in seen and im[2] * im[3] >= _MIN_AREA:
			seen.add(xref)
			imgs.append(xref)
	return imgs


def embed_genuine_images(page, markdown: str, source_document: str, page_no: int, kind: str = "text") -> str:
	"""Embed genuine graphics the baseline parser would otherwise drop.

	Two cases:
	  1. An omitted-picture placeholder with no recovered text → replace it inline with the
	     matching page image (precise placement).
	  2. No placeholder, but a sparse-text page carrying a big image → a flowchart/diagram
	     pymupdf dropped silently; append it as a figure.
	Returns the markdown unchanged on the common page (text-dense, or no substantial image,
	or only text-image placeholders). Visual pages are left to the VLM re-parse.
	"""
	imgs = _big_images(page)
	if not imgs:
		return markdown

	# Case 1: genuine-graphic placeholders (omitted, no "picture text"), matched in order.
	genuine = [m for m in _OMIT_RE.finditer(markdown) if not _is_text_image(markdown, m.end())]
	if genuine:
		out, cursor, idx = [], 0, 0
		for m in genuine:
			out.append(markdown[cursor : m.start()])
			url = _save_png(page, imgs[idx], source_document, page_no, idx) if idx < len(imgs) else None
			idx += 1
			out.append(f"![Figure {page_no}.{idx}]({url})" if url else m.group(0))
			cursor = m.end()
		out.append(markdown[cursor:])
		return "".join(out)

	# Case 2: silently-dropped figure on a sparse text page (the content is in the image).
	if kind == "visual" or "![" in markdown or len(page.get_text("text")) >= _SPARSE_TEXT:
		return markdown
	url = _save_png(page, imgs[0], source_document, page_no, 0)
	return f"{markdown.rstrip()}\n\n![Figure {page_no}.1]({url})\n" if url else markdown
