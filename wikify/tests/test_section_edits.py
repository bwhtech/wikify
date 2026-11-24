# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Slice 5 — tree-review mutations + the build_graph approval gate.

Builds a known three-node tree (Alpha > Alpha-One, and a sibling Beta) via the store
seam, then drives the whitelisted APIs directly, asserting the NestedSet bounds and the
denormalized level/hierarchy_path/is_group fields stay consistent after each edit.
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import sections as api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.seed import seed_uncategorized_project


def _sec(title, level, path, p_start, p_end):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=f"body of {title}",
	)


class TestSectionEdits(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Edit Test"}).insert(
			ignore_permissions=True
		)
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Alpha", 1, ["1. Alpha"], 1, 1),
				_sec("1.1 Alpha-One", 2, ["1. Alpha", "1.1 Alpha-One"], 1, 1),
				_sec("2. Beta", 1, ["2. Beta"], 2, 2),
			],
		)

	def _import(self, status):
		return frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Edit Test",
				"pdf": "/files/none.pdf",
				"status": status,
				"project": seed_uncategorized_project(),
				"source_document": self.sd.name,
			}
		).insert(ignore_permissions=True)

	def _rows(self):
		return {
			r.title: r
			for r in frappe.get_all(
				"Source Section",
				filters={"source_document": self.sd.name},
				fields=[
					"name",
					"title",
					"level",
					"parent_source_section",
					"hierarchy_path",
					"is_group",
					"include_in_wiki",
					"lft",
					"rgt",
				],
			)
		}

	def _name(self, title):
		return self._rows()[title].name

	def test_initial_tree_well_formed(self):
		rows = self._rows()
		self.assertEqual(rows["1. Alpha"].is_group, 1)
		self.assertEqual(rows["1.1 Alpha-One"].parent_source_section, rows["1. Alpha"].name)
		self.assertEqual(rows["2. Beta"].is_group, 0)

	def test_reorder_reparents_and_recomputes_denorm(self):
		# Move Beta under Alpha — it becomes a level-2 child; Alpha stays a group.
		alpha, beta = self._name("1. Alpha"), self._name("2. Beta")
		alpha_one = self._name("1.1 Alpha-One")
		api.reorder_section(beta, new_parent=alpha, new_index=1, siblings=[alpha_one, beta])

		rows = self._rows()
		moved = rows["2. Beta"]
		self.assertEqual(moved.parent_source_section, alpha)
		self.assertEqual(moved.level, 2)
		self.assertEqual(moved.hierarchy_path, "1. Alpha > 2. Beta")
		# Alpha now contains both children; its lft/rgt span them.
		self.assertEqual(rows["1. Alpha"].is_group, 1)
		self.assertTrue(rows["1. Alpha"].lft < moved.lft < moved.rgt < rows["1. Alpha"].rgt)
		# Sibling order honors the siblings list (Alpha-One before Beta).
		self.assertTrue(rows["1.1 Alpha-One"].lft < moved.lft)

	def test_reorder_into_own_subtree_is_rejected(self):
		alpha, alpha_one = self._name("1. Alpha"), self._name("1.1 Alpha-One")
		with self.assertRaises(frappe.ValidationError):
			api.reorder_section(alpha, new_parent=alpha_one, new_index=0, siblings=[alpha])

	def test_reorder_to_root_clears_parent(self):
		alpha_one = self._name("1.1 Alpha-One")
		beta = self._name("2. Beta")
		api.reorder_section(
			alpha_one,
			new_parent=None,
			new_index=2,
			siblings=[
				self._name("1. Alpha"),
				beta,
				alpha_one,
			],
		)
		row = self._rows()["1.1 Alpha-One"]
		self.assertIn(row.parent_source_section, (None, ""))
		self.assertEqual(row.level, 1)
		self.assertEqual(row.hierarchy_path, "1.1 Alpha-One")

	def test_rename_updates_descendant_paths(self):
		alpha = self._name("1. Alpha")
		api.rename_section(alpha, "Chapter One")
		rows = {r.name: r for r in self._rows().values()}
		# The renamed node and its child's threaded path both reflect the new title.
		self.assertEqual(rows[alpha].hierarchy_path, "Chapter One")
		child = next(r for r in rows.values() if r.parent_source_section == alpha)
		self.assertEqual(child.hierarchy_path, "Chapter One > 1.1 Alpha-One")

	def test_toggle_include_cascades_to_subtree(self):
		api.toggle_include(self._name("1. Alpha"), False)
		rows = self._rows()
		self.assertEqual(rows["1. Alpha"].include_in_wiki, 0)
		self.assertEqual(rows["1.1 Alpha-One"].include_in_wiki, 0)  # cascaded
		self.assertEqual(rows["2. Beta"].include_in_wiki, 1)  # untouched sibling

		api.toggle_include(self._name("1. Alpha"), True)
		self.assertEqual(self._rows()["1.1 Alpha-One"].include_in_wiki, 1)

	def test_delete_cascades_and_keeps_tree_consistent(self):
		api.delete_section(self._name("1. Alpha"))
		rows = self._rows()
		self.assertNotIn("1. Alpha", rows)
		self.assertNotIn("1.1 Alpha-One", rows)  # child removed too
		beta = rows["2. Beta"]
		self.assertEqual(beta.lft, 1)  # tree renumbered from 1
		self.assertLess(beta.lft, beta.rgt)

	def test_update_section_markdown_saves_and_relints(self):
		beta = self._name("2. Beta")
		api.update_section_markdown(beta, "| a | b |\n| 1 | 2 |")

		markdown, lint_issues = frappe.db.get_value("Source Section", beta, ["markdown", "lint_issues"])
		self.assertEqual(markdown, "| a | b |\n| 1 | 2 |")
		self.assertTrue(store.lint_count(lint_issues))

	def test_update_section_markdown_needs_write_permission(self):
		beta = self._name("2. Beta")
		frappe.set_user("Guest")
		try:
			with self.assertRaises(frappe.PermissionError):
				api.update_section_markdown(beta, "edited")
		finally:
			frappe.set_user("Administrator")
		self.assertEqual(frappe.db.get_value("Source Section", beta, "markdown"), "body of 2. Beta")

	def test_build_graph_advances_status(self):
		imp = self._import("Review")

		out = api.build_graph(imp.name)
		self.assertEqual(out["status"], "Graphed")
		self.assertEqual(frappe.db.get_value("Wikify Import", imp.name, "status"), "Graphed")
		self.assertEqual(frappe.db.get_value("Source Document", self.sd.name, "status"), "Graphed")

	def test_completed_import_blocks_further_tree_edits(self):
		# A published wiki is a one-way handoff to the Wiki app's own editor — nothing
		# that would let the source tree drift from what's already published should
		# still be reachable once the import is Completed.
		imp = self._import("Completed")
		alpha, beta = self._name("1. Alpha"), self._name("2. Beta")

		with self.assertRaises(frappe.ValidationError):
			api.reorder_section(beta, new_parent=alpha, new_index=0, siblings=[beta])
		with self.assertRaises(frappe.ValidationError):
			api.rename_section(alpha, "Renamed")
		with self.assertRaises(frappe.ValidationError):
			api.toggle_include(alpha, False)
		with self.assertRaises(frappe.ValidationError):
			api.delete_section(alpha)
		with self.assertRaises(frappe.ValidationError):
			api.update_section_markdown(alpha, "edited")
		with self.assertRaises(frappe.ValidationError):
			api.build_graph(imp.name)

	def test_completed_import_blocks_assistant_tree_edits(self):
		self._import("Completed")
		alpha, beta = self._name("1. Alpha"), self._name("2. Beta")
		store.set_section_markdown(beta, "intro\n\n## Tail\n\nmore")

		for edit in (
			lambda: api.move_section(beta, new_parent=alpha),
			lambda: api.set_section_type(alpha, None),
			lambda: api.create_section(self.sd.name, "Gamma"),
			lambda: api.split_section(beta, "Tail"),
			lambda: api.merge_sections([alpha, beta]),
		):
			with self.assertRaisesRegex(frappe.ValidationError, "already been published"):
				edit()
		self.assertEqual(set(self._rows()), {"1. Alpha", "1.1 Alpha-One", "2. Beta"})
		self.assertIn(self._rows()["2. Beta"].parent_source_section, (None, ""))

	def test_unpublished_import_allows_assistant_tree_edits(self):
		self._import("Review")
		alpha, beta = self._name("1. Alpha"), self._name("2. Beta")
		store.set_section_markdown(beta, "intro\n\n## Tail\n\nmore")

		api.set_section_type(alpha, None)
		api.create_section(self.sd.name, "Gamma")
		split = api.split_section(beta, "Tail")
		api.merge_sections([beta, split["new_name"]])
		api.move_section(beta, new_parent=alpha)

		rows = self._rows()
		self.assertEqual(set(rows), {"1. Alpha", "1.1 Alpha-One", "2. Beta", "Gamma"})
		self.assertEqual(rows["2. Beta"].parent_source_section, alpha)
