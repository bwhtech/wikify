# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import pages as pages_api
from wikify.engine import store
from wikify.tests._figures import PNG, add_document_with_pdf

CROP_ARGS = {"page_no": "1", "occurrence": "0", "x0": "0.1", "y0": "0.1", "x1": "0.4", "y1": "0.4"}


class TestPagesApi(FrappeTestCase):
	def setUp(self):
		self.sd = add_document_with_pdf(self, "Pages API Test")
		self.page_name = store.add_page(self.sd.name, 1, "visual", PNG, "![Diagram](image1.png)")

	def test_crop_page_figure_coerces_args_and_replaces_the_tag(self):
		result = pages_api.crop_page_figure(source_document=self.sd.name, caption="Diagram", **CROP_ARGS)
		self.assertTrue(result["image_url"])
		markdown = frappe.db.get_value("Source Page", self.page_name, "canonical_markdown")
		self.assertIn(f"![Diagram]({result['image_url']})", markdown)

	def test_crop_page_figure_throws_a_user_facing_error_on_bad_caption(self):
		with self.assertRaises(frappe.ValidationError):
			pages_api.crop_page_figure(source_document=self.sd.name, caption="Nope", **CROP_ARGS)


class TestPagesApiAcl(FrappeTestCase):
	def setUp(self):
		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Pages ACL {frappe.generate_hash(length=6)}"}
		).insert()
		self.sd = add_document_with_pdf(self, "Pages ACL Test", self.project.name)
		self.addCleanup(frappe.set_user, "Administrator")
		self.page_name = store.add_page(self.sd.name, 1, "visual", PNG, "![Diagram](image1.png)")

	def test_cropping_a_page_of_an_unreadable_document_is_refused(self):
		frappe.set_user("Guest")
		with self.assertRaises(frappe.PermissionError):
			pages_api.crop_page_figure(source_document=self.sd.name, caption="Diagram", **CROP_ARGS)
