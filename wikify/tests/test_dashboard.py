# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import dashboard
from wikify.api import projects as projects_api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.tests.test_ask_history import make_user

OTHER_USER = "dashboard-other@example.com"


def _sec(title, level, path, p_start, p_end):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=f"body of {title}",
	)


class TestDashboard(FrappeTestCase):
	def setUp(self):
		self.tag = frappe.generate_hash(length=6)
		self.project = projects_api.create_project(f"Dashboard {self.tag}")
		self.sd = frappe.get_doc(
			{
				"doctype": "Source Document",
				"title": f"Policy {self.tag}",
				"project": self.project,
				"pdf": "/files/policy.pdf",
			}
		).insert(ignore_permissions=True)
		store.replace_sections(
			self.sd.name,
			[
				_sec("Medicines", 1, ["Medicines"], 1, 4),
				_sec("Storage", 2, ["Medicines", "Storage"], 1, 2),
				_sec("Disposal", 2, ["Medicines", "Disposal"], 3, 4),
				_sec("Emergencies", 1, ["Emergencies"], 5, 6),
			],
		)
		types = {
			"Medicines": "medication_management",
			"Storage": "medication_management",
			"Disposal": "medication_management",
			"Emergencies": "emergency_procedures",
		}
		self.names = {}
		for row in frappe.get_all(
			"Source Section", filters={"source_document": self.sd.name}, fields=["name", "title"]
		):
			frappe.db.set_value("Source Section", row.name, "section_type", types[row.title])
			self.names[row.title] = row.name
		self.addCleanup(frappe.set_user, "Administrator")

	def test_categories_are_scoped_to_the_project(self):
		rows = {c["type_name"]: c for c in dashboard.summary(project=self.project)["categories"]}
		self.assertEqual(set(rows), {"medication_management", "emergency_procedures"})
		self.assertEqual(rows["medication_management"]["sections"], 3)
		self.assertEqual(rows["medication_management"]["subcategories"], 1)
		self.assertEqual(rows["medication_management"]["documents"], 1)

	def test_subcategory_is_the_top_level_heading(self):
		result = dashboard.summary(project=self.project, section_type="medication_management")
		self.assertEqual(result["subcategories"], [{"title": "Medicines", "sections": 3, "documents": 1}])
		self.assertEqual(result["sections"], [])

	def test_sections_listed_for_a_subcategory(self):
		result = dashboard.summary(
			project=self.project, section_type="medication_management", subcategory="Medicines"
		)
		self.assertEqual({s["title"] for s in result["sections"]}, {"Medicines", "Storage", "Disposal"})
		self.assertEqual({s["doc_title"] for s in result["sections"]}, {f"Policy {self.tag}"})

	def test_cards_ignore_the_drill_filters(self):
		everything = dashboard.summary()["cards"]
		drilled = dashboard.summary(project=self.project, section_type="emergency_procedures")["cards"]
		self.assertEqual(everything, drilled)

	def test_section_detail_returns_content_children_and_pdf(self):
		detail = dashboard.section_detail(self.names["Medicines"])
		self.assertEqual(detail["markdown"], "body of Medicines")
		self.assertEqual(detail["pdf"], "/files/policy.pdf")
		self.assertEqual([c["title"] for c in detail["children"]], ["Storage", "Disposal"])
		self.assertEqual(detail["category"], "Medication Management")

	def test_unreadable_project_is_hidden(self):
		frappe.set_user(make_user(OTHER_USER))
		self.assertNotIn(self.project, [p["name"] for p in dashboard.summary()["projects"]])
		with self.assertRaises(frappe.PermissionError):
			dashboard.summary(project=self.project)
		with self.assertRaises(frappe.PermissionError):
			dashboard.section_detail(self.names["Storage"])

	def test_heading_containing_the_path_separator_stays_whole(self):
		store.replace_sections(
			self.sd.name,
			[
				_sec("Temperature > 25C", 1, ["Temperature > 25C"], 1, 2),
				_sec("Fridge", 2, ["Temperature > 25C", "Fridge"], 2, 2),
			],
		)
		frappe.db.set_value(
			"Source Section", {"source_document": self.sd.name}, "section_type", "medication_management"
		)
		result = dashboard.summary(project=self.project, section_type="medication_management")
		self.assertEqual([s["title"] for s in result["subcategories"]], ["Temperature > 25C"])
		fridge = frappe.db.get_value("Source Section", {"source_document": self.sd.name, "title": "Fridge"})
		self.assertEqual(dashboard.section_detail(fridge)["ancestors"], ["Temperature > 25C"])

	def test_section_detail_returns_ancestors_and_page_count(self):
		frappe.db.set_value("Source Document", self.sd.name, "page_count", 6)
		detail = dashboard.section_detail(self.names["Storage"])
		self.assertEqual(detail["ancestors"], ["Medicines"])
		self.assertEqual(detail["page_count"], 6)
		self.assertEqual(dashboard.section_detail(self.names["Medicines"])["ancestors"], [])

	def test_documents_without_sections_are_not_counted(self):
		frappe.get_doc(
			{"doctype": "Source Document", "title": f"Failed {self.tag}", "project": self.project}
		).insert(ignore_permissions=True)
		row = next(p for p in dashboard.summary()["projects"] if p["name"] == self.project)
		self.assertEqual(row["documents"], 1)

	def test_untagged_section_detail_has_a_category(self):
		frappe.db.set_value("Source Section", self.names["Disposal"], "section_type", None)
		detail = dashboard.section_detail(self.names["Disposal"])
		self.assertEqual(detail["category"], "Untagged")
		self.assertEqual(detail["category_color"], "#cbd5e1")
