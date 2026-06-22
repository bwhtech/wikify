"""Seed the "Uncategorized" project and backfill pre-0.2 rows into it (Slice 10).

Re-running is a no-op: the project is get-or-created by `is_default`, and the backfill
only touches rows whose `project` is still empty.
"""

from __future__ import annotations

import frappe

from wikify.seed import seed_uncategorized_project


def execute() -> None:
	project = seed_uncategorized_project()
	project_title = frappe.db.get_value("Wikify Project", project, "project_name")

	# 1. Every Import without a project → Uncategorized (stamp the denormalized
	#    project_name too, since fetch_from only fires on save).
	frappe.db.set_value(
		"Wikify Import",
		{"project": ["in", ["", None]]},
		{"project": project, "project_name": project_title},
		update_modified=False,
	)

	# 1b. Stamp the denormalized project_name on any Import still missing it (covers rows
	#     filed by an earlier run of this patch, before project_name existed).
	for imp in frappe.get_all(
		"Wikify Import",
		filters={"project_name": ["in", ["", None]], "project": ["is", "set"]},
		fields=["name", "project"],
	):
		frappe.db.set_value(
			"Wikify Import",
			imp["name"],
			"project_name",
			frappe.db.get_value("Wikify Project", imp["project"], "project_name"),
			update_modified=False,
		)

	# 2. Every Source Document without a project → its Import's project (= Uncategorized
	#    for all existing rows, since step 1 just filed them there).
	docs = frappe.get_all(
		"Source Document",
		filters={"project": ["in", ["", None]]},
		fields=["name", "import"],
	)
	for doc in docs:
		proj = (
			frappe.db.get_value("Wikify Import", doc["import"], "project") if doc["import"] else None
		) or project
		frappe.db.set_value("Source Document", doc["name"], "project", proj, update_modified=False)

	# 3. Recompute import_count for every project so the cards are correct post-backfill.
	for name in frappe.get_all("Wikify Project", pluck="name"):
		frappe.db.set_value(
			"Wikify Project",
			name,
			"import_count",
			frappe.db.count("Wikify Import", {"project": name}),
			update_modified=False,
		)
