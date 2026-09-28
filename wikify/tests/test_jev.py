# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import json
from unittest.mock import MagicMock, patch

import fitz
import frappe
import requests
from frappe.tests.utils import FrappeTestCase

from wikify.api import imports as imports_api
from wikify.engine import config, jev_client
from wikify.engine.verify import jev
from wikify.jobs import jev_score as jev_score_job
from wikify.seed import seed_uncategorized_project

BODY = "The quarterly maintenance checklist covers pumps, valves and filters in every plant room."


def _answers(completeness=4.0, structure=3.0, accuracy=3.0, placeholder=0.0, confidence=0.9) -> dict:
	return {
		"completeness": {
			"type": "score",
			"score": completeness,
			"confidence": confidence,
			"probabilities": {},
		},
		"structure": {"type": "score", "score": structure, "confidence": confidence, "probabilities": {}},
		"accuracy": {"type": "score", "score": accuracy, "confidence": confidence, "probabilities": {}},
		"placeholder": {"type": "noul", "noul": placeholder},
	}


def _response(**kwargs) -> dict:
	return {"model": "jev-1.13.0", "answers": _answers(**kwargs), "usage": {"input_tokens": 321}}


def _http(status: int, payload: dict | None = None, headers: dict | None = None) -> MagicMock:
	resp = MagicMock(status_code=status, headers=headers or {})
	resp.json.return_value = payload or {}
	if status >= 400:
		resp.raise_for_status.side_effect = requests.HTTPError(str(status))
	return resp


def _pdf_file(page_texts: list[str]) -> str:
	document = fitz.open()
	for text in page_texts:
		document.new_page().insert_textbox(fitz.Rect(72, 72, 520, 760), text, fontsize=11)
	uploaded = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{frappe.generate_hash(length=6)}-jev.pdf",
			"content": document.tobytes(),
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	return uploaded.file_url


class TestJevScoring(FrappeTestCase):
	def test_perfect_answers_score_one(self):
		score, confidence, detail = jev.combine(_answers())
		self.assertEqual(score, 1.0)
		self.assertEqual(confidence, 0.9)
		self.assertEqual(detail["completeness"]["score"], 1.0)

	def test_weights_are_applied_to_normalized_levels(self):
		score, _, _ = jev.combine(_answers(completeness=2.0, structure=3.0, accuracy=0.0))
		expected = (0.5 * 0.5 + 0.2 * 1.0 + 0.3 * 0.0) / sum(config.JEV_WEIGHTS.values())
		self.assertAlmostEqual(score, round(expected, 3))

	def test_placeholder_caps_the_page(self):
		score, _, _ = jev.combine(_answers(placeholder=0.9))
		self.assertEqual(score, config.JEV_PLACEHOLDER_CAP)

	def test_confidence_is_the_weakest_answer(self):
		answers = _answers()
		answers["structure"]["confidence"] = 0.3
		_, confidence, _ = jev.combine(answers)
		self.assertEqual(confidence, 0.3)

	def test_state_fields_are_truncated(self):
		state = jev.build_state("a" * (config.JEV_MAX_FIELD_CHARS + 10), "b" * 5)
		self.assertEqual(len(state["pdf_text"]), config.JEV_MAX_FIELD_CHARS)
		self.assertEqual(state["markdown"], "bbbbb")

	def test_visual_and_textless_pages_are_not_scorable(self):
		self.assertFalse(jev.is_scorable("visual", BODY))
		self.assertFalse(jev.is_scorable("text", "Fig. 3"))
		self.assertTrue(jev.is_scorable("text", BODY))

	def test_empty_markdown_scores_zero_without_calling_jev(self):
		with patch.object(jev_client, "system_one") as call:
			result = jev.score_page(1, BODY, "  ", api_key="k", model="jev-latest")
		call.assert_not_called()
		self.assertEqual((result.status, result.score), ("scored", 0.0))

	def test_request_failure_marks_the_page_as_error(self):
		with patch.object(jev_client, "system_one", side_effect=requests.HTTPError("401")):
			result = jev.score_page(1, BODY, "# Checklist", api_key="k", model="jev-latest")
		self.assertEqual(result.status, "error")
		self.assertIn("401", result.detail["error"])

	def test_document_score_is_weighted_by_text_length(self):
		results = [
			jev.JevPageScore(1, "scored", 1.0, 0.9, weight=300),
			jev.JevPageScore(2, "scored", 0.0, 0.9, weight=100),
			jev.JevPageScore(3, "error"),
			jev.JevPageScore(4, "skipped"),
		]
		self.assertEqual(jev.document_score(results), 0.75)
		self.assertIsNone(jev.document_score([jev.JevPageScore(1, "skipped")]))


