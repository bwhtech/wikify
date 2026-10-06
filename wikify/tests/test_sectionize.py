# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import tempfile
from pathlib import Path
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import parse_pdf, remediate_pdf, store
from wikify.engine.loader.cleanup import clean_pages, strip_outer_markdown_fence
from wikify.engine.loader.sectionizer import (
	MAX_TITLE_LENGTH,
	Section,
	running_header_titles,
	sectionize,
)
from wikify.rag.chunk import build_chunks
from wikify.tests import _cleanup
from wikify.tests.test_parse_pipeline import _make_sample_pdf
from wikify.tests.test_remediate_pipeline import _MERMAID, _fake_chat


class TestStripMarkdownFence(FrappeTestCase):
	def test_strips_outer_markdown_fence(self):
		table = "| a | b |\n|---|---|\n| 1 | 2 |"
		self.assertEqual(strip_outer_markdown_fence(f"```markdown\n{table}\n```"), table)

	def test_drops_commentary_after_the_fence(self):
		table = "| a | b |\n|---|---|\n| 1 | 2 |"
		wrapped = f"```markdown\n{table}\n```\n\nThe table is part of the manual."
		self.assertEqual(strip_outer_markdown_fence(wrapped), table)

	def test_strips_untagged_and_md_fences(self):
		self.assertEqual(strip_outer_markdown_fence("```\n# Title\n```"), "# Title")
		self.assertEqual(strip_outer_markdown_fence("```md\n# Title\n```"), "# Title")

	def test_leaves_plain_markdown_untouched(self):
		md = "# Title\n\n| a | b |\n|---|---|"
		self.assertEqual(strip_outer_markdown_fence(md), md)

	def test_leaves_mermaid_block_untouched(self):
		page = '```mermaid\nflowchart TD\nA["x"] --> B["y"]\n```'
		self.assertEqual(strip_outer_markdown_fence(page), page)

	def test_does_not_strip_when_inner_fence_present(self):
		wrapped = "```markdown\n# Title\n```mermaid\nflowchart TD\n```\n```"
		self.assertEqual(strip_outer_markdown_fence(wrapped), wrapped)


