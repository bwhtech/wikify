# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import json
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import review as review_api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.engine.review import rules
from wikify.engine.review.suggestions import alternative
from wikify.jobs import review as review_job
from wikify.tests.test_ask_history import make_project, make_user

THRESHOLD = 0.70
CLEAN_TABLE = "| a | b |\n|---|---|\n| 1 | 2 |"
BROKEN_TABLE = "| a | b |\n| 1 | 2 |"


def page(page_no, **values):
	row = {
		"name": f"page-{page_no}",
		"page_no": page_no,
		"kind": "text",
		"image": None,
		"composite": 0.95,
		"canonical_composite": 0.95,
		"canonical_source": "baseline",
		"canonical_markdown": "Plain page text that is long enough to count as content.",
		"baseline_markdown": "Plain page text that is long enough to count as content.",
		"notes": None,
		"text_recall": 0.99,
		"extra_ratio": 0.01,
		"table_score": None,
		"judge_score": None,
		"remediation_method": None,
		"remediation_markdown": None,
		"remediation_composite": None,
		"remediation_notes": None,
	}
	return frappe._dict({**row, **values})


def section(name, **values):
	row = {
		"name": name,
		"title": name.title(),
		"page_start": 1,
		"page_end": 1,
		"is_group": 0,
		"include_in_wiki": 1,
		"section_type": "clinical_protocols",
		"lint_issues": None,
		"markdown": "Body",
	}
	return frappe._dict({**row, **values})


def detect(pages, sections=()):
	return rules.detect("doc", pages, list(sections), THRESHOLD, "other")


def codes(issues):
	return sorted(i["code"] for i in issues)


class TestReviewRules(FrappeTestCase):
	def test_clean_document_has_no_issues(self):
		self.assertEqual(detect([page(1), page(2)], [section("intro")]), [])

	def test_low_score_with_missing_text_blocks(self):
		low = page(3, canonical_composite=0.5, notes="low text recall — possible dropped content")
		(found,) = detect([page(1), low])
		self.assertEqual(found["code"], "page_low_score")
		self.assertEqual(found["severity"], "blocks")
		self.assertEqual(found["title"], "Some text on this page is missing")
		self.assertEqual(found["pages"], [3])

	def test_low_score_about_a_table_is_a_table_review(self):
		low = page(1, canonical_composite=0.6, notes="table present but not reproduced")
		(found,) = detect([low])
		self.assertEqual((found["severity"], found["category"]), ("review", "tables"))

	def test_many_vision_failures_group_into_one_document_item(self):
		failed = [page(n, remediation_notes="vlm failed: 400 Bad Request") for n in range(1, 13)]
		issues = detect(failed)
		self.assertEqual(codes(issues), ["doc_vision_failed"])
		self.assertIn("400 Bad Request", issues[0]["reason"])

	def test_few_vision_failures_stay_per_page(self):
		pages = [page(n) for n in range(1, 21)]
		pages[4] = page(5, remediation_notes="vlm failed: timeout")
		self.assertEqual(codes(detect(pages)), ["page_vision_failed"])

	def test_vision_failure_on_an_adopted_page_is_not_an_issue(self):
		adopted = page(1, canonical_source="cleanup", remediation_notes="vlm failed: timeout")
		self.assertEqual(detect([adopted, *[page(n) for n in range(2, 10)]]), [])

	def test_visual_page_with_only_an_image(self):
		visual = page(1, kind="visual", canonical_markdown="![Source page](/files/p1.png)")
		self.assertEqual(codes(detect([visual])), ["page_image_only"])

	def test_lint_issues_make_a_blocking_section_item(self):
		lint = json.dumps([{"code": "missing_separator", "line": 1, "message": "x"}])
		(found,) = detect([page(1)], [section("intro", lint_issues=lint, markdown=BROKEN_TABLE)])
		self.assertEqual(
			(found["code"], found["severity"], found["category"]), ("section_lint", "blocks", "tables")
		)
		self.assertEqual(found["title"], "A table lost its header line")

	def test_unclosed_fence_alone_is_formatting(self):
		lint = json.dumps([{"code": "unclosed_fence", "line": 0, "message": "x"}])
		(found,) = detect([page(1)], [section("intro", lint_issues=lint)])
		self.assertEqual(found["category"], "formatting")

	def test_mostly_untyped_sections_group_into_one_document_item(self):
		sections = [section(f"s{n}", section_type="other") for n in range(4)]
		self.assertEqual(codes(detect([page(1)], sections)), ["doc_unclassified"])

	def test_a_single_untyped_section_is_its_own_item(self):
		sections = [section(f"s{n}") for n in range(6)] + [section("odd", section_type="other")]
		self.assertEqual(codes(detect([page(1)], sections)), ["section_other_type"])

	def test_fingerprint_follows_the_markdown(self):
		low = page(1, canonical_composite=0.5)
		changed = page(1, canonical_composite=0.5, canonical_markdown="Different text")
		self.assertNotEqual(detect([low])[0]["fingerprint"], detect([changed])[0]["fingerprint"])