class TestJevClient(FrappeTestCase):
	def test_retries_rate_limit_then_succeeds(self):
		responses = [_http(429, headers={"retry-after": "0"}), _http(200, _response())]
		with patch.object(requests, "post", side_effect=responses) as post, patch("time.sleep"):
			data = jev_client.system_one("state", {}, model="jev-latest", api_key="k")
		self.assertEqual(post.call_count, 2)
		self.assertEqual(data["model"], "jev-1.13.0")
		sent = post.call_args.kwargs
		self.assertEqual(sent["headers"]["Authorization"], "Bearer k")
		self.assertEqual(sent["json"]["model"], "jev-latest")

	def test_gives_up_after_max_attempts(self):
		with patch.object(requests, "post", return_value=_http(529)) as post, patch("time.sleep"):
			with self.assertRaises(requests.HTTPError):
				jev_client.system_one("state", {}, model="jev-latest", api_key="k")
		self.assertEqual(post.call_count, jev_client.MAX_ATTEMPTS)

	def test_typesafe_key_wins_then_openrouter(self):
		with (
			patch("wikify.engine.settings.typesafe_key", return_value="ts"),
			patch("wikify.engine.settings.openrouter_key", return_value="or"),
		):
			self.assertEqual(jev_client.credentials(), (jev_client.TYPESAFE_URL, "ts"))
		with (
			patch("wikify.engine.settings.typesafe_key", return_value=""),
			patch("wikify.engine.settings.openrouter_key", return_value="or"),
		):
			self.assertEqual(jev_client.credentials(), (jev_client.OPENROUTER_URL, "or"))

	def test_missing_key_raises(self):
		with self.assertRaises(RuntimeError):
			jev_client.system_one("state", {}, model="jev-latest", api_key="")


class TestJevJob(FrappeTestCase):
	def setUp(self):
		self.pdf = _pdf_file(
			[BODY, "Filters are replaced every ninety days by the facilities contractor.", "Fig. 3"]
		)
		self.source_document = frappe.get_doc(
			{"doctype": "Source Document", "title": "Jev Test", "page_count": 3}
		).insert(ignore_permissions=True)
		self.pages = {}
		for page_no, kind, markdown in [(1, "text", BODY), (2, "text", ""), (3, "visual", "![Fig](x.png)")]:
			page = frappe.get_doc(
				{
					"doctype": "Source Page",
					"source_document": self.source_document.name,
					"page_no": page_no,
					"kind": kind,
					"baseline_markdown": markdown,
				}
			).insert(ignore_permissions=True)
			self.pages[page_no] = page.name
		self.imp = frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Jev Test",
				"pdf": self.pdf,
				"project": seed_uncategorized_project(),
				"status": "Review",
				"source_document": self.source_document.name,
			}
		).insert(ignore_permissions=True)

	def page(self, page_no: int) -> dict:
		return frappe.db.get_value(
			"Source Page",
			self.pages[page_no],
			["jev_status", "jev_score", "jev_confidence", "jev_detail"],
			as_dict=True,
		)

	def test_no_key_logs_a_warning_and_writes_nothing(self):
		with patch.object(jev_client, "has_jev", return_value=False):
			self.assertIsNone(jev_score_job.run(self.imp.name))
		self.assertIsNone(frappe.db.get_value("Source Document", self.source_document.name, "jev_scored_at"))
		messages = frappe.get_all("Import Log Entry", filters={"import": self.imp.name}, pluck="message")
		self.assertTrue(any("no TypeSafe or OpenRouter key" in m for m in messages))

	def test_scores_pages_and_rolls_up_the_document(self):
		with (
			patch.object(jev_client, "has_jev", return_value=True),
			patch.object(jev_client, "credentials", return_value=(jev_client.OPENROUTER_URL, "k")),
			patch.object(jev_client, "system_one", return_value=_response(completeness=3.0)) as call,
		):
			summary = jev_score_job.run(self.imp.name)

		self.assertEqual(call.call_count, 1)
		self.assertEqual(call.call_args.kwargs["url"], jev_client.OPENROUTER_URL)
		self.assertEqual(summary["scored"], 2)
		self.assertEqual(summary["skipped"], 1)

		first = self.page(1)
		self.assertEqual(first.jev_status, "scored")
		self.assertEqual(first.jev_score, 0.875)
		self.assertEqual(json.loads(first.jev_detail)["model"], "jev-1.13.0")
		self.assertEqual((self.page(2).jev_status, self.page(2).jev_score), ("scored", 0.0))
		self.assertEqual(self.page(3).jev_status, "skipped")

		doc = frappe.db.get_value(
			"Source Document",
			self.source_document.name,
			["jev_score", "jev_pages_scored", "jev_low_pages", "jev_scored_at"],
			as_dict=True,
		)
		self.assertEqual(doc.jev_pages_scored, 2)
		self.assertEqual(doc.jev_low_pages, 1)
		self.assertGreater(doc.jev_score, 0)
		self.assertLess(doc.jev_score, 0.875)
		self.assertIsNotNone(doc.jev_scored_at)

	def test_rescore_enqueues_once(self):
		with (
			patch.object(frappe, "enqueue") as enqueue,
			patch.object(imports_api, "is_job_enqueued", side_effect=[False, True]),
		):
			imports_api.rescore_jev(self.imp.name)
			imports_api.rescore_jev(self.imp.name)
		enqueue.assert_called_once()
		self.assertEqual(enqueue.call_args.args[0], "wikify.jobs.jev_score.run")
		self.assertEqual(enqueue.call_args.kwargs["job_id"], jev_score_job.job_id(self.imp.name))
