# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""0.6 Slice 30 — markdown lint spine.

Pure-rule units first (synthetic strings, no site data), then the write funnel: every
path that lands `Source Section.markdown` must leave `lint_issues` reflecting the new
body — raw db writes via `store.set_section_markdown`, document writes via the
controller. Plus the pipeline auto-fix boundary and the page-verify artifact hook.
"""

import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import sections as api
from wikify.engine import store
from wikify.engine.lint import fix_table_separators, lint_markdown, table_artifacts
from wikify.engine.loader.sectionizer import Section
from wikify.engine.sectionize import rebuild_section_markdown
from wikify.engine.verify.deterministic import parser_artifacts

MISSING_SEPARATOR = "| Issue | Date |\n| 1 | 09/2010 |\n| 2 | 05/2015 |"
RAGGED = "| A | B | C |\n|---|---|---|\n| 1 | 2 |\n| only one |"
LONE_PIPE = "Some prose.\n\n| orphaned fragment |\n\nMore prose."
UNCLOSED_FENCE = "Intro.\n\n```python\nprint('never closed')"
CLEAN = (
	"# Title\n\nProse.\n\n| A | B |\n|---|---|\n| 1 | 2 |\n\n```\ncode\n```\n"
	"\nA pipe in prose a | b is fine outside a table row."
)


class TestLintRules(FrappeTestCase):
	def _codes(self, md):
		return [i["code"] for i in lint_markdown(md)]

	def test_each_rule_fires_and_clean_control_is_silent(self):
		self.assertEqual(self._codes(MISSING_SEPARATOR), ["missing_separator"])
		self.assertEqual(self._codes(RAGGED), ["ragged_row", "ragged_row"])
		self.assertEqual(self._codes(LONE_PIPE), ["lone_pipe_row"])
		self.assertEqual(self._codes(UNCLOSED_FENCE), ["unclosed_fence"])
		self.assertEqual(lint_markdown(CLEAN), [])
		self.assertEqual(lint_markdown(""), [])

	def test_lines_are_one_based_and_point_at_the_block(self):
		issues = lint_markdown("prose\n" + MISSING_SEPARATOR)
		self.assertEqual(issues[0]["line"], 2)

	def test_table_inside_code_fence_is_ignored(self):
		fenced = "```\n| A | B |\n| 1 | 2 |\n```"
		self.assertEqual(lint_markdown(fenced), [])

	def test_missing_separator_suppresses_per_row_raggedness(self):
		md = "| A | B | C |\n| 1 | 2 |\n| 1 | 2 | 3 | 4 |"
		self.assertEqual(self._codes(md), ["missing_separator"])

	def test_escaped_pipes_do_not_split_cells(self):
		md = "| A | B |\n|---|---|\n| a \\| b | c |"
		self.assertEqual(lint_markdown(md), [])

	def test_issue_cap(self):
		md = "\n\n".join("| lone |" for _ in range(20))
		self.assertEqual(len(lint_markdown(md)), 8)


class TestFixTableSeparators(FrappeTestCase):
	def test_fixes_missing_separator_and_result_lints_clean(self):
		fixed = fix_table_separators(MISSING_SEPARATOR)
		self.assertIn("|---|---|", fixed)
		self.assertEqual(fixed.split("\n")[1], "|---|---|")
		self.assertEqual(lint_markdown(fixed), [])

	def test_idempotent_and_noop_on_valid(self):
		fixed = fix_table_separators(MISSING_SEPARATOR)
		self.assertEqual(fix_table_separators(fixed), fixed)
		self.assertEqual(fix_table_separators(CLEAN), CLEAN)

	def test_leaves_lone_rows_and_fenced_tables_alone(self):
		self.assertEqual(fix_table_separators(LONE_PIPE), LONE_PIPE)
		fenced = "```\n| A | B |\n| 1 | 2 |\n```"
		self.assertEqual(fix_table_separators(fenced), fenced)


class TestPageArtifacts(FrappeTestCase):
	def test_parser_artifacts_include_table_breakage(self):
		self.assertIn("table missing separator row", parser_artifacts(MISSING_SEPARATOR))
		self.assertIn("ragged table rows", parser_artifacts(RAGGED))
		self.assertIn("orphaned table row", parser_artifacts(LONE_PIPE))
		self.assertEqual(table_artifacts(CLEAN), [])

	def test_broken_table_page_scores_below_fixed_page(self):
		"""Harness-level (deterministic, no LLM): the same page content with a
		separator-less table composites lower and carries the artifact note."""
		from wikify.engine.verify import score_page

		gt = "Issue Date 1 09/2010 2 05/2015"
		broken = score_page(1, MISSING_SEPARATOR, gt, page_kind="text")
		fixed = score_page(1, fix_table_separators(MISSING_SEPARATOR), gt, page_kind="text")
		self.assertLess(broken.composite, fixed.composite)
		self.assertTrue(any("table missing separator row" in n for n in broken.notes))


def _add_page(source_document, page_no, canonical):
	"""A Source Page row without the rendered-PNG File (same shortcut as
	test_agent_content — the lint funnel never touches the image)."""
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


class TestLintFunnel(FrappeTestCase):
	"""Every markdown write path lands lint_issues — adding a write path that skips the
	funnel should fail one of these."""

	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Lint Test"}).insert(
			ignore_permissions=True
		)
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Alpha", 1, ["1. Alpha"], 1, 1, MISSING_SEPARATOR),
				_sec("2. Beta", 1, ["2. Beta"], 2, 2),
				_sec("3. Gamma", 1, ["3. Gamma"], 3, 3, "## Keep\ntop\n### Split Here\n" + LONE_PIPE),
			],
		)

	def _row(self, title):
		rows = frappe.get_all(
			"Source Section",
			filters={"source_document": self.sd.name, "title": title},
			fields=["name", "markdown", "lint_issues"],
		)
		return rows[0] if rows else None

	def _issues(self, title):
		row = self._row(title)
		return [i["code"] for i in json.loads(row.lint_issues)] if row.lint_issues else []

	def test_replace_sections_insert_path_lints(self):
		self.assertEqual(self._issues("1. Alpha"), ["missing_separator"])
		self.assertEqual(self._issues("2. Beta"), [])

	def test_set_section_markdown_lints_and_clears(self):
		alpha = self._row("1. Alpha")
		store.set_section_markdown(alpha.name, CLEAN)
		self.assertEqual(self._issues("1. Alpha"), [])
		store.set_section_markdown(alpha.name, RAGGED)
		self.assertEqual(self._issues("1. Alpha"), ["ragged_row", "ragged_row"])

	def test_desk_save_path_lints(self):
		doc = frappe.get_doc("Source Section", self._row("2. Beta").name)
		doc.markdown = UNCLOSED_FENCE
		doc.save(ignore_permissions=True)
		self.assertEqual(self._issues("2. Beta"), ["unclosed_fence"])

	def test_split_lints_both_halves(self):
		gamma = self._row("3. Gamma")
		api.split_section(gamma.name, "Split Here")
		self.assertEqual(self._issues("3. Gamma"), [])
		self.assertEqual(self._issues("Split Here"), ["lone_pipe_row"])

	def test_merge_lints_survivor(self):
		alpha, beta = self._row("1. Alpha"), self._row("2. Beta")
		api.merge_sections([beta.name, alpha.name])
		# Survivor is Beta; merged body still carries Alpha's broken table.
		self.assertEqual(self._issues("2. Beta"), ["missing_separator"])

	def test_rebuild_from_pages_autofixes_and_lints(self):
		_add_page(self.sd.name, 1, MISSING_SEPARATOR)
		alpha = self._row("1. Alpha")
		rebuild_section_markdown(alpha.name)
		row = self._row("1. Alpha")
		# Pipeline path: separator inserted (auto-fix boundary), so lint is clean —
		# and the page's canonical stays broken (pages are evidence).
		self.assertIn("|---|---|", row.markdown)
		self.assertEqual(self._issues("1. Alpha"), [])
		canonical = frappe.db.get_value(
			"Source Page", {"source_document": self.sd.name, "page_no": 1}, "canonical_markdown"
		)
		self.assertEqual(canonical, MISSING_SEPARATOR)

	def test_lint_crash_never_blocks_the_write(self):
		alpha = self._row("1. Alpha")
		with patch("wikify.engine.lint.lint_markdown", side_effect=RuntimeError("boom")):
			store.set_section_markdown(alpha.name, "new body")
		row = self._row("1. Alpha")
		self.assertEqual(row.markdown, "new body")
		self.assertFalse(row.lint_issues)


class TestLintSurfacing(FrappeTestCase):
	"""Slice 31 — lint travels to the tree payloads, the preview, and the agent."""

	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Surface Test"}).insert(
			ignore_permissions=True
		)
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Alpha", 1, ["1. Alpha"], 1, 1, MISSING_SEPARATOR),
				_sec("2. Beta", 1, ["2. Beta"], 2, 2),
			],
		)

	def _name(self, title):
		return frappe.get_all(
			"Source Section", filters={"source_document": self.sd.name, "title": title}, pluck="name"
		)[0]

	def test_get_tree_ships_lint_count_not_raw_issues(self):
		roots = api.get_tree(self.sd.name)
		by_title = {n["title"]: n for n in roots}
		self.assertEqual(by_title["1. Alpha"]["lint_count"], 1)
		self.assertEqual(by_title["2. Beta"]["lint_count"], 0)
		self.assertNotIn("lint_issues", by_title["1. Alpha"])

	def test_preview_wiki_nodes_carry_lint_count(self):
		from wikify.engine.generate import preview_wiki

		tree = preview_wiki(self.sd.name)["tree"]
		by_title = {n["title"]: n for n in tree}
		self.assertEqual(by_title["1. Alpha"]["lint_count"], 1)
		self.assertEqual(by_title["2. Beta"]["lint_count"], 0)

	def test_render_section_preview_returns_lint_issues(self):
		from wikify.api.wiki import render_section_preview

		flagged = render_section_preview(self._name("1. Alpha"))
		self.assertEqual(flagged["lint_issues"][0]["code"], "missing_separator")
		clean = render_section_preview(self._name("2. Beta"))
		self.assertEqual(clean["lint_issues"], [])

	def test_agent_context_carries_lint_line_only_when_flagged(self):
		from wikify.agent.context import resolve_attachments

		flagged = resolve_attachments([{"type": "section", "name": self._name("1. Alpha")}])
		self.assertIn("Markdown lint:", flagged.block)
		self.assertIn("table missing separator row (line 1)", flagged.block)
		clean = resolve_attachments([{"type": "section", "name": self._name("2. Beta")}])
		self.assertNotIn("Markdown lint:", clean.block)
