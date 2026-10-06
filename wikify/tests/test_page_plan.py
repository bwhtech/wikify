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


def _outline():
	return [
		_sec(["Preamble"], 1),
		_sec(["5.8 Care"], 2, markdown=""),
		_sec(["5.8 Care", "5.8.1 Chaperone"], 2),
		_sec(["5.8 Care", "5.8.4 Sedation"], 3),
		_sec(["5.8 Care", "5.8.4 Sedation", "5.8.4.1 Indications"], 4),
		_sec(["5.8 Care", "5.8.4 Sedation", "5.8.4.1 Indications", "Contraindications"], 4),
		_sec(["5.8 Care", "5.8.4 Sedation", "5.8.4.2 Consents"], 5, 6),
		_sec(["5.8 Care", "5.8.9 Grievances"], 7),
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
			_sec(["Guide", "Setup"], 1),
			_sec(["Guide", "Setup", "Prerequisites"], 2),
			_sec(["Guide", "Usage"], 3),
		]
		self.assertEqual(_titles(plan_pages(sections)), ["Guide", "Setup", "Usage"])

	def test_a_lone_sub_section_folds_into_its_parent(self):
		sections = [
			_sec(["5.7 Billing"], 1, markdown=""),
			_sec(["5.7 Billing", "5.7.2 Inpatient billing"], 1, 2),
			_sec(["5.8 Care"], 2),
			_sec(["5.8 Care", "5.8.1 Chaperone"], 2),
			_sec(["5.8 Care", "5.8.1 Chaperone", "5.8.1.1 Escort"], 3),
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
		sections = [_sec(path[:depth], 1) for depth in range(1, 9)]
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
			_titles(pages), ["Preamble", "5.8 Care", "5.8.4 Sedation", "5.8.9 Grievances", "6.1 Protocols"]
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
		self.assertEqual(_titles(pages), ["Preamble", "5.8 Care", "5.8.4 Sedation", "6.1 Protocols"])

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
			["Preamble", "5.8 Care", "5.8.1 Chaperone", "6.1 Protocols", "6.1.2 Lupus nephritis"],
		)
