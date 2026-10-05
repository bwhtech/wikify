# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import figure, store
from wikify.tests._figures import PNG, add_document_with_pdf


class TestFigureCrop(FrappeTestCase):
	def setUp(self):
		self.sd = add_document_with_pdf(self, "Figure Crop Test")

	def _add_page(self, markdown: str) -> str:
		return store.add_page(self.sd.name, 1, "visual", PNG, markdown)

	def test_crops_and_replaces_only_that_tag(self):
		self._add_page("Intro text.\n\n![Diagram](image1.png)\n\nMore text.")
		result = figure.crop_page_figure(
			self.sd.name, 1, "Diagram", 0, {"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.4}
		)
		self.assertTrue(result["image_url"])
		page = frappe.get_all(
			"Source Page", filters={"source_document": self.sd.name}, fields=["canonical_markdown"]
		)[0]
		self.assertIn("Intro text.", page.canonical_markdown)
		self.assertIn("More text.", page.canonical_markdown)
		self.assertNotIn("image1.png", page.canonical_markdown)
		self.assertIn(f"![Diagram]({result['image_url']})", page.canonical_markdown)

	def test_duplicate_captions_are_disambiguated_by_occurrence(self):
		self._add_page("![Button](a.png) and ![Button](b.png)")
		result = figure.crop_page_figure(
			self.sd.name, 1, "Button", 1, {"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.4}
		)
		page = frappe.get_all(
			"Source Page", filters={"source_document": self.sd.name}, fields=["canonical_markdown"]
		)[0]
		self.assertIn("![Button](a.png)", page.canonical_markdown)
		self.assertIn(f"![Button]({result['image_url']})", page.canonical_markdown)
		self.assertNotIn("b.png", page.canonical_markdown)

	def test_identical_duplicate_tags_replace_the_clicked_occurrence(self):
		self._add_page("![Button](same.png) then ![Button](same.png)")
		result = figure.crop_page_figure(
			self.sd.name, 1, "Button", 1, {"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.4}
		)
		page = frappe.get_all(
			"Source Page", filters={"source_document": self.sd.name}, fields=["canonical_markdown"]
		)[0]
		self.assertEqual(
			page.canonical_markdown,
			f"![Button](same.png) then ![Button]({result['image_url']})",
		)

	def test_unknown_caption_raises(self):
		self._add_page("![Diagram](image1.png)")
		with self.assertRaises(ValueError):
			figure.crop_page_figure(self.sd.name, 1, "Nope", 0, {"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.4})

	def test_occurrence_out_of_range_raises(self):
		self._add_page("![Diagram](image1.png)")
		with self.assertRaises(ValueError):
			figure.crop_page_figure(
				self.sd.name, 1, "Diagram", 1, {"x0": 0.1, "y0": 0.1, "x1": 0.4, "y1": 0.4}
			)

	def test_degenerate_crop_raises(self):
		self._add_page("![Diagram](image1.png)")
		with self.assertRaises(ValueError):
			figure.crop_page_figure(
				self.sd.name, 1, "Diagram", 0, {"x0": 0.1, "y0": 0.1, "x1": 0.1001, "y1": 0.1001}
			)