class TestCandidates(FrappeTestCase):
	def test_baseline_page_offers_the_unadopted_remediation(self):
		row = page(1, remediation_method="cleanup", remediation_markdown="Cleaner text")
		self.assertEqual(alternative(row)["source"], "cleanup")

	def test_adopted_page_offers_the_first_parse(self):
		row = page(
			1,
			canonical_source="vlm",
			canonical_markdown="Vision text",
			remediation_method="vlm",
			remediation_markdown="Vision text",
			remediation_adopted=1,
			baseline_markdown="First parse",
		)
		self.assertEqual(alternative(row)["source"], "baseline")

	def test_a_preview_comes_before_the_stored_versions(self):
		row = page(1, remediation_method="cleanup", remediation_markdown="Cleaner", preview_markdown="New")
		self.assertEqual(alternative(row)["source"], "preview")


class TestReviewApi(FrappeTestCase):
	def setUp(self):
		self.project = make_project("Review")
		self.sd = frappe.get_doc(
			{
				"doctype": "Source Document",
				"title": "Review doc",
				"project": self.project,
				"pdf": "/files/r.pdf",
			}
		).insert(ignore_permissions=True)
		self.imp = frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Review doc",
				"project": self.project,
				"pdf": "/files/r.pdf",
				"status": "Review",
				"source_document": self.sd.name,
			}
		).insert(ignore_permissions=True)
		for page_no in (1, 2, 3):
			frappe.get_doc(
				{
					"doctype": "Source Page",
					"source_document": self.sd.name,
					"page_no": page_no,
					"kind": "text",
					"composite": 0.95,
					"canonical_composite": 0.95,
					"canonical_source": "baseline",
					"canonical_markdown": f"Page {page_no} text that is long enough to count.",
				}
			).insert(ignore_permissions=True)
		store.replace_sections(
			self.sd.name,
			[
				Section(
					title="Intro",
					level=1,
					hierarchy_path=["Intro"],
					page_start=1,
					page_end=3,
					markdown=CLEAN_TABLE,
				)
			],
		)
		frappe.db.set_value(
			"Source Section", {"source_document": self.sd.name}, "section_type", "clinical_protocols"
		)
		self.low = frappe.db.get_value("Source Page", {"source_document": self.sd.name, "page_no": 2})
		frappe.db.set_value(
			"Source Page",
			self.low,
			{
				"canonical_composite": 0.5,
				"notes": "low text recall",
				"remediation_method": "vlm",
				"remediation_markdown": "A much better read of page 2.",
				"remediation_composite": 0.93,
			},
		)
		self.issue_id = f"page_low_score:{self.low}"
		self.addCleanup(frappe.set_user, "Administrator")

	def test_overview_counts_and_page_map(self):
		result = review_api.get_review(self.imp.name)
		self.assertEqual(result["summary"]["pages"], 3)
		self.assertEqual(result["summary"]["open"], 1)
		self.assertEqual(result["summary"]["blocks"], 1)
		self.assertEqual(result["summary"]["ready"], 2)
		self.assertEqual([t["state"] for t in result["page_map"]], ["clean", "blocks", "clean"])
		self.assertEqual(result["issues"][0]["suggested"], "Use the vision model version")

	def test_issue_detail_previews_the_stored_candidate(self):
		detail = review_api.get_issue(self.imp.name, self.issue_id)
		candidate = detail["suggestions"][0]
		self.assertEqual(candidate["id"], "use_candidate")
		self.assertTrue(candidate["recommended"])
		self.assertEqual(candidate["preview"], "A much better read of page 2.")

	def test_accept_hides_until_the_markdown_changes(self):
		review_api.accept(self.imp.name, self.issue_id)
		result = review_api.get_review(self.imp.name)
		self.assertEqual(result["summary"]["open"], 0)
		self.assertEqual(result["reviewed"][0]["issue_id"], self.issue_id)
		self.assertEqual(result["page_map"][1]["state"], "reviewed")

		frappe.db.set_value("Source Page", self.low, "canonical_markdown", "Edited later")
		self.assertEqual(review_api.get_review(self.imp.name)["summary"]["open"], 1)

	def test_apply_candidate_publishes_it_and_closes_the_item(self):
		result = review_api.apply(self.imp.name, self.issue_id, "use_candidate")
		self.assertEqual(result["follow_up"], [])
		page = frappe.db.get_value(
			"Source Page",
			self.low,
			["canonical_markdown", "canonical_source", "remediation_adopted"],
			as_dict=True,
		)
		self.assertEqual(page.canonical_markdown, "A much better read of page 2.")
		self.assertEqual((page.canonical_source, page.remediation_adopted), ("vlm", 1))
		review = review_api.get_review(self.imp.name)
		self.assertEqual(review["summary"]["open"], 0)
		self.assertIn("0.50 to 0.93", review["reviewed"][0]["outcome"])

	def test_fix_that_keeps_the_signal_still_closes_the_item(self):
		frappe.db.set_value("Source Page", self.low, {"image": "/files/p2.png", "remediation_markdown": ""})
		review_api.apply(self.imp.name, self.issue_id, "page_image")
		self.assertIn(
			"![Page 2](/files/p2.png)", frappe.db.get_value("Source Page", self.low, "canonical_markdown")
		)
		self.assertEqual(review_api.get_review(self.imp.name)["summary"]["open"], 0)
		review_api.reopen(self.imp.name, self.issue_id)
		self.assertEqual(review_api.get_review(self.imp.name)["summary"]["open"], 1)

	def test_a_fix_that_is_not_offered_is_refused(self):
		with self.assertRaises(frappe.ValidationError):
			review_api.apply(self.imp.name, self.issue_id, "close_fence")

	def test_apply_safe_runs_only_safe_fixes(self):
		section = frappe.db.get_value("Source Section", {"source_document": self.sd.name})
		store.set_section_markdown(section, BROKEN_TABLE)
		self.assertEqual(review_api.get_review(self.imp.name)["summary"]["safe"], 1)
		self.assertEqual(review_api.apply_safe(self.imp.name), {"applied": 1})
		self.assertEqual(frappe.db.get_value("Source Section", section, "markdown"), CLEAN_TABLE)
		self.assertEqual(review_api.get_review(self.imp.name)["summary"]["open"], 1)

	def test_reopen_brings_an_accepted_issue_back(self):
		review_api.accept(self.imp.name, self.issue_id)
		review_api.reopen(self.imp.name, self.issue_id)
		self.assertEqual(review_api.get_review(self.imp.name)["summary"]["open"], 1)

	def test_published_import_is_read_only(self):
		self.imp.db_set("status", "Completed")
		self.assertTrue(review_api.get_review(self.imp.name)["read_only"])
		with self.assertRaises(frappe.ValidationError):
			review_api.accept(self.imp.name, self.issue_id)

	def test_user_without_access_cannot_read_or_accept(self):
		frappe.set_user(make_user("review-other@example.com"))
		with self.assertRaises(frappe.PermissionError):
			review_api.get_review(self.imp.name)
		with self.assertRaises(frappe.PermissionError):
			review_api.accept(self.imp.name, self.issue_id)

	def test_applying_a_preview_publishes_it_as_the_vision_version(self):
		frappe.db.set_value(
			"Source Page",
			self.low,
			{
				"preview_markdown": "The re-read page 2.",
				"preview_composite": 0.95,
				"preview_instruction": "Drop the footer",
			},
		)
		detail = review_api.get_issue(self.imp.name, self.issue_id)
		self.assertEqual(detail["suggestions"][0]["label"], "Use the new reading")
		self.assertIn("Drop the footer", detail["suggestions"][0]["detail"])

		review_api.apply(self.imp.name, self.issue_id, "use_candidate")
		page = frappe.db.get_value(
			"Source Page",
			self.low,
			[
				"canonical_markdown",
				"canonical_source",
				"remediation_markdown",
				"remediation_adopted",
				"preview_markdown",
			],
			as_dict=True,
		)
		self.assertEqual(page.canonical_markdown, "The re-read page 2.")
		self.assertEqual((page.canonical_source, page.remediation_adopted), ("vlm", 1))
		self.assertEqual(page.remediation_markdown, "The re-read page 2.")
		self.assertIsNone(page.preview_markdown)
		self.assertEqual(
			review_api.get_review(self.imp.name)["reviewed"][0]["instruction"], "Drop the footer"
		)

	def test_reread_is_offered_with_a_key_but_cannot_be_applied(self):
		with patch("wikify.engine.llm.has_openrouter", return_value=True):
			ids = [s["id"] for s in review_api.get_issue(self.imp.name, self.issue_id)["suggestions"]]
			self.assertIn("reparse", ids)
			with self.assertRaises(frappe.ValidationError):
				review_api.apply(self.imp.name, self.issue_id, "reparse")

	def test_preview_reparse_queues_one_job_per_page(self):
		with (
			patch("wikify.engine.llm.has_openrouter", return_value=True),
			patch("wikify.api.review.is_job_enqueued", return_value=False),
			patch("frappe.enqueue") as enqueue,
		):
			self.assertEqual(review_api.preview_reparse(self.imp.name, 2, "  Drop it  "), {"queued": True})
		kwargs = enqueue.call_args.kwargs
		self.assertEqual(kwargs["job_id"], f"review-preview:{self.low}")
		self.assertEqual((kwargs["page_no"], kwargs["instruction"]), (2, "Drop it"))

	def test_preview_reparse_needs_a_model_key(self):
		with (
			patch("wikify.engine.llm.has_openrouter", return_value=False),
			self.assertRaises(frappe.ValidationError),
		):
			review_api.preview_reparse(self.imp.name, 2)

	def test_preview_job_reports_success_and_failure(self):
		events = []
		with (
			patch("wikify.engine.store.get_import_pdf_path", return_value="/tmp/r.pdf"),
			patch("frappe.publish_realtime", lambda event, payload, **kw: events.append((event, payload))),
			patch("frappe.log_error"),
		):
			with patch.object(review_job, "reparse_page", return_value={"composite": 0.88}):
				review_job.preview(self.imp.name, 2)
			with patch.object(review_job, "reparse_page", side_effect=RuntimeError("402 Payment Required")):
				review_job.preview(self.imp.name, 2)
		self.assertEqual(
			events,
			[
				(
					"wikify_review_preview",
					{"import": self.imp.name, "page_no": 2, "ok": True, "composite": 0.88},
				),
				(
					"wikify_review_preview",
					{"import": self.imp.name, "page_no": 2, "ok": False, "error": "402 Payment Required"},
				),
			],
		)