class TestSectionizer(FrappeTestCase):
	def test_numbered_headings_nest_by_their_number(self):
		pages = [
			(1, "## 1. Introduction\nbody\n## 1.1 Scope\nscope body"),
			(2, "## 2. Procedures\nproc body"),
		]
		secs = sectionize(pages)
		titles = [(s.title, s.level) for s in secs]
		self.assertEqual(titles, [("1. Introduction", 1), ("1.1 Scope", 2), ("2. Procedures", 1)])
		scope = secs[1]
		self.assertEqual(scope.hierarchy_path, ["1. Introduction", "1.1 Scope"])
		self.assertEqual(secs[2].page_start, 2)

	def test_out_of_sequence_numbered_heading_is_demoted(self):
		pages = [(1, "## 1. First\na\n## 2. Second\nb\n## 1. Eclampsia\nc")]
		secs = sectionize(pages)
		levels = {s.title: s.level for s in secs}
		self.assertEqual(levels["1. First"], 1)
		self.assertEqual(levels["2. Second"], 1)
		self.assertEqual(levels["1. Eclampsia"], 2)

	def test_repeated_numbered_running_header_is_merged(self):
		pages = [
			(1, "## 3. Surgery\nintro"),
			(2, "## 3. Surgery\nmore on page two"),
			(3, "## 3. Surgery\nstill more"),
		]
		secs = sectionize(pages)
		self.assertEqual(len(secs), 1)
		self.assertEqual(secs[0].page_start, 1)
		self.assertEqual(secs[0].page_end, 3)
		self.assertIn("more on page two", secs[0].markdown)

	def test_unnumbered_heading_stays_under_the_numbered_section(self):
		pages = [
			(1, "# 3. Job Descriptions\na\n## 3.6.1 Nursing Supervisor\nb\n# Research\nc"),
			(2, "## 3.6.2 Charge Nurse\nd\n# Monday\ne\n# Tuesday\nf\n# 4. Records\ng"),
		]
		secs = sectionize(pages)
		paths = {s.title: s.hierarchy_path for s in secs}
		self.assertEqual(paths["Research"], ["3. Job Descriptions", "3.6.1 Nursing Supervisor", "Research"])
		self.assertEqual(paths["3.6.2 Charge Nurse"], ["3. Job Descriptions", "3.6.2 Charge Nurse"])
		self.assertEqual(paths["Tuesday"], ["3. Job Descriptions", "3.6.2 Charge Nurse", "Tuesday"])
		self.assertEqual(paths["4. Records"], ["4. Records"])

	def test_mixed_case_list_item_does_not_take_a_capitalised_chapter_number(self):
		pages = [
			(1, "# 3 JOB DESCRIPTIONS\na\n## 3.6.8 CAPD Nurse\nb\n# 4 Extra procedures\nc\n# 5 Teaching\nd"),
			(2, "# 4 RECORDS MAINTAINED\ne\n# 5 PATIENT ACCESS\nf"),
		]
		secs = sectionize(pages)
		paths = {s.title: s.hierarchy_path for s in secs}
		self.assertEqual(paths["5 Teaching"], ["3 JOB DESCRIPTIONS", "3.6.8 CAPD Nurse", "5 Teaching"])
		self.assertEqual(paths["4 RECORDS MAINTAINED"], ["4 RECORDS MAINTAINED"])
		self.assertEqual(paths["5 PATIENT ACCESS"], ["5 PATIENT ACCESS"])

	def test_number_continuing_a_nested_list_stays_in_the_list(self):
		pages = [
			(1, "# 1 SCOPE\na\n# 2 PROTOCOLS\nb\n# 1 HYPOTENSION\nc\n# 2 CRAMPS\nd\n# 3 HYPERKALEMIA\ne"),
			(2, "# 3 MEDICATION\nf"),
		]
		secs = sectionize(pages)
		paths = {s.title: s.hierarchy_path for s in secs}
		self.assertEqual(paths["3 HYPERKALEMIA"], ["2 PROTOCOLS", "3 HYPERKALEMIA"])
		self.assertEqual(paths["3 MEDICATION"], ["3 MEDICATION"])

	def test_chapter_on_the_page_after_a_nested_list_is_not_demoted(self):
		pages = [
			(1, "# 1 SCOPE\na\n# 2 PROTOCOLS\nb\n# 1 HYPOTENSION\nc\n# 2 CRAMPS\nd"),
			(2, "# 3 MEDICATION\ne"),
		]
		paths = {s.title: s.hierarchy_path for s in sectionize(pages)}
		self.assertEqual(paths["3 MEDICATION"], ["3 MEDICATION"])

	def test_unnumbered_heading_before_any_number_keeps_its_level(self):
		secs = sectionize([(1, "# Revision History\na\n# 1. Profile\nb")])
		self.assertEqual([(s.title, s.level) for s in secs], [("Revision History", 1), ("1. Profile", 1)])

	def test_content_before_any_heading_becomes_preamble(self):
		secs = sectionize([(1, "just some text\nno headings here")])
		self.assertEqual(len(secs), 1)
		self.assertEqual(secs[0].title, "Preamble")
		self.assertEqual(secs[0].level, 1)

	def test_content_before_the_first_heading_is_kept(self):
		pages = [
			(
				1,
				"- ✓ Concessions of Rs 2,500 are discussed.\n\n| a | b |\n|---|---|\n| 1 | 2 |\n\n## 5.7 Billing\nbill body",
			),
		]
		secs = sectionize(pages)
		self.assertEqual((secs[0].title, secs[0].hierarchy_path), ("Preamble", ["Preamble"]))
		self.assertIn("Concessions of Rs 2,500", secs[0].markdown)
		self.assertIn("| 1 | 2 |", secs[0].markdown)
		self.assertEqual(secs[1].hierarchy_path, ["5.7 Billing"])

	def test_running_sub_header_variants_fold_into_the_open_section(self):
		running = "## 5.8 Policies on patient care"
		pages = [
			(1, f"{running}\n### 5.8.1 Chaperone\na\n{running}\n### 5.8.2 Antibiotics\nb"),
			(2, f"{running}\n### 5.8.3 Pain\nc"),
			(3, f"{running}\n### 5.8.4 Sedation\nd\n## 5.8Policies on patient care\n### 5.8.5 Restraints\ne"),
		]
		secs = sectionize(clean_pages(pages))
		self.assertEqual([s.title for s in secs].count("5.8 Policies on patient care"), 1)
		self.assertEqual(secs[0].title, "5.8 Policies on patient care")
		self.assertTrue(all(s.hierarchy_path[0] == "5.8 Policies on patient care" for s in secs))

	def test_repeated_chapter_title_with_another_number_or_plural_is_not_a_new_section(self):
		protocols = [
			(1, "# 6.1 PROTOCOLS FOR MANAGEMENT\n## 6.1.1 IgA nephropathy\na"),
			(2, "# 6. PROTOCOLS FOR MANAGEMENT\n## 6.1.2 Lupus nephritis\n#### Hydroxychloroquine.\nb"),
		]
		paths = {s.title: s.hierarchy_path for s in sectionize(protocols)}
		self.assertNotIn("6. PROTOCOLS FOR MANAGEMENT", paths)
		self.assertEqual(
			paths["6.1.2 Lupus nephritis"], ["6.1 PROTOCOLS FOR MANAGEMENT", "6.1.2 Lupus nephritis"]
		)
		self.assertIn("Hydroxychloroquine", paths)

		jobs = [
			(1, "# 3. JOB DESCRIPTIONS\n## 3.1 Head\na"),
			(2, "## 3. JOB DESCRIPTION\n## 3.2 Unit head\nb"),
		]
		paths = {s.title: s.hierarchy_path for s in sectionize(jobs)}
		self.assertNotIn("3. JOB DESCRIPTION", paths)
		self.assertEqual(paths["3.2 Unit head"], ["3. JOB DESCRIPTIONS", "3.2 Unit head"])

	def test_bold_line_continuing_the_numbering_is_a_heading(self):
		pages = [
			(
				1,
				"## 5.8.6 Blood Donation\na\n### 5.8.6.2 Donor criteria\nb\n"
				"**5.8.6.3 Requesting blood from blood bank**\n**For use in ward**\nc\n"
				"**5.8.6.4 For use in OR**\nd\n**1. Not a heading**\ne",
			)
		]
		secs = sectionize(pages)
		self.assertEqual(
			[(s.title, s.level) for s in secs],
			[
				("5.8.6 Blood Donation", 3),
				("5.8.6.2 Donor criteria", 4),
				("5.8.6.3 Requesting blood from blood bank", 4),
				("5.8.6.4 For use in OR", 4),
			],
		)
		self.assertNotIn("Requesting blood", secs[1].markdown)
		self.assertIn("**For use in ward**", secs[2].markdown)
		self.assertIn("**1. Not a heading**", secs[3].markdown)

	def test_unnumbered_heading_never_parents_a_numbered_heading(self):
		pages = [(1, "# Contraindications\na\n# 5.8.4.2 Patient Assessment\nb\n### 5.8.5 Restraints\nc")]
		paths = {s.title: s.hierarchy_path for s in sectionize(pages)}
		self.assertEqual(paths["5.8.4.2 Patient Assessment"], ["5.8.4.2 Patient Assessment"])
		self.assertEqual(paths["5.8.5 Restraints"], ["5.8.5 Restraints"])

	def test_numbered_list_headings_nest_under_a_deep_numbered_section(self):
		pages = [
			(1, "#### 5.9.2.2 Staff tested for HIV\na\n# 1. Drug Regimen\nb\n# 2. Testing for HBsAg\nc"),
			(2, "# 6.1 PROTOCOLS\n## 6.1.1 IgA nephropathy\nd"),
		]
		paths = {s.title: s.hierarchy_path for s in sectionize(pages)}
		self.assertEqual(paths["1. Drug Regimen"], ["5.9.2.2 Staff tested for HIV", "1. Drug Regimen"])
		self.assertEqual(
			paths["2. Testing for HBsAg"], ["5.9.2.2 Staff tested for HIV", "2. Testing for HBsAg"]
		)
		self.assertEqual(paths["6.1.1 IgA nephropathy"], ["6.1 PROTOCOLS", "6.1.1 IgA nephropathy"])

	def test_emphasis_is_stripped_from_heading_titles(self):
		pages = [
			(1, "## _**Verbal Orders**_\nbody"),
			(2, "## **2. Procedures**\np\n## **2.1 Scope**\nq"),
		]
		secs = sectionize(pages)
		by_title = {s.title: s.level for s in secs}
		self.assertIn("Verbal Orders", by_title)
		self.assertEqual(by_title["2. Procedures"], 1)
		self.assertEqual(by_title["2.1 Scope"], 2)
		self.assertFalse(any("*" in s.title or s.title.startswith("_") for s in secs))

	def test_clean_pages_strips_varying_boilerplate_before_sectionizing(self):
		pages = [(1, "## 1. Intro\nbody\nPg 1 of 2"), (2, "## 2. Next\nbody\nPg 2 of 2")]
		secs = sectionize(clean_pages(pages))
		self.assertTrue(all("Pg" not in s.markdown for s in secs))
		self.assertEqual([s.title for s in secs], ["1. Intro", "2. Next"])

	def test_clean_pages_strips_signoff_footer_block(self):
		footer = "|**Prepared by - Dr. A**|**Issued by: QMC**|**Approved by - Dr. B**|\n|---|---|---|"
		pages = [(1, f"## 1. Intro\nreal body\n{footer}"), (2, f"## 2. Next\nmore body\n{footer}")]
		cleaned = dict(clean_pages(pages))
		for md in cleaned.values():
			self.assertNotIn("Prepared by", md)
			self.assertNotIn("Issued by", md)
			self.assertNotIn("|---|---|---|", md)
		self.assertIn("real body", cleaned[1])
		self.assertIn("more body", cleaned[2])

	def test_clean_pages_strips_page_numbers_and_headless_separator_rows(self):
		header = "NEPHROLOGY MANUAL\n\n| MAN/RPT/NEPH/001 | Ver.: 1 | Pg. {page} of 398 |\n|---|---|---|"
		table = "| Health worker | Action |\n|---|---|\n| Antibody 100 | Reassure |"
		pages = [
			(1, header.format(page=121) + f"\n\n## 5.7 Billing\n\n- Pay 2500 by noon.\n\n{table}\n\n121"),
			(2, header.format(page=122) + "\n\n- Second page item.\n\n122"),
			(3, header.format(page=123) + "\n\n- Third page item.\n\n123"),
		]
		cleaned = dict(clean_pages(pages))
		self.assertFalse(cleaned[1].startswith("|"))
		self.assertEqual(cleaned[2].strip(), "- Second page item.")
		self.assertNotIn("121", cleaned[1])
		self.assertIn(f"- Pay 2500 by noon.\n\n{table}", cleaned[1])

	def test_clean_pages_rejoins_a_sentence_split_by_a_page_break(self):
		pages = [
			(1, "- Organs are retrieved after withdrawal of life-sustaining\n\n132"),
			(
				2,
				"measures; the kidneys may be recovered.\n\n## 5.8.12 Grievances\n\n- Last item ends.\n\n133",
			),
			(3, "- New item starts the page.\n\n134"),
		]
		cleaned = dict(clean_pages(pages))
		self.assertEqual(
			cleaned[1],
			"- Organs are retrieved after withdrawal of life-sustaining measures; the kidneys may be recovered.",
		)
		self.assertNotIn("measures", cleaned[2])
		self.assertIn("## 5.8.12 Grievances", cleaned[2])
		self.assertEqual(cleaned[3], "- New item starts the page.")

	def test_title_is_clipped_to_the_storable_length(self):
		long_title = "DETERMINATION OF RESIDENTIAL STATUS OF " + "HINDU UNDIVIDED FAMILY " * 8
		secs = sectionize([(1, f"## {long_title}\nbody")])
		self.assertLessEqual(len(secs[0].title), MAX_TITLE_LENGTH)
		self.assertTrue(secs[0].title.endswith("…"))
		self.assertTrue(secs[0].title.startswith("DETERMINATION OF RESIDENTIAL STATUS"))
		self.assertNotIn(" …", secs[0].title)

	def test_clipped_title_fits_the_data_column(self):
		self.assertEqual(MAX_TITLE_LENGTH, frappe.db.VARCHAR_LEN)

	def test_long_titled_sections_all_reach_the_database(self):
		pages = [
			(1, "## Chapter One\nfirst"),
			(2, f"## {'X' * 200}\nsecond"),
			(3, "## Chapter Three\nthird"),
		]
		secs = sectionize(pages)
		self.assertEqual(len(secs), 3)
		self.assertEqual(secs[-1].title, "Chapter Three")
		self.assertEqual(max(s.page_end for s in secs), 3)

	def test_running_page_header_opens_one_section_not_one_per_page(self):
		pages = [
			(1, "# TRANSFER PRICING\n## Arm's Length Price\nalp body"),
			(2, "# TRANSFER PRICING\nmore alp body"),
			(3, "# TRANSFER PRICING\nstill more alp body"),
		]
		secs = sectionize(pages)
		self.assertEqual([s.title for s in secs], ["TRANSFER PRICING", "Arm's Length Price"])
		alp = secs[1]
		self.assertEqual(alp.page_end, 3)
		self.assertIn("still more alp body", alp.markdown)
		self.assertNotIn("TRANSFER PRICING", alp.markdown)

	def test_heading_repeated_mid_page_is_not_a_running_header(self):
		pages = [
			(page, f"## Chapter {page}\nlead in\nfiller\nfiller\n## Notes\nnote body") for page in (1, 2, 3)
		]
		self.assertNotIn("Notes", running_header_titles(pages))
		self.assertEqual(len([s for s in sectionize(pages) if s.title == "Notes"]), 3)

	def test_heading_on_two_pages_is_not_a_running_header(self):
		pages = [(1, "# Intro\na"), (2, "# Intro\nb")]
		self.assertEqual(running_header_titles(pages), set())

	def test_clean_pages_keeps_data_row_mentioning_approved_by_once(self):
		table = "| Step | Status |\n|---|---|\n| Reviewed and approved by committee | done |"
		pages = [(1, f"## 1. Audit\n{table}")]
		cleaned = dict(clean_pages(pages))
		self.assertIn("approved by committee", cleaned[1])
		self.assertIn("|---|---|", cleaned[1])

	def test_clean_pages_strips_signoff_footer_written_as_text(self):
		pipes = "Prepared by: Dr. A, Dr. B | Issued by: QMC | Approved by: Dr. C"
		merged = "**Prepared by: Dr. A, Dr. Issued by: QMC Approved by: Dr. C B** "
		paragraphs = "Prepared by: Dr. A, Dr. B\n\nIssued by: QMC\n\nApproved by: Dr. C"
		pages = [
			(1, f"## DONOR WITH OBESITY\nreal body\n\n{pipes}"),
			(2, f"- more body\n\n{merged}\n"),
			(3, f"## DIABETIC PATIENT\n\n{paragraphs}\n\nThe protocol is approved by the HOD."),
		]
		cleaned = dict(clean_pages(pages))
		for md in cleaned.values():
			self.assertNotIn("Prepared by", md)
			self.assertNotIn("Issued by", md)
		self.assertIn("real body", cleaned[1])
		self.assertIn("- more body", cleaned[2])
		self.assertIn("approved by the HOD", cleaned[3])


