# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Slice 10 — project hierarchy: the Wikify Project DocType guards, the seed/backfill of
the "Uncategorized" catch-all, the denormalized import_count + project_name, and the
project-scoped Explore filter.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import explore as explore_api
from wikify.api import projects as projects_api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.seed import seed_section_types, seed_uncategorized_project


def _sec(title, ptype):
	return Section(
		title=title,
		level=1,
		hierarchy_path=[title],
		page_start=1,
		page_end=1,
		markdown=f"body of {title}",
		section_type=ptype,
	)


class TestProjects(FrappeTestCase):
	def test_seed_is_idempotent(self):
		"""seed_uncategorized_project get-or-creates exactly one is_default project."""
		first = seed_uncategorized_project()
		second = seed_uncategorized_project()
		self.assertEqual(first, second)
		self.assertEqual(frappe.db.count("Wikify Project", {"is_default": 1}), 1)

	def test_single_default_guard(self):
		"""A second is_default project is rejected by validate."""
		seed_uncategorized_project()
		with self.assertRaises(frappe.ValidationError):
			frappe.get_doc(
				{"doctype": "Wikify Project", "project_name": "Another Default", "is_default": 1}
			).insert(ignore_permissions=True)

	def test_cannot_delete_default(self):
		default = seed_uncategorized_project()
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Wikify Project", default)

	def test_cannot_delete_non_empty_project(self):
		name = projects_api.create_project("Has Imports")
		imp = frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Doc",
				"pdf": "/files/x.pdf",
				"project": name,
			}
		).insert(ignore_permissions=True)
		with self.assertRaises(frappe.ValidationError):
			frappe.delete_doc("Wikify Project", name)
		# Empty it, then deletion succeeds.
		imp.delete()
		frappe.delete_doc("Wikify Project", name)
		self.assertFalse(frappe.db.exists("Wikify Project", name))

	def test_import_count_denormalized(self):
		"""import_count tracks Import insert/delete; project_name is fetched onto the Import."""
		name = projects_api.create_project("Counted")
		self.assertEqual(frappe.db.get_value("Wikify Project", name, "import_count"), 0)
		imp = frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Doc",
				"pdf": "/files/x.pdf",
				"project": name,
			}
		).insert(ignore_permissions=True)
		self.assertEqual(frappe.db.get_value("Wikify Project", name, "import_count"), 1)
		self.assertEqual(imp.project_name, "Counted")
		imp.delete()
		self.assertEqual(frappe.db.get_value("Wikify Project", name, "import_count"), 0)

	def test_create_project_requires_name(self):
		with self.assertRaises(frappe.ValidationError):
			projects_api.create_project("   ")

	def test_list_projects_pins_default_first(self):
		seed_uncategorized_project()
		projects_api.create_project("Zeta")
		rows = projects_api.list_projects()
		self.assertTrue(rows[0]["is_default"])

	def test_explore_scopes_to_project(self):
		"""type_summary / sections_by_type honour the project filter via denormalized docs."""
		seed_section_types()
		proj = projects_api.create_project("Scoped Explore")
		sd = frappe.get_doc({"doctype": "Source Document", "title": "Scoped Doc", "project": proj}).insert(
			ignore_permissions=True
		)
		store.replace_sections(sd.name, [_sec("Alpha", "clinical_protocols")])

		summary = explore_api.type_summary(project=proj)
		narrative = next((t for t in summary if t["type_name"] == "clinical_protocols"), None)
		self.assertIsNotNone(narrative)
		self.assertEqual(narrative["count"], 1)

		groups = explore_api.sections_by_type("clinical_protocols", project=proj)
		self.assertEqual(len(groups), 1)
		self.assertEqual(groups[0]["source_document"], sd.name)

		# A different project sees none of it.
		other = projects_api.create_project("Empty Project")
		empty = explore_api.sections_by_type("clinical_protocols", project=other)
		self.assertEqual(empty, [])
