# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import json
from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from wikify.engine.loader import page_plan
from wikify.engine.loader.page_plan import plan_pages
from wikify.engine.loader.sectionizer import Section


def _sec(path, page_start, page_end=None, markdown=None):
	return Section(
		title=path[-1],
		level=len(path),
		hierarchy_path=path,
		page_start=page_start,
		page_end=page_end or page_start,
		markdown=markdown if markdown is not None else f"body of {path[-1]}",
	)


def _words(count):
	return " ".join(["word"] * count)


def _outline():
	return [
		_sec(["Preamble"], 1, markdown=_words(60)),
		_sec(["5.8 Care"], 2, markdown=""),
		_sec(["5.8 Care", "5.8.1 Chaperone"], 2),
		_sec(["5.8 Care", "5.8.4 Sedation"], 3),
		_sec(["5.8 Care", "5.8.4 Sedation", "5.8.4.1 Indications"], 4),
		_sec(["5.8 Care", "5.8.4 Sedation", "5.8.4.1 Indications", "Contraindications"], 4),
		_sec(["5.8 Care", "5.8.4 Sedation", "5.8.4.2 Consents"], 5, 6),
		_sec(["5.8 Care", "5.8.9 Grievances"], 7, markdown=_words(30)),
		_sec(["5.8 Care", "5.8.9 Grievances", "1. Drug Regimen"], 7),
		_sec(["6.1 Protocols"], 8, markdown=""),
		_sec(["6.1 Protocols", "6.1.2 Lupus nephritis"], 8),
		_sec(["6.1 Protocols", "6.1.2 Lupus nephritis", "Class IV"], 9, markdown=""),
		_sec(["6.1 Protocols", "6.1.2 Lupus nephritis", "Class V"], 10),
	]


def _llm_reply(payload):
	content = payload if isinstance(payload, str) else json.dumps(payload)
	return {"choices": [{"message": {"content": content}}]}


def _titles(sections):
	return [section.title for section in sections]


class TestFallbackPagePlan(FrappeTestCase):
	def test_pages_are_the_top_two_numbered_levels_below_the_chapter(self):
		pages = plan_pages(_outline())
		self.assertEqual(
			_titles(pages),
			[
				"Preamble",
				"5.8 Care",
				"5.8.1 Chaperone",
				"5.8.4 Sedation",
				"5.8.9 Grievances",
				"6.1 Protocols",
				"6.1.2 Lupus nephritis",
			],
		)

	def test_unnumbered_document_keeps_its_top_two_heading_levels(self):
		sections = [
			_sec(["Guide"], 1),
			_sec(["Guide", "Setup"], 2),
			_sec(["Guide", "Setup", "Prerequisites"], 3),
			_sec(["Guide", "Usage"], 4),
		]
		self.assertEqual(_titles(plan_pages(sections)), ["Guide", "Setup", "Usage"])

	def test_a_lone_sub_section_folds_into_its_parent(self):
		sections = [
			_sec(["5.7 Billing"], 1, markdown=""),
			_sec(["5.7 Billing", "5.7.2 Inpatient billing"], 1, 2),
			_sec(["5.8 Care"], 2),
			_sec(["5.8 Care", "5.8.1 Chaperone"], 3),
			_sec(["5.8 Care", "5.8.1 Chaperone", "5.8.1.1 Escort"], 4),
		]
		pages = plan_pages(sections)
		self.assertEqual(_titles(pages), ["5.7 Billing", "5.8 Care", "5.8.1 Chaperone"])
		self.assertEqual(pages[0].markdown, "## 5.7.2 Inpatient billing\n\nbody of 5.7.2 Inpatient billing")

	def test_folded_sections_become_headings_in_document_order(self):
		sedation = plan_pages(_outline())[3]
		self.assertEqual(
			sedation.markdown,
			"body of 5.8.4 Sedation\n\n"
			"## 5.8.4.1 Indications\n\nbody of 5.8.4.1 Indications\n\n"
			"### Contraindications\n\nbody of Contraindications\n\n"
			"## 5.8.4.2 Consents\n\nbody of 5.8.4.2 Consents",
		)
		self.assertEqual((sedation.page_start, sedation.page_end), (3, 6))

	def test_an_empty_folded_section_keeps_its_heading(self):
		lupus = plan_pages(_outline())[-1]
		self.assertEqual(
			lupus.markdown,
			"body of 6.1.2 Lupus nephritis\n\n## Class IV\n\n## Class V\n\nbody of Class V",
		)
		self.assertEqual(lupus.page_end, 10)

	def test_heading_depth_is_capped_at_six(self):
		path = [f"Level {depth}" for depth in range(1, 9)]
		sections = [_sec(path[:depth], min(depth, 2)) for depth in range(1, 9)]
		child = plan_pages(sections)[1]
		self.assertEqual(child.title, "Level 2")
		self.assertIn("###### Level 7\n\nbody of Level 7\n\n###### Level 8", child.markdown)

	def test_hierarchy_of_the_remaining_pages_is_unchanged(self):
		pages = plan_pages(_outline())
		self.assertEqual(pages[-1].hierarchy_path, ["6.1 Protocols", "6.1.2 Lupus nephritis"])