class TestEmptySectionsAreFlagged(FrappeTestCase):
	def test_section_without_markdown_is_chunked_as_title_only(self):
		rows = [
			{
				"name": "SEC-EMPTY",
				"source_document": "SD-1",
				"title": "Concessional tax rates under section 115BAC(1A)",
				"hierarchy_path": "Basic Concepts",
				"markdown": "",
				"page_start": 8,
				"page_end": 8,
			},
			{
				"name": "SEC-BODY",
				"source_document": "SD-1",
				"title": "Conditions",
				"hierarchy_path": "Basic Concepts > Conditions",
				"markdown": "The conditions are as follows.",
				"page_start": 8,
				"page_end": 8,
			},
		]
		chunks = build_chunks(rows, {"SD-1": {"title": "Referencer", "project": "PRJ-1"}}, {})
		self.assertEqual([chunk.section for chunk in chunks], ["SEC-EMPTY", "SEC-BODY"])
		self.assertEqual([chunk.title_only for chunk in chunks], [True, False])
		self.assertEqual(chunks[0].text, "Concessional tax rates under section 115BAC(1A)")


class TestSectionizeIntegration(FrappeTestCase):
	def _sections(self, sd):
		return frappe.get_all(
			"Source Section",
			filters={"source_document": sd},
			fields=[
				"name",
				"title",
				"level",
				"parent_source_section",
				"hierarchy_path",
				"is_group",
				"lft",
				"rgt",
				"page_start",
				"page_end",
			],
			order_by="lft asc",
		)

	def test_parse_builds_section_tree(self):
		path = Path(tempfile.mkdtemp()) / "sample.pdf"
		_make_sample_pdf(str(path))
		sd = parse_pdf(str(path), title="Sectionize Test")

		secs = self._sections(sd)
		self.assertTrue(secs, "parse produced no sections")
		roots = [s for s in secs if not s.parent_source_section]
		self.assertTrue(roots)
		for s in secs:
			self.assertGreaterEqual(s.level, 1)
			self.assertTrue(s.hierarchy_path)
			self.assertLess(s.lft, s.rgt)
		titles = " ".join(s.title for s in secs)
		self.assertIn("Procedures", titles)

	def test_remediation_rebuilds_tree_over_adopted_markdown(self):
		path = Path(tempfile.mkdtemp()) / "sample.pdf"
		_make_sample_pdf(str(path))
		with (
			patch("wikify.engine.llm.has_openrouter", return_value=True),
			patch("wikify.engine.llm.chat_completion", side_effect=_fake_chat),
		):
			sd = parse_pdf(str(path), title="Sectionize Remediate Test")

		before = {s.name for s in self._sections(sd)}
		self.assertTrue(before)

		with (
			patch("wikify.engine.llm.has_openrouter", return_value=True),
			patch("wikify.engine.llm.chat_completion", side_effect=_fake_chat),
			patch("wikify.engine.remediate.clean_markdown", side_effect=lambda md, model=None: md),
			patch("wikify.engine.remediate.vlm.parse_page_image", return_value=_MERMAID),
		):
			result = remediate_pdf(sd, str(path), scope="all")

		after = self._sections(sd)
		self.assertTrue(after, "remediation left an empty tree")
		self.assertEqual(result["sections"], len(after))
		self.assertFalse(before & {s.name for s in after}, "old section rows not replaced")
		self.assertTrue(any("Procedures" in s.title for s in after))


