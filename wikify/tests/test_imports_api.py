# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import json
from unittest.mock import patch

import fitz
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import imports as imports_api
from wikify.jobs import parse as parse_job
from wikify.jobs import remediate as remediate_job
from wikify.seed import seed_uncategorized_project


def make_file(file_name: str) -> str:
	document = fitz.open()
	document.new_page().insert_text((72, 90), file_name, fontsize=12)
	uploaded = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{frappe.generate_hash(length=6)}-{file_name}",
			"content": document.tobytes(),
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	return uploaded.file_url


def _files(n: int) -> list[dict]:
	return [{"file_url": make_file(f"doc{i}.pdf"), "title": f"Doc {i}"} for i in range(n)]


class TestImportsApi(FrappeTestCase):
	def setUp(self):
		self.project = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Batch Upload Test {frappe.generate_hash(length=6)}",
			}
		).insert(ignore_permissions=True)

	def test_batch_creates_one_import_per_file_in_order(self):
		files = _files(3)
		with patch.object(frappe, "enqueue") as enqueue:
			names = imports_api.start_imports(files, project=self.project.name)

		self.assertEqual(len(names), 3)
		for i, name in enumerate(names):
			imp = frappe.get_doc("Wikify Import", name)
			self.assertEqual(imp.import_title, f"Doc {i}")
			self.assertEqual(imp.pdf, files[i]["file_url"])
			self.assertEqual(imp.project, self.project.name)
			self.assertEqual(imp.status, "Queued")

		self.assertEqual(enqueue.call_count, 3)
		for i, call in enumerate(enqueue.call_args_list):
			self.assertEqual(call.args[0], "wikify.jobs.parse.run")
			self.assertEqual(call.kwargs["queue"], "long")
			self.assertEqual(call.kwargs["import_name"], names[i])
			self.assertEqual(call.kwargs["job_id"], parse_job.job_id(names[i]))

	def test_import_count_tracks_the_batch(self):
		with patch.object(frappe, "enqueue"):
			imports_api.start_imports(_files(3), project=self.project.name)
		count = frappe.db.get_value("Wikify Project", self.project.name, "import_count")
		self.assertEqual(count, 3)

	def test_defaults_to_the_uncategorized_project(self):
		default = seed_uncategorized_project()
		with patch.object(frappe, "enqueue"):
			names = imports_api.start_imports(_files(2))
		for name in names:
			self.assertEqual(frappe.db.get_value("Wikify Import", name, "project"), default)

	def test_json_string_payload_parses(self):
		with patch.object(frappe, "enqueue"):
			names = imports_api.start_imports(json.dumps(_files(2)), project=self.project.name)
		self.assertEqual(len(names), 2)

	def test_blank_title_falls_back_to_the_filename(self):
		file_url = make_file("handbook.pdf")
		with patch.object(frappe, "enqueue"):
			names = imports_api.start_imports(
				[{"file_url": file_url, "title": ""}],
				project=self.project.name,
			)
		expected = file_url.rsplit("/", 1)[-1].removesuffix(".pdf")
		self.assertEqual(frappe.db.get_value("Wikify Import", names[0], "import_title"), expected)

	def test_empty_batch_is_rejected(self):
		with patch.object(frappe, "enqueue"), self.assertRaises(frappe.ValidationError):
			imports_api.start_imports([], project=self.project.name)

	def test_oversized_batch_is_rejected(self):
		files = _files(imports_api.MAX_BATCH + 1)
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				imports_api.start_imports(files, project=self.project.name)
			enqueue.assert_not_called()
		self.assertEqual(
			frappe.db.count("Wikify Import", {"project": self.project.name}),
			0,
		)

	def test_single_import_still_works(self):
		with patch.object(frappe, "enqueue") as enqueue:
			name = imports_api.start_import(make_file("one.pdf"), "One", project=self.project.name)
		self.assertIsInstance(name, str)
		self.assertEqual(frappe.db.get_value("Wikify Import", name, "import_title"), "One")
		self.assertEqual(enqueue.call_count, 1)

	def test_file_without_a_url_is_rejected(self):
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.ValidationError):
				imports_api.start_imports(
					[{"file_url": make_file("ok.pdf"), "title": "Ok"}, {"title": "No URL"}],
					project=self.project.name,
				)
			enqueue.assert_not_called()
		self.assertEqual(frappe.db.count("Wikify Import", {"project": self.project.name}), 0)

	def test_a_url_with_no_file_behind_it_is_rejected(self):
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				imports_api.start_imports(
					[{"file_url": "/private/files/never-uploaded.pdf", "title": "Theirs"}],
					project=self.project.name,
				)
			enqueue.assert_not_called()
		self.assertEqual(frappe.db.count("Wikify Import", {"project": self.project.name}), 0)

	def test_one_unreadable_file_rejects_the_whole_batch(self):
		files = [*_files(2), {"file_url": "/private/files/never-uploaded.pdf", "title": "Theirs"}]
		with patch.object(frappe, "enqueue") as enqueue:
			with self.assertRaises(frappe.PermissionError):
				imports_api.start_imports(files, project=self.project.name)
			enqueue.assert_not_called()
		self.assertEqual(frappe.db.count("Wikify Import", {"project": self.project.name}), 0)

	def test_another_users_private_file_is_not_importable(self):
		file_url = make_file("private-to-admin.pdf")
		outsider = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"outsider-{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Outsider",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(outsider.name)
		try:
			with self.assertRaises(frappe.PermissionError):
				imports_api.assert_readable_file(file_url)
		finally:
			frappe.set_user("Administrator")