class TestLlmPagePlan(FrappeTestCase):
	def _plan(self, reply, sections=None):
		with patch.object(page_plan.llm, "chat_completion", return_value=reply) as chat:
			pages = plan_pages(sections or _outline(), "Hospital manual", use_llm=True)
		return pages, chat

	def test_llm_choice_decides_the_pages(self):
		pages, chat = self._plan(_llm_reply({"pages": [2, 3, 7, 10]}))
		self.assertEqual(
			_titles(pages),
			[
				"Preamble",
				"5.8 Care",
				"5.8.1 Chaperone",
				"5.8.4 Sedation",
				"5.8.9 Grievances",
				"6.1 Protocols",
				"6.1.2 Lupus nephritis",
			],
		)
		self.assertEqual(chat.call_args.kwargs["label"], "page_plan")
		self.assertEqual(chat.call_args.kwargs["response_format"], {"type": "json_object"})
		prompt = chat.call_args.args[1][0]["content"]
		self.assertIn("Hospital manual", prompt)
		self.assertIn("4 | 3 | 5.8.4.1 Indications | p4-4 | 4 | 7 | 1", prompt)

	def test_a_json_encoded_page_list_is_accepted(self):
		pages, _ = self._plan(_llm_reply({"pages": "[3, 7]"}))
		self.assertEqual(
			_titles(pages),
			[
				"Preamble",
				"5.8 Care",
				"5.8.1 Chaperone",
				"5.8.4 Sedation",
				"5.8.9 Grievances",
				"6.1 Protocols",
			],
		)

	def test_top_level_sections_are_pages_even_when_the_llm_skips_them(self):
		pages, _ = self._plan(_llm_reply({"pages": []}))
		self.assertEqual(_titles(pages), ["Preamble", "5.8 Care", "6.1 Protocols"])
		self.assertIn("## 5.8.1 Chaperone", pages[1].markdown)
		self.assertIn("### 5.8.4.1 Indications", pages[1].markdown)

	def test_a_page_under_a_folded_parent_folds_with_it(self):
		pages, _ = self._plan(_llm_reply({"pages": [4, 5]}))
		self.assertEqual(_titles(pages), ["Preamble", "5.8 Care", "6.1 Protocols"])

	def test_an_empty_page_without_page_children_folds_into_its_parent(self):
		pages, _ = self._plan(_llm_reply({"pages": [10, 11]}))
		self.assertEqual(_titles(pages)[-2:], ["6.1 Protocols", "6.1.2 Lupus nephritis"])
		self.assertIn("## Class IV\n\n## Class V", pages[-1].markdown)

	def test_unusable_indices_are_ignored(self):
		pages, _ = self._plan(_llm_reply({"pages": ["2", 99, -1, True, 3]}))
		self.assertEqual(
			_titles(pages), ["Preamble", "5.8 Care", "5.8.4 Sedation", "5.8.9 Grievances", "6.1 Protocols"]
		)

	def test_invalid_reply_falls_back_to_the_numbering_rule(self):
		expected = _titles(plan_pages(_outline()))
		for reply in (_llm_reply("not json"), _llm_reply({"pages": "all"}), _llm_reply({})):
			with patch.object(page_plan.frappe, "log_error"):
				pages, _ = self._plan(reply)
			self.assertEqual(_titles(pages), expected)

	def test_llm_error_falls_back_to_the_numbering_rule(self):
		with (
			patch.object(page_plan.llm, "chat_completion", side_effect=RuntimeError("down")),
			patch.object(page_plan.frappe, "log_error") as log_error,
		):
			pages = plan_pages(_outline(), use_llm=True)
		self.assertEqual(_titles(pages), _titles(plan_pages(_outline())))
		log_error.assert_called_once()

	def test_large_outlines_are_sent_per_chapter(self):
		replies = [_llm_reply({"pages": [2]}), _llm_reply({"pages": [10]})]
		with (
			patch.object(page_plan, "OUTLINE_BATCH_LINES", 9),
			patch.object(page_plan.llm, "chat_completion", side_effect=replies) as chat,
		):
			pages = plan_pages(_outline(), use_llm=True)
		self.assertEqual(chat.call_count, 2)
		self.assertNotIn("6.1 Protocols", chat.call_args_list[0].args[1][0]["content"])
		self.assertEqual(
			_titles(pages),
			[
				"Preamble",
				"5.8 Care",
				"5.8.1 Chaperone",
				"5.8.4 Sedation",
				"5.8.9 Grievances",
				"6.1 Protocols",
				"6.1.2 Lupus nephritis",
			],
		)

	def test_an_unnumbered_top_level_fragment_folds_into_the_page_before_it(self):
		sections = [
			_sec(["Preamble"], 1, markdown=_words(60)),
			_sec(["IMMEDIATE PRE-OPERATIVE PROTOCOLS"], 7, 9, markdown=_words(60)),
			_sec(["Pre transplant day"], 9, markdown=""),
			_sec(["PRE-TRANSPLANT RECIPIENT ORDER SHEET"], 9, 10, markdown=_words(60)),
			_sec(["6.3.3.2 Donor evaluation"], 11),
		]
		pages, _ = self._plan(_llm_reply({"pages": [1, 2]}), sections)
		self.assertEqual(
			_titles(pages), ["Preamble", "IMMEDIATE PRE-OPERATIVE PROTOCOLS", "6.3.3.2 Donor evaluation"]
		)
		self.assertIn("## Pre transplant day\n\n## PRE-TRANSPLANT RECIPIENT ORDER SHEET", pages[1].markdown)
		self.assertEqual(pages[1].page_end, 10)
		self.assertEqual(
			_titles(plan_pages(sections)),
			[
				"Preamble",
				"IMMEDIATE PRE-OPERATIVE PROTOCOLS",
				"PRE-TRANSPLANT RECIPIENT ORDER SHEET",
				"6.3.3.2 Donor evaluation",
			],
		)

	def test_a_page_spanning_too_many_pdf_pages_splits_at_its_numbered_children(self):
		capd = "6.2.2 CAPD"
		initiation = "6.2.2.1 Initiation"
		sections = [
			_sec([capd], 4, markdown=""),
			_sec([capd, "6.2.2.0 Introduction"], 4, markdown=_words(80)),
			_sec([capd, initiation], 4, markdown=""),
			_sec([capd, initiation, "6.2.2.1.1 Counselling"], 4, 5, markdown=_words(200)),
			_sec([capd, initiation, "6.2.2.1.2 Pre-op evaluation"], 5, 17, markdown=_words(2000)),
			_sec([capd, initiation, "6.2.2.1.3 Intra-op"], 17, 20, markdown=_words(800)),
			_sec([capd, "6.2.2.2 Note"], 20, markdown=_words(10)),
		]
		pages, _ = self._plan(_llm_reply({"pages": []}), sections)
		self.assertEqual(
			_titles(pages),
			[
				capd,
				"6.2.2.0 Introduction",
				initiation,
				"6.2.2.1.1 Counselling",
				"6.2.2.1.2 Pre-op evaluation",
				"6.2.2.1.3 Intra-op",
			],
		)
		self.assertIn("6.2.2.2 Note", pages[-1].markdown)
		self.assertNotIn("6.2.2.2 Note", pages[0].markdown)

	def test_a_long_page_without_numbered_children_splits_at_its_sub_headings(self):
		access = "6.2.1.4 PROTOCOL FOR VASCULAR ACCESS"
		sections = [
			_sec([access], 3, markdown=""),
			_sec([access, "AV FISTULA/AV GRAFT"], 3, markdown=_words(60)),
			_sec([access, "A. Skin Preparation"], 3, 4, markdown=_words(230)),
			_sec([access, "CENTRAL VENOUS CATHETER"], 5, markdown=""),
			_sec([access, "CATHETER CARE"], 5, markdown=_words(260)),
			_sec([access, "I. EXIT SITE CARE"], 6, 7, markdown=_words(140)),
			_sec([access, "DIALYZER REUSE"], 7, markdown=_words(30)),
			_sec([access, "IDENTIFYING AND MANAGING COMPLICATIONS"], 7, markdown=""),
			_sec([access, "1. INTRA DIALYTIC HYPOTENSION"], 7, 8, markdown=_words(110)),
			_sec([access, "2. HYPERKALEMIA"], 9, 10, markdown=_words(80)),
			_sec([access, "Peritoneal Dialysis"], 11, markdown=""),
			_sec([access, "1 Dialysis Prescription"], 11, markdown=_words(30)),
			_sec([access, "2 Peritonitis"], 11, markdown=_words(90)),
			_sec([access, "4 Exit Site Infection"], 11, markdown=_words(40)),
		]
		pages, _ = self._plan(_llm_reply({"pages": []}), sections)
		self.assertEqual(
			_titles(pages),
			[
				access,
				"AV FISTULA/AV GRAFT",
				"CENTRAL VENOUS CATHETER",
				"Peritoneal Dialysis",
			],
		)
		self.assertEqual(pages[0].markdown, "")
		self.assertIn("## CATHETER CARE", pages[2].markdown)
		self.assertIn(
			f"## DIALYZER REUSE\n\n{_words(30)}\n\n## IDENTIFYING AND MANAGING COMPLICATIONS",
			pages[2].markdown,
		)
		self.assertEqual(pages[3].hierarchy_path, [access, "Peritoneal Dialysis"])
		self.assertEqual((pages[3].page_start, pages[3].page_end), (11, 11))

	def test_a_short_leading_preamble_joins_the_page_after_it(self):
		sections = [
			_sec(["Preamble"], 1, markdown="Saline dialysis is reserved for bleeding patients."),
			_sec(["6.2.1 POLICIES"], 2, markdown=""),
			_sec(["6.2.1 POLICIES", "6.2.1.3 ASSESSMENT"], 2),
			_sec(["6.2.1 POLICIES", "6.2.1.4 VASCULAR ACCESS"], 3),
		]
		pages, _ = self._plan(_llm_reply({"pages": [0, 1, 2, 3]}), sections)
		self.assertEqual(_titles(pages), ["6.2.1 POLICIES", "6.2.1.3 ASSESSMENT", "6.2.1.4 VASCULAR ACCESS"])
		self.assertEqual(pages[0].markdown, "")
		self.assertEqual(
			pages[1].markdown,
			"Saline dialysis is reserved for bleeding patients.\n\nbody of 6.2.1.3 ASSESSMENT",
		)

	def test_numbered_siblings_follow_the_majority_into_pages(self):
		sections = [
			_sec(["5.8 Policies on patient care"], 1, markdown=_words(20)),
			_sec(["5.8 Policies on patient care", "5.8.1 Chaperone"], 2, markdown=_words(90)),
			_sec(["5.8 Policies on patient care", "5.8.2 Antibiotic policy"], 3, markdown=_words(29)),
			_sec(["5.8 Policies on patient care", "5.8.3 Consent"], 4, markdown=_words(200)),
			_sec(["5.8 Policies on patient care", "5.8.4 Sedation"], 5, markdown=_words(300)),
			_sec(["5.8 Policies on patient care", "5.8.4 Sedation", "Contraindications"], 5),
		]
		pages, _ = self._plan(_llm_reply({"pages": [1, 3, 4]}), sections)
		self.assertEqual(
			_titles(pages),
			[
				"5.8 Policies on patient care",
				"5.8.1 Chaperone",
				"5.8.2 Antibiotic policy",
				"5.8.3 Consent",
				"5.8.4 Sedation",
			],
		)
		pages, _ = self._plan(_llm_reply({"pages": [4]}), sections)
		self.assertEqual(_titles(pages), ["5.8 Policies on patient care", "5.8.4 Sedation"])

	def test_numbered_siblings_after_a_page_are_pages_even_when_short(self):
		sections = [
			_sec(["2. Organogram"], 1, markdown=_words(20)),
			_sec(["2. Organogram", "2.1 The staff"], 1, markdown=_words(200)),
			_sec(["2. Organogram", "2.2 Hierarchy"], 2, markdown=_words(200)),
			_sec(["2. Organogram", "2.3 Staff list"], 3, markdown=_words(200)),
			_sec(["2. Organogram", "2.4 Laboratory staff"], 4, markdown=_words(60)),
			_sec(["2. Organogram", "2.5 Office staff"], 5, markdown=_words(60)),
			_sec(["2. Organogram", "2.6 Support staff"], 6, markdown=_words(25)),
			_sec(["2. Organogram", "2.7 Attendants"], 6, markdown=_words(5)),
		]
		pages, _ = self._plan(_llm_reply({"pages": [0, 2]}), sections)
		self.assertEqual(
			_titles(pages),
			[
				"2. Organogram",
				"2.2 Hierarchy",
				"2.3 Staff list",
				"2.4 Laboratory staff",
				"2.5 Office staff",
				"2.6 Support staff",
			],
		)
		self.assertNotIn("2.6 Support staff", pages[0].markdown + pages[-2].markdown)
		self.assertIn("## 2.7 Attendants", pages[-1].markdown)

	def test_a_long_page_never_splits_at_a_form_letterhead_or_a_step(self):
		training = "6.2.2.1.5 PD TRAINING PROCEDURE"
		sections = [
			_sec([training], 227, markdown=""),
			_sec([training, "CHRISTIAN MEDICAL COLLEGE"], 227, markdown=""),
			_sec([training, "DEPARTMENT OF NEPHROLOGY"], 227, markdown=""),
			_sec([training, "PD TRAINING OBJECTIVES"], 227, 228, markdown=_words(200)),
			_sec([training, "DAY WISE SCHEDULE OF TRAINING"], 229, 231, markdown=_words(300)),
			_sec([training, "CHRISTIAN MEDICAL COLLEGE VELLORE"], 232, markdown=""),
			_sec([training, "PROCEDURE MANUAL - NEPHROLOGY"], 232, markdown=""),
			_sec([training, "PD EXCHANGE PROCEDURE"], 232, 233, markdown=_words(229)),
			_sec([training, "Step 11: Check, measure, Record and Discard"], 233, 234, markdown=_words(191)),
			_sec([training, "DISCHARGE CHECKLIST"], 239, 240, markdown=_words(193)),
			_sec([training, "CONTACT INFORMATION SHEET"], 240, markdown=""),
			_sec([training, "CHRISTIAN MEDICAL COLLEGE"], 240, markdown=""),
			_sec([training, "DEPARTMENT OF NEPHROLOGY"], 240, markdown=_words(178)),
		]
		pages, _ = self._plan(_llm_reply({"pages": []}), sections)
		self.assertEqual(
			_titles(pages),
			[
				training,
				"PD TRAINING OBJECTIVES",
				"DAY WISE SCHEDULE OF TRAINING",
				"PD EXCHANGE PROCEDURE",
				"DISCHARGE CHECKLIST",
			],
		)
		self.assertIn("## Step 11: Check, measure, Record and Discard", pages[3].markdown)
		self.assertIn("## CONTACT INFORMATION SHEET", pages[4].markdown)

	def test_sections_sharing_a_pdf_page_share_a_wiki_page(self):
		sections = [
			_sec(["2. Organogram"], 25, markdown=_words(12)),
			_sec(["2. Organogram", "2.1 The staff"], 25, markdown=_words(50)),
			_sec(["2. Organogram", "2.2 Staff hierarchy"], 26, 28, markdown=_words(390)),
			_sec(["2. Organogram", "2.3 Staff list"], 29, 32, markdown=_words(1000)),
			_sec(["2. Organogram", "2.4 Laboratory staff"], 33, markdown=_words(5)),
			_sec(["2. Organogram", "2.5 Research staff"], 33, markdown=_words(5)),
			_sec(["2. Organogram", "2.6 Office staff"], 33, markdown=_words(25)),
			_sec(["2. Organogram", "2.7 Support staff"], 33, markdown=_words(30)),
		]
		pages, _ = self._plan(_llm_reply({"pages": [0, 1, 2, 3, 4, 5, 6, 7]}), sections)
		self.assertEqual(
			_titles(pages),
			["2. Organogram", "2.2 Staff hierarchy", "2.3 Staff list", "2.4 Laboratory staff"],
		)
		self.assertIn("## 2.1 The staff", pages[0].markdown)
		for title in ("2.5 Research staff", "2.6 Office staff", "2.7 Support staff"):
			self.assertIn(f"## {title}", pages[-1].markdown)

	def test_a_long_numbered_section_keeps_its_page_when_it_starts_mid_page(self):
		sections = [
			_sec(["3. Job descriptions"], 34, markdown=_words(80)),
			_sec(["3. Job descriptions", "3.1 Head of the department"], 34, 36, markdown=_words(600)),
			_sec(["3. Job descriptions", "3.2 Head of the unit"], 37, 38, markdown=_words(300)),
			_sec(["3. Job descriptions", "3.3 Consultant"], 38, 39, markdown=_words(180)),
			_sec(["3. Job descriptions", "3.4 Registrar"], 39, markdown=_words(90)),
		]
		pages, _ = self._plan(_llm_reply({"pages": [0, 1, 2, 3, 4]}), sections)
		self.assertEqual(
			_titles(pages),
			["3. Job descriptions", "3.1 Head of the department", "3.2 Head of the unit", "3.3 Consultant"],
		)
		self.assertIn("## 3.4 Registrar", pages[-1].markdown)