def _tree_section(title, path):
	return Section(
		title=title,
		level=len(path),
		hierarchy_path=path,
		page_start=1,
		page_end=1,
		markdown=f"body of {title}",
	)


def _parents_by_hierarchy_path(sections):
	path_to_index = {}
	parents = []
	for index, section in enumerate(sections):
		parents.append(path_to_index.get(tuple(section.hierarchy_path[:-1])))
		path_to_index[tuple(section.hierarchy_path)] = index
	return parents


class TestSectionTreeKeying(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Tree Keying Test"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(_cleanup.delete_document, self.sd.name)

	def _rows(self):
		return frappe.get_all(
			"Source Section",
			filters={"source_document": self.sd.name},
			fields=["name", "title", "parent_source_section", "is_group", "sort_order"],
			order_by="sort_order asc",
		)

	def _children_of(self, rows, name):
		return [row.title for row in rows if row.parent_source_section == name]

	def test_duplicate_sibling_titles_keep_their_own_children(self):
		store.replace_sections(
			self.sd.name,
			[
				_tree_section("Chapter", ["Chapter"]),
				_tree_section("Procedure", ["Chapter", "Procedure"]),
				_tree_section("First steps", ["Chapter", "Procedure", "First steps"]),
				_tree_section("Procedure", ["Chapter", "Procedure"]),
				_tree_section("Second steps", ["Chapter", "Procedure", "Second steps"]),
			],
		)
		rows = self._rows()
		first, second = rows[1], rows[3]
		self.assertNotEqual(first.name, second.name)
		self.assertEqual(self._children_of(rows, first.name), ["First steps"])
		self.assertEqual(self._children_of(rows, second.name), ["Second steps"])
		self.assertEqual([first.is_group, second.is_group], [1, 1])

	def test_repeated_root_titles_do_not_collapse(self):
		sections = []
		for index in range(35):
			sections.append(_tree_section("NEPHROLOGY MANUAL", ["NEPHROLOGY MANUAL"]))
			sections.append(_tree_section(f"Policy {index}", ["NEPHROLOGY MANUAL", f"Policy {index}"]))
		store.replace_sections(self.sd.name, sections)

		rows = self._rows()
		roots = [row for row in rows if not row.parent_source_section]
		self.assertEqual(len(roots), 35)
		self.assertEqual(len({row.name for row in roots}), 35)
		for index, root in enumerate(roots):
			self.assertEqual(self._children_of(rows, root.name), [f"Policy {index}"])
			self.assertEqual(root.is_group, 1)

	def test_childless_section_sharing_a_title_is_not_a_group(self):
		store.replace_sections(
			self.sd.name,
			[
				_tree_section("Definition", ["Definition"]),
				_tree_section("Scope", ["Definition", "Scope"]),
				_tree_section("Definition", ["Definition"]),
			],
		)
		rows = self._rows()
		self.assertEqual([row.is_group for row in rows], [1, 0, 0])

	def test_unambiguous_tree_matches_the_hierarchy_path_keying(self):
		sections = [
			_tree_section("1. Alpha", ["1. Alpha"]),
			_tree_section("1.1 Alpha-One", ["1. Alpha", "1.1 Alpha-One"]),
			_tree_section("1.1.1 Alpha-Deep", ["1. Alpha", "1.1 Alpha-One", "1.1.1 Alpha-Deep"]),
			_tree_section("1.2 Alpha-Two", ["1. Alpha", "1.2 Alpha-Two"]),
			_tree_section("2. Beta", ["2. Beta"]),
			_tree_section("2.1 Beta-One", ["2. Beta", "2.1 Beta-One"]),
		]
		self.assertEqual(
			store.resolve_parent_indexes(sections),
			_parents_by_hierarchy_path(sections),
		)

		store.replace_sections(self.sd.name, sections)
		rows = self._rows()
		by_title = {row.title: row for row in rows}
		self.assertEqual(
			[row.parent_source_section for row in rows],
			[
				None,
				by_title["1. Alpha"].name,
				by_title["1.1 Alpha-One"].name,
				by_title["1. Alpha"].name,
				None,
				by_title["2. Beta"].name,
			],
		)
		self.assertEqual([row.is_group for row in rows], [1, 1, 0, 0, 1, 0])

	def test_orphan_depth_without_a_parent_stays_a_root(self):
		sections = [_tree_section("Deep", ["Missing", "Deep"])]
		self.assertEqual(store.resolve_parent_indexes(sections), [None])
		store.replace_sections(self.sd.name, sections)
		self.assertEqual([row.parent_source_section for row in self._rows()], [None])
