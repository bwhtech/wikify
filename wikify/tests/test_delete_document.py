# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from unittest.mock import patch

import fitz
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.rag import events


def make_png() -> bytes:
	document = fitz.open()
	document.new_page(width=80, height=20).insert_text((2, 12), frappe.generate_hash(length=8), fontsize=8)
	return document[0].get_pixmap().tobytes("png")


def make_section(title: str, level: int, path: list[str]) -> Section:
	return Section(
		title=title, level=level, hierarchy_path=path, page_start=1, page_end=1, markdown=f"body of {title}"
	)


class TestDeleteDocument(FrappeTestCase):
	def setUp(self):
		self.project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Delete Test {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		self.source_document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Delete Test", "project": self.project.name}
		).insert(ignore_permissions=True)
		self.wikify_import = frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Delete Test",
				"pdf": "/files/none.pdf",
				"status": "Review",
				"project": self.project.name,
				"source_document": self.source_document.name,
			}
		).insert(ignore_permissions=True)
		self.source_document.db_set("import", self.wikify_import.name)

		store.replace_sections(
			self.source_document.name,
			[make_section("1. Alpha", 1, ["1. Alpha"]), make_section("2. Beta", 1, ["2. Beta"])],
		)
		sections = frappe.get_all(
			"Source Section", filters={"source_document": self.source_document.name}, pluck="name"
		)
		frappe.get_doc(
			{
				"doctype": "Section Reference",
				"source_document": self.source_document.name,
				"from_section": sections[0],
				"to_section": sections[1],
			}
		).insert(ignore_permissions=True)
		self.page = store.add_page(self.source_document.name, 1, "text", make_png(), "page one")
		frappe.get_doc(
			{"doctype": "Import Log Entry", "import": self.wikify_import.name, "message": "parsed"}
		).insert(ignore_permissions=True)
		self.session = frappe.get_doc(
			{
				"doctype": "Wikify Agent Session",
				"user": "Administrator",
				"scope": "document",
				"project": self.project.name,
				"source_document": self.source_document.name,
			}
		).insert(ignore_permissions=True)

	def page_file(self) -> str:
		return frappe.db.get_value(
			"File", {"attached_to_doctype": "Source Page", "attached_to_name": self.page}
		)

	def test_deleting_an_import_removes_everything_its_document_owns(self):
		self.assertEqual(frappe.db.get_value("Wikify Project", self.project.name, "import_count"), 1)
		page_file = self.page_file()

		frappe.delete_doc("Wikify Import", self.wikify_import.name)

		self.assertFalse(frappe.db.exists("Wikify Import", self.wikify_import.name))
		self.assertFalse(frappe.db.exists("Source Document", self.source_document.name))
		for doctype in ("Source Section", "Source Page", "Section Reference"):
			self.assertEqual(frappe.db.count(doctype, {"source_document": self.source_document.name}), 0)
		self.assertEqual(frappe.db.count("Import Log Entry", {"import": self.wikify_import.name}), 0)
		self.assertFalse(frappe.db.exists("File", page_file))
		self.assertIsNone(frappe.db.get_value("Wikify Agent Session", self.session.name, "source_document"))
		self.assertEqual(frappe.db.get_value("Wikify Project", self.project.name, "import_count"), 0)

	def test_a_running_import_cannot_be_deleted(self):
		self.wikify_import.db_set("status", "Parsing")

		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Wikify Import", self.wikify_import.name)
		self.assertTrue(frappe.db.exists("Source Document", self.source_document.name))
		self.assertEqual(frappe.db.count("Source Page", {"source_document": self.source_document.name}), 1)

	def test_a_published_document_keeps_its_page_images(self):
		self.source_document.db_set("wiki_root_group", "published-root")
		page_file = self.page_file()

		frappe.delete_doc("Wikify Import", self.wikify_import.name)

		self.assertTrue(frappe.db.exists("File", page_file))
		self.assertIsNone(frappe.db.get_value("File", page_file, "attached_to_name"))

	def test_deleting_queues_a_rag_rebuild_of_the_project(self):
		frappe.cache().delete_value(events.pending_key(self.project.name))
		self.addCleanup(frappe.cache().delete_value, events.pending_key(self.project.name))

		with (
			patch.object(events, "indexing_suspended", return_value=False),
			patch.object(frappe, "enqueue") as enqueue,
		):
			frappe.delete_doc("Wikify Import", self.wikify_import.name)

		rebuilds = [
			call
			for call in enqueue.call_args_list
			if call.args and call.args[0] == "wikify.rag.events.rebuild_pending_project"
		]
		self.assertEqual(len(rebuilds), 1)
		self.assertEqual(rebuilds[0].kwargs["project"], self.project.name)
