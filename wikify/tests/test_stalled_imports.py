# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase
from frappe.utils import add_to_date, now_datetime

from wikify.jobs import stalled
from wikify.seed import seed_uncategorized_project


class TestStalledImports(FrappeTestCase):
	def make_import(self, status: str, minutes_since_modified: int):
		source_document = frappe.get_doc({"doctype": "Source Document", "title": "Stalled Test"}).insert(
			ignore_permissions=True
		)
		imp = frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Stalled Test Import",
				"project": seed_uncategorized_project(),
				"pdf": "/private/files/x.pdf",
				"source_document": source_document.name,
				"status": status,
			}
		).insert(ignore_permissions=True)
		frappe.db.set_value(
			"Wikify Import",
			imp.name,
			"modified",
			add_to_date(now_datetime(), minutes=-minutes_since_modified),
			update_modified=False,
		)
		return imp.name

	def fail_stalled_imports(self, job_is_enqueued: bool) -> None:
		with (
			patch.object(stalled, "is_job_enqueued", return_value=job_is_enqueued),
			patch("frappe.db.commit"),
			patch.object(frappe, "publish_realtime"),
		):
			stalled.fail_stalled_imports()

	def test_an_import_with_no_live_job_is_marked_failed(self):
		name = self.make_import("Parsing", 30)

		self.fail_stalled_imports(job_is_enqueued=False)

		status, error = frappe.db.get_value("Wikify Import", name, ["status", "error"])
		self.assertEqual(status, "Failed")
		self.assertIn("Click Remediate to retry", error)
		self.assertTrue(frappe.db.exists("Import Log Entry", {"import": name, "level": "error"}))

	def test_an_import_with_a_live_job_is_left_running(self):
		name = self.make_import("Remediating", 30)

		self.fail_stalled_imports(job_is_enqueued=True)

		self.assertEqual(frappe.db.get_value("Wikify Import", name, "status"), "Remediating")
		self.assertFalse(frappe.db.get_value("Wikify Import", name, "error"))

	def test_a_recently_updated_import_is_left_running(self):
		name = self.make_import("Parsing", 1)

		self.fail_stalled_imports(job_is_enqueued=False)

		self.assertEqual(frappe.db.get_value("Wikify Import", name, "status"), "Parsing")
		self.assertFalse(frappe.db.get_value("Wikify Import", name, "error"))

	def test_an_import_in_review_is_left_alone(self):
		name = self.make_import("Review", 30)

		self.fail_stalled_imports(job_is_enqueued=False)

		self.assertEqual(frappe.db.get_value("Wikify Import", name, "status"), "Review")