class TestResumeImport(FrappeTestCase):
	def make_import(self, status: str):
		return frappe.get_doc(
			{
				"doctype": "Wikify Import",
				"import_title": "Resume Test",
				"pdf": "/private/files/resume-test.pdf",
				"project": seed_uncategorized_project(),
				"status": status,
			}
		).insert(ignore_permissions=True)

	def test_a_stuck_import_requeues_its_parse_job(self):
		for status in ("Parsing", "Remediating"):
			imp = self.make_import(status)
			with self.subTest(status=status), patch.object(frappe, "enqueue") as enqueue:
				self.assertTrue(imports_api.can_resume_import(imp.name))
				imports_api.resume_import(imp.name)

				enqueue.assert_called_once()
				self.assertEqual(enqueue.call_args.args[0], "wikify.jobs.parse.run")
				self.assertEqual(enqueue.call_args.kwargs["queue"], "long")
				self.assertEqual(enqueue.call_args.kwargs["import_name"], imp.name)
				self.assertEqual(enqueue.call_args.kwargs["job_id"], parse_job.job_id(imp.name))

	def test_refuses_while_a_job_for_the_import_is_queued_or_running(self):
		imp = self.make_import("Remediating")
		for running_job_id in (parse_job.job_id(imp.name), remediate_job.job_id(imp.name)):
			with (
				self.subTest(running_job_id=running_job_id),
				patch.object(
					imports_api, "is_job_enqueued", side_effect=lambda job_id: job_id == running_job_id
				),
				patch.object(frappe, "enqueue") as enqueue,
			):
				self.assertFalse(imports_api.can_resume_import(imp.name))
				with self.assertRaises(frappe.ValidationError):
					imports_api.resume_import(imp.name)
				enqueue.assert_not_called()

	def test_refuses_an_import_that_is_not_parsing_or_remediating(self):
		imp = self.make_import("Review")
		with patch.object(frappe, "enqueue") as enqueue:
			self.assertFalse(imports_api.can_resume_import(imp.name))
			with self.assertRaises(frappe.ValidationError):
				imports_api.resume_import(imp.name)
		enqueue.assert_not_called()

	def test_a_user_without_write_permission_cannot_resume(self):
		imp = self.make_import("Remediating")
		outsider = frappe.get_doc(
			{
				"doctype": "User",
				"email": f"resume-outsider-{frappe.generate_hash(length=6)}@example.com",
				"first_name": "Outsider",
			}
		).insert(ignore_permissions=True)

		frappe.set_user(outsider.name)
		try:
			with patch.object(frappe, "enqueue") as enqueue:
				self.assertFalse(imports_api.can_resume_import(imp.name))
				with self.assertRaises(frappe.PermissionError):
					imports_api.resume_import(imp.name)
			enqueue.assert_not_called()
		finally:
			frappe.set_user("Administrator")
