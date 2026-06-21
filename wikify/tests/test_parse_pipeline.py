# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

import tempfile
from pathlib import Path
from unittest.mock import patch

import fitz  # PyMuPDF
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import classify, parse_pdf
from wikify.engine.verify import score_page


def _make_sample_pdf(path: str) -> None:
	"""A tiny 3-page PDF: two text pages + one drawing-heavy 'visual' page."""
	doc = fitz.open()
	p = doc.new_page()
	p.insert_text((72, 90), "Wikify Sample Manual", fontsize=22)
	p.insert_text(
		(72, 140), "1. Introduction\n\nDigital-native text page with plenty of\nselectable text.", fontsize=12
	)
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

		# Classification (eager in parse, Slice 6) is a network call — stub it so the
		# parse integration test stays hermetic; classification has its own test module.
		with patch.object(classify, "classify_section", return_value="other"):
			sd_name = parse_pdf(str(path), title="Test Sample")

		sd = frappe.get_doc("Source Document", sd_name)
		self.assertEqual(sd.title, "Test Sample")
		self.assertEqual(sd.page_count, 3)
		self.assertEqual(sd.parser_used, "pymupdf4llm")

		# mean_score is mirrored onto the doc (scoring ran for every page).
		self.assertIsNotNone(sd.mean_score)

		pages = frappe.get_all(
			"Source Page",
			filters={"source_document": sd_name},
			fields=["page_no", "kind", "image", "baseline_markdown", "composite", "verdict", "text_recall"],
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
			# Every page is scored: a verdict + composite are persisted.
			self.assertIn(p.verdict, ("pass", "escalate", "review"))
			self.assertIsNotNone(p.composite)
		# A clean digital-native text page should recall ~all its tokens and pass.
		text_page = pages[0]
		self.assertEqual(text_page.kind, "text")
		self.assertGreater(text_page.text_recall, 0.9)
		# Visual page has no judge (no key in test) -> composite 0 -> flagged for review.
		self.assertEqual(pages[2].verdict, "review")

	def test_harness_scores_page_type_aware(self):
		"""Deterministic harness behavior (no LLM): a faithful transcription passes;
		a near-empty one is flagged."""
		gt = "The quick brown fox jumps over the lazy dog. Section one covers setup."
		good = score_page(1, gt, gt, page_kind="text")
		self.assertEqual(good.verdict, "pass")
		self.assertGreater(good.text_recall, 0.95)

		dropped = score_page(2, "The fox.", gt, page_kind="text")
		self.assertLess(dropped.text_recall, 0.85)
		self.assertIn("low text recall — possible dropped content", dropped.notes)

		# Visual page with no judge score -> composite unreliable -> review.
		visual = score_page(3, "", gt, page_kind="visual")
		self.assertEqual(visual.verdict, "review")
		self.assertEqual(visual.composite, 0.0)
