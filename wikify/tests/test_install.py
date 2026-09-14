# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import generate_wiki, store
from wikify.engine.loader.sectionizer import Section
from wikify.install import WIKI_TITLE_DOCTYPES, add_wiki_title_customizations
from wikify.tests import _cleanup


class TestWikiTitleCustomizations(FrappeTestCase):
	def test_titles_are_widened_in_meta_and_column(self):
		add_wiki_title_customizations()

		for doctype in WIKI_TITLE_DOCTYPES:
			self.assertEqual(frappe.get_meta(doctype).get_field("title").fieldtype, "Small Text")
			self.assertEqual(frappe.db.get_column_type(doctype, "title"), "text")

	def test_running_again_keeps_one_property_setter(self):
		add_wiki_title_customizations()
		add_wiki_title_customizations()

		for doctype in WIKI_TITLE_DOCTYPES:
			setters = frappe.get_all(
				"Property Setter",
				filters={"doc_type": doctype, "field_name": "title", "property": "fieldtype"},
				pluck="name",
			)
			self.assertEqual(len(setters), 1)

	def test_generation_keeps_a_title_longer_than_140(self):
		add_wiki_title_customizations()
		title = "5. " + "The transplant coordinator keeps records " * 5
		source_document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Long Title Test", "page_count": 1}
		).insert(ignore_permissions=True)
		self.addCleanup(_cleanup.delete_document, source_document.name)
		store.replace_sections(
			source_document.name,
			[
				Section(
					title=title, level=1, hierarchy_path=[title], page_start=1, page_end=1, markdown="body"
				)
			],
		)

		result = generate_wiki(
			source_document.name,
			new_space={
				"space_name": "Long Title Test",
				"route": "long-title-" + frappe.generate_hash(length=8),
			},
		)

		self.assertTrue(frappe.db.exists("Wiki Document", {"title": title, "is_group": 0}))
		self.assertTrue(result["pages"])
