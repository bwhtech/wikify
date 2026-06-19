"""Wikify pipeline package — ported from the POC (`scratch/pdf_lab/`).

No Flask, no SQLite: persistence goes through `store.py` (the Frappe ORM seam).
The POC logic is preserved; only the I/O boundaries change.

`parse_pdf` is the headless entry point (Slice 1b walking skeleton): render +
baseline-parse every page of a PDF into Source Document + Source Page rows.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import fitz  # PyMuPDF

from wikify.engine import pdf_utils, store
from wikify.engine.parsers import pymupdf as baseline


def parse_pdf(
	pdf_path: str,
	title: str | None = None,
	import_name: str | None = None,
	pdf_url: str | None = None,
	progress_cb: Callable[[int, int], None] | None = None,
	log_cb: Callable[[int, int, str], None] | None = None,
) -> str:
	"""Render + baseline-parse every page → Source Document (+ Source Page rows).

	Returns the Source Document name. `progress_cb(done, total)` and
	`log_cb(page_no, total, kind)` are optional hooks the parse job uses to stream
	progress + log lines; the headless `bench execute` path passes neither.
	"""
	pdf_path = str(pdf_path)
	title = title or Path(pdf_path).stem

	with fitz.open(pdf_path) as doc:
		total = doc.page_count
		sd = store.create_document(
			title=title, import_name=import_name, pdf_url=pdf_url, parser=baseline.NAME
		)
		for i, page in enumerate(doc):
			page_no = i + 1
			png = pdf_utils.render_png(page)
			kind = pdf_utils.classify_page(page)
			markdown = baseline.parse_page(pdf_path, page_no)
			store.add_page(sd, page_no, kind, png, markdown)
			if log_cb:
				log_cb(page_no, total, kind)
			if progress_cb:
				progress_cb(page_no, total)
		store.set_page_count(sd, total)

	return sd
