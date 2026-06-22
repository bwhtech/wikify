# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Slice 6 — section classification + the Explore (typed, cross-document) queries.

Builds known section trees via the store seam, classifies them with the LLM seam
patched (hermetic), and asserts `section_type` is written, the no-key path falls back
to `other`, and the Explore APIs (`type_summary` / `sections_by_type`) return the
counts + cross-document grouping the headline screen consumes.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import explore
from wikify.engine import classify, store
from wikify.engine.loader import classifier
from wikify.engine.loader.sectionizer import Section


def _sec(title, level, path, p_start, p_end):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=f"body of {title}",
	)


# Deterministic stand-in for the LLM classifier: route by a keyword in the title.
def _fake_classify(title, content, taxonomy, project_context=""):
	lowered = title.lower()
	if "nurse" in lowered or "role" in lowered:
		return "staff_roles_and_responsibilities"
	if "emergency" in lowered:
		return "emergency_procedures"
	return "other"


class TestClassify(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Classify Test"}).insert(
			ignore_permissions=True
		)
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Staff Roles", 1, ["1. Staff Roles"], 1, 2),
				_sec("1.1 Nurse Duties", 2, ["1. Staff Roles", "1.1 Nurse Duties"], 1, 1),
				_sec("2. Emergency Response", 1, ["2. Emergency Response"], 3, 3),
				_sec("3. Appendix", 1, ["3. Appendix"], 4, 4),
			],
		)

	def _types(self):
		return {
			r.title: r.section_type
			for r in frappe.get_all(
				"Source Section",
				filters={"source_document": self.sd.name},
				fields=["title", "section_type"],
			)
		}

	def test_seed_taxonomy_present(self):
		taxonomy = store.get_section_taxonomy()
		self.assertIn("staff_roles_and_responsibilities", taxonomy)
		self.assertEqual(taxonomy[-1], "other")  # catch-all is ordered last
		self.assertTrue(frappe.db.get_value("Section Type", "other", "is_other"))

	def test_classify_writes_section_type_and_counts(self):
		with patch.object(classify, "classify_section", side_effect=_fake_classify):
			result = classify.classify_document(self.sd.name)

		self.assertEqual(result["sections"], 4)
		self.assertEqual(result["by_type"]["staff_roles_and_responsibilities"], 2)
		self.assertEqual(result["by_type"]["emergency_procedures"], 1)
		self.assertEqual(result["by_type"]["other"], 1)

		types = self._types()
		self.assertEqual(types["1.1 Nurse Duties"], "staff_roles_and_responsibilities")
		self.assertEqual(types["2. Emergency Response"], "emergency_procedures")
		self.assertEqual(types["3. Appendix"], "other")

	def test_classify_progress_callback_streams_each_section(self):
		seen = []
		with patch.object(classify, "classify_section", side_effect=_fake_classify):
			classify.classify_document(
				self.sd.name, progress_cb=lambda d, t, title, st: seen.append((d, t, st))
			)
		self.assertEqual(len(seen), 4)
		self.assertEqual(seen[-1][0], 4)  # done counts up to total
		self.assertEqual(seen[-1][1], 4)

	def test_no_key_falls_back_to_other(self):
		# With no OpenRouter key, classify_section never calls out — returns "other".
		with patch.object(classifier.llm, "has_openrouter", return_value=False):
			label = classifier.classify_section(
				"1. Staff Roles", "body", ["staff_roles_and_responsibilities"]
			)
		self.assertEqual(label, "other")

	def test_transient_failure_retries_then_succeeds(self):
		# A rate-limit blip must not be recorded as "other" — retry, then take the label.
		good = {"choices": [{"message": {"content": '{"type": "clinical_protocols"}'}}]}
		calls = {"n": 0}

		def flaky(*a, **k):
			calls["n"] += 1
			if calls["n"] == 1:
				raise RuntimeError("429 rate limited")
			return good

		with (
			patch.object(classifier.llm, "has_openrouter", return_value=True),
			patch.object(classifier.llm, "chat_completion", side_effect=flaky),
			patch.object(classifier.settings, "get", return_value="x/model"),
			patch.object(classifier.time, "sleep", return_value=None),
		):
			label = classifier.classify_section("T", "c", ["clinical_protocols"])
		self.assertEqual(label, "clinical_protocols")
		self.assertEqual(calls["n"], 2)  # failed once, retried once

	def test_persistent_failure_falls_back_to_other(self):
		with (
			patch.object(classifier.llm, "has_openrouter", return_value=True),
			patch.object(classifier.llm, "chat_completion", side_effect=RuntimeError("boom")),
			patch.object(classifier.settings, "get", return_value="x/model"),
			patch.object(classifier.time, "sleep", return_value=None),
		):
			label = classifier.classify_section("T", "c", ["clinical_protocols"])
		self.assertEqual(label, "other")

	def test_classify_rejects_off_taxonomy_label(self):
		# A model returning a label not in the taxonomy is coerced to "other".
		resp = {"choices": [{"message": {"content": '{"type": "made_up_label"}'}}]}
		with (
			patch.object(classifier.llm, "has_openrouter", return_value=True),
			patch.object(classifier.llm, "chat_completion", return_value=resp),
			patch.object(classifier.settings, "get", return_value="x/model"),
		):
			label = classifier.classify_section("T", "c", ["clinical_protocols"])
		self.assertEqual(label, "other")

	def test_type_summary_counts_and_untagged(self):
		with patch.object(classify, "classify_section", side_effect=_fake_classify):
			classify.classify_document(self.sd.name)

		summary = {t["type_name"]: t for t in explore.type_summary(self.sd.name)}
		# Every seeded type is present (stable chips), with the right counts.
		self.assertEqual(summary["staff_roles_and_responsibilities"]["count"], 2)
		self.assertEqual(summary["emergency_procedures"]["count"], 1)
		self.assertEqual(summary["other"]["count"], 1)
		self.assertNotIn("__untagged__", summary)  # nothing left untagged

		# Blank a section → an "Untagged" bucket appears.
		nurse = frappe.get_all(
			"Source Section",
			filters={"source_document": self.sd.name, "title": "1.1 Nurse Duties"},
			pluck="name",
		)[0]
		store.set_section_type(nurse, None)
		summary2 = {t["type_name"]: t for t in explore.type_summary(self.sd.name)}
		self.assertEqual(summary2["__untagged__"]["count"], 1)

	def test_sections_by_type_groups_across_documents(self):
		# A second document with a section of the same type → cross-doc grouping.
		sd2 = frappe.get_doc({"doctype": "Source Document", "title": "Another Manual"}).insert(
			ignore_permissions=True
		)
		store.replace_sections(sd2.name, [_sec("1. Roles", 1, ["1. Roles"], 1, 1)])

		with patch.object(classify, "classify_section", side_effect=_fake_classify):
			classify.classify_document(self.sd.name)
			classify.classify_document(sd2.name)

		groups = explore.sections_by_type("staff_roles_and_responsibilities")
		titles_by_doc = {g["doc_title"]: [s["title"] for s in g["sections"]] for g in groups}
		self.assertIn("Classify Test", titles_by_doc)
		self.assertIn("Another Manual", titles_by_doc)
		self.assertEqual(titles_by_doc["Another Manual"], ["1. Roles"])
		# Provenance carries page range + hierarchy path for each hit.
		first = groups[0]["sections"][0]
		self.assertIn("page_start", first)
		self.assertIn("hierarchy_path", first)

		# Scoped to one document, only that doc's group is returned.
		scoped = explore.sections_by_type("staff_roles_and_responsibilities", self.sd.name)
		self.assertEqual({g["source_document"] for g in scoped}, {self.sd.name})
