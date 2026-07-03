# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""0.3 Slices 17-19 — content editing + page→section propagation + single-page wiki sync.

Builds a known page/section fixture via the store seam and drives the tool handlers +
engine seams directly (no live model): edit modes and the find/replace uniqueness
contract, `rebuild_section_markdown` (single-owner, overlap detection, tree invariant),
the reparse propagation strings, and `sync_section`'s content-only contract.
"""

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils.nestedset import get_descendants_of

from wikify.agent.context import Ctx
from wikify.agent.tools import content as ct
from wikify.agent.tools.reparse import _propagate_page
from wikify.engine import generate_wiki, store
from wikify.engine.generate import sync_section
from wikify.engine.loader.sectionizer import Section
from wikify.tests import _cleanup
from wikify.engine.sectionize import (
	rebuild_section_markdown,
	sections_covering_page,
)

def _add_page(source_document: str, page_no: int, canonical: str) -> str:
	"""A Source Page row without the rendered-PNG File (File inserts trip the test-mode
	global-search assertion in this environment; content tools never touch the image)."""
	page = frappe.new_doc("Source Page")
	page.source_document = source_document
	page.page_no = page_no
	page.kind = "text"
	page.baseline_markdown = f"baseline page {page_no}"
	page.insert(ignore_permissions=True)
	store.set_canonical(page.name, canonical, 0.9, "cleanup")
	return page.name


def _sec(title, level, path, p_start, p_end, markdown=None):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=markdown if markdown is not None else f"body of {title}",
	)


class TestAgentContent(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc(
			{"doctype": "Source Document", "title": "Content Test", "page_count": 4}
		).insert(ignore_permissions=True)
		# Pages 1-4 with canonical markdown.
		self.pages = {n: _add_page(self.sd.name, n, f"canonical page {n}") for n in range(1, 5)}
		# Alpha owns pages 1-2 exclusively; Beta and Gamma share boundary page 3.
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Alpha", 1, ["1. Alpha"], 1, 2, "old alpha | broken | table"),
				_sec("2. Beta", 1, ["2. Beta"], 3, 3, "beta body"),
				_sec("3. Gamma", 1, ["3. Gamma"], 3, 4, "gamma body"),
			],
		)
		self.ctx = Ctx(session="test", user="Administrator", source_document=self.sd.name)
		self._spaces: set[str] = set()

	def tearDown(self):
		for space in self._spaces:
			root = frappe.db.get_value("Wiki Space", space, "root_group")
			if root:
				names = frappe.get_all(
					"Wiki Document",
					filters={"name": ["in", [root, *get_descendants_of("Wiki Document", root, ignore_permissions=True)]]},
					order_by="lft desc",
					pluck="name",
				)
				for n in names:
					frappe.delete_doc("Wiki Document", n, ignore_permissions=True, force=True)
			frappe.delete_doc("Wiki Space", space, ignore_permissions=True, force=True)
		# Covers sections + pages + the Source Document row itself: the commit below
		# is what used to persist the doc insert and leak one fixture per test run.
		_cleanup._delete_document_rows(self.sd.name)
		frappe.db.commit()

	def _rows(self):
		return {
			r.title: r
			for r in frappe.get_all(
				"Source Section",
				filters={"source_document": self.sd.name},
				fields=["name", "title", "markdown", "lft", "rgt", "level", "parent_source_section", "wiki_document"],
			)
		}

	def _name(self, title):
		return self._rows()[title].name

	# --- Slice 17: edit_section_content ------------------------------------------------

	def test_edit_replace_mode(self):
		out = ct._edit_section_content(
			self.ctx, {"name": self._name("1. Alpha"), "mode": "replace", "content": "# Fixed\n\n| a | b |\n|---|---|"}
		)
		self.assertIn("Updated '1. Alpha'", out)
		self.assertIn("no wiki generated yet", out)
		self.assertEqual(self._rows()["1. Alpha"].markdown, "# Fixed\n\n| a | b |\n|---|---|")

	def test_edit_find_replace_unique(self):
		out = ct._edit_section_content(
			self.ctx,
			{"name": self._name("1. Alpha"), "mode": "find_replace", "find": "broken", "replace": "fixed"},
		)
		self.assertIn("Updated", out)
		self.assertEqual(self._rows()["1. Alpha"].markdown, "old alpha | fixed | table")

	def test_edit_find_replace_rejects_non_unique(self):
		name = self._name("1. Alpha")
		frappe.db.set_value("Source Section", name, "markdown", "dup dup", update_modified=False)
		out = ct._edit_section_content(
			self.ctx, {"name": name, "mode": "find_replace", "find": "dup", "replace": "x"}
		)
		self.assertIn("found 2", out)
		self.assertEqual(self._rows()["1. Alpha"].markdown, "dup dup")
		out = ct._edit_section_content(
			self.ctx, {"name": name, "mode": "find_replace", "find": "absent", "replace": "x"}
		)
		self.assertIn("found 0", out)

	def test_edit_mentions_stale_wiki_when_generated(self):
		self._generate()
		out = ct._edit_section_content(
			self.ctx, {"name": self._name("1. Alpha"), "mode": "replace", "content": "new"}
		)
		self.assertIn("sync_wiki_page", out)

	# --- Slice 18: propagation ----------------------------------------------------------

	def test_sections_covering_page_returns_deepest_and_boundaries(self):
		owners = sections_covering_page(self.sd.name, 1)
		self.assertEqual([o.title for o in owners], ["1. Alpha"])
		owners = sections_covering_page(self.sd.name, 3)
		self.assertEqual(sorted(o.title for o in owners), ["2. Beta", "3. Gamma"])

	def test_rebuild_section_markdown_single_owner(self):
		before = {t: (r.lft, r.rgt, r.level) for t, r in self._rows().items()}
		res = rebuild_section_markdown(self._name("1. Alpha"))
		self.assertEqual(res["overlaps"], [])
		md = self._rows()["1. Alpha"].markdown
		self.assertIn("canonical page 1", md)
		self.assertIn("canonical page 2", md)
		after = {t: (r.lft, r.rgt, r.level) for t, r in self._rows().items()}
		self.assertEqual(before, after)  # tree untouched

	def test_rebuild_reports_boundary_overlap(self):
		res = rebuild_section_markdown(self._name("3. Gamma"))
		self.assertEqual([o["title"] for o in res["overlaps"]], ["2. Beta"])

	def test_propagate_page_single_owner_updates_section(self):
		store.set_canonical(self.pages[1], "REPARSED page 1", 0.95, "vlm")
		out = _propagate_page(self.sd.name, 1)
		self.assertIn("Propagated into section '1. Alpha'", out)
		self.assertIn("REPARSED page 1", self._rows()["1. Alpha"].markdown)

	def test_propagate_boundary_page_changes_nothing_and_names_candidates(self):
		before = {t: r.markdown for t, r in self._rows().items()}
		out = _propagate_page(self.sd.name, 3)
		self.assertIn("NOT yet visible", out)
		self.assertIn("2. Beta", out)
		self.assertIn("3. Gamma", out)
		self.assertEqual({t: r.markdown for t, r in self._rows().items()}, before)

	# --- Slice 19: sync_wiki_page --------------------------------------------------------

	def _generate(self):
		route = "content-test-" + frappe.generate_hash(length=8)
		res = generate_wiki(self.sd.name, new_space={"space_name": "Content Test Wiki", "route": route})
		self._spaces.add(res["space"])
		return res

	def test_sync_without_wiki_document(self):
		res = sync_section(self._name("1. Alpha"))
		self.assertEqual(res, {"synced": False, "reason": "no_wiki_document"})
		out = ct._sync_wiki_page(self.ctx, {"name": self._name("1. Alpha")})
		self.assertIn("no generated wiki page yet", out)

	def test_sync_updates_content_only(self):
		self._generate()
		row = self._rows()["1. Alpha"]
		wd_before = frappe.db.get_value(
			"Wiki Document", row.wiki_document,
			["title", "route", "parent_wiki_document", "sort_order"], as_dict=True,
		)
		frappe.db.set_value("Source Section", row.name, "markdown", "## Freshly fixed", update_modified=False)
		res = sync_section(row.name)
		self.assertTrue(res["synced"])
		wd_after = frappe.db.get_value(
			"Wiki Document", row.wiki_document,
			["title", "route", "parent_wiki_document", "sort_order", "content"], as_dict=True,
		)
		self.assertEqual(wd_after.content, "## Freshly fixed")
		for f in ("title", "route", "parent_wiki_document", "sort_order"):
			self.assertEqual(wd_after[f], wd_before[f])

	def test_sync_group_needs_regenerate_when_child_missing_wiki(self):
		self._generate()
		# A new child under Alpha (which becomes a group) has no wiki page yet.
		from wikify.api.sections import create_section

		alpha = self._name("1. Alpha")
		create_section(self.sd.name, "1.1 New Child", parent=alpha)
		frappe.db.set_value("Source Section", alpha, "markdown", "", update_modified=False)
		res = sync_section(alpha)
		self.assertEqual(res, {"synced": False, "reason": "needs_regenerate"})

	def test_sync_tool_reports_both_layers(self):
		self._generate()
		out = ct._sync_wiki_page(self.ctx, {"name": self._name("1. Alpha")})
		self.assertIn("Both the preview and the live wiki", out)
