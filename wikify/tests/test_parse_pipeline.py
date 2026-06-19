# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

import tempfile
from pathlib import Path

import fitz  # PyMuPDF
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import parse_pdf


def _make_sample_pdf(path: str) -> None:
	"""A tiny 3-page PDF: two text pages + one drawing-heavy 'visual' page."""
	doc = fitz.open()
	p = doc.new_page()
	p.insert_text((72, 90), "Wikify Sample Manual", fontsize=22)
	p.insert_text((72, 140), "1. Introduction\n\nDigital-native text page with plenty of\nselectable text.", fontsize=12)
	p = doc.new_page()
	p.insert_text((72, 90), "2. Procedures", fontsize=18)
	p.insert_text((72, 140), "Step one.\nStep two.\nStep three.\n" * 3, fontsize=12)
	p = doc.new_page()
	p.insert_text((72, 80), "Fig 1", fontsize=10)
	for i in range(60):
		x = 50 + (i % 10) * 45
		y = 120 + (i // 10) * 60
		p.draw_rect(fitz.Rect(x, y, x + 35, y + 45), color=(0, 0, 0), width=1)
	doc.save(path)
	doc.close()


class TestParsePipeline(FrappeTestCase):
	def test_parse_pdf_creates_document_and_pages(self):
		path = Path(tempfile.mkdtemp()) / "sample.pdf"
		_make_sample_pdf(str(path))

		sd_name = parse_pdf(str(path), title="Test Sample")

		sd = frappe.get_doc("Source Document", sd_name)
		self.assertEqual(sd.title, "Test Sample")
		self.assertEqual(sd.page_count, 3)
		self.assertEqual(sd.parser_used, "pymupdf4llm")

		pages = frappe.get_all(
			"Source Page",
			filters={"source_document": sd_name},
			fields=["page_no", "kind", "image", "baseline_markdown"],
			order_by="page_no asc",
		)
		self.assertEqual(len(pages), 3)
		self.assertEqual([p.page_no for p in pages], [1, 2, 3])
		# Page 3 is drawing-heavy with sparse text -> classified visual.
		self.assertEqual(pages[2].kind, "visual")
		self.assertIn(pages[0].kind, ("text", "visual"))
		for p in pages:
			self.assertTrue(p.image, f"page {p.page_no} missing rendered image")
			self.assertTrue(p.baseline_markdown is not None)
