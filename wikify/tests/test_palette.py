# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import palette
from wikify.api import projects as projects_api
from wikify.rag import history
from wikify.tests.test_ask_history import make_result, make_user

OTHER_USER = "palette-other@example.com"


def make_import(title: str, project: str) -> str:
	return (
		frappe.get_doc(
			{"doctype": "Wikify Import", "import_title": title, "pdf": "/files/x.pdf", "project": project}
		)
		.insert(ignore_permissions=True)
		.name
	)


class TestPaletteSearch(FrappeTestCase):
	def setUp(self):
		self.tag = frappe.generate_hash(length=6)
		self.project = projects_api.create_project(f"Handbook {self.tag}")
		self.addCleanup(frappe.set_user, "Administrator")

	def names(self, query: str, kind: str) -> list[str]:
		return [row.name for row in palette.search(query)[kind]]

	def test_import_matches_on_title(self):
		imp = make_import(f"Leave Policy {self.tag}", self.project)
		self.assertIn(imp, self.names(f"Policy {self.tag}", "imports"))

	def test_import_matches_on_project_name(self):
		imp = make_import(f"Onboarding {self.tag}", self.project)
		self.assertIn(imp, self.names(f"Handbook {self.tag}", "imports"))

	def test_import_row_carries_project_name(self):
		imp = make_import(f"Payroll {self.tag}", self.project)
		row = next(r for r in palette.search(f"Payroll {self.tag}")["imports"] if r.name == imp)
		self.assertEqual(row.project, self.project)
		self.assertEqual(row.title, f"Payroll {self.tag}")
		self.assertEqual(row.project_name, f"Handbook {self.tag}")

	def test_short_query_returns_nothing(self):
		make_import(f"X {self.tag}", self.project)
		self.assertEqual(palette.search(" x "), {"imports": [], "conversations": []})

	def test_imports_hidden_without_read_permission(self):
		make_import(f"Restricted {self.tag}", self.project)
		frappe.set_user(make_user(OTHER_USER))
		self.assertEqual(self.names(self.tag, "imports"), [])

	def test_conversation_matches_on_title(self):
		session = history.record_turn(None, f"Which roles exist {self.tag}?", make_result())
		self.assertIn(session, self.names(self.tag, "conversations"))

	def test_conversation_hidden_from_another_user(self):
		session = history.record_turn(None, f"Private question {self.tag}", make_result())
		frappe.set_user(make_user(OTHER_USER))
		self.assertNotIn(session, self.names(self.tag, "conversations"))

	def test_conversation_shown_to_its_owner(self):
		frappe.set_user(make_user(OTHER_USER))
		session = history.record_turn(None, f"My own question {self.tag}", make_result())
		self.assertIn(session, self.names(self.tag, "conversations"))
