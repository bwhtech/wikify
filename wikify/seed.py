"""Seed data for Wikify masters.

`Section Type` is the classifier taxonomy — derived bottom-up from the real manuals'
headings (see the POC README). Seeded idempotently from both `after_install` (fresh
sites) and a `post_model_sync` patch (existing sites on `bench migrate`). Editable
afterward — the classifier reads whatever rows exist, so a corpus can re-derive types.
"""

from __future__ import annotations

import frappe

UNCATEGORIZED = "Uncategorized"

# (type_name, label, color, description) — order is the display order in Explore.
# The 11 POC types; `other` is the catch-all (is_other), always last.
SECTION_TYPES: list[tuple[str, str, str, str]] = [
	(
		"staff_roles_and_responsibilities",
		"Staff Roles & Responsibilities",
		"#3b82f6",
		"Job descriptions, role definitions, duties, reporting lines, and org structure.",
	),
	(
		"clinical_protocols",
		"Clinical Protocols",
		"#10b981",
		"Clinical guidelines, care pathways, assessment and treatment protocols.",
	),
	(
		"surgical_procedures",
		"Surgical Procedures",
		"#ef4444",
		"Operative techniques, surgical steps, peri-operative and theatre procedures.",
	),
	(
		"patient_management",
		"Patient Management",
		"#8b5cf6",
		"Admission, triage, monitoring, discharge, and ongoing patient-care management.",
	),
	(
		"medication_management",
		"Medication Management",
		"#f59e0b",
		"Prescribing, dosing, administration, storage, and reconciliation of medicines.",
	),
	(
		"administrative_policies",
		"Administrative Policies",
		"#64748b",
		"Governance, HR, finance, scheduling, and other non-clinical administrative policy.",
	),
	(
		"equipment_and_facilities",
		"Equipment & Facilities",
		"#14b8a6",
		"Devices, instruments, maintenance, supplies, and facility/environment management.",
	),
	(
		"training_and_audits",
		"Training & Audits",
		"#ec4899",
		"Education, competencies, induction, quality audits, and compliance reviews.",
	),
	(
		"research_and_documentation",
		"Research & Documentation",
		"#6366f1",
		"Research methods, evidence, record-keeping, forms, and documentation standards.",
	),
	(
		"emergency_procedures",
		"Emergency Procedures",
		"#f97316",
		"Resuscitation, escalation, codes, and other urgent/emergency response procedures.",
	),
	(
		"other",
		"Other",
		"#9ca3af",
		"Anything that does not fit the categories above (the catch-all).",
	),
]


def seed_section_types() -> None:
	"""Insert any missing Section Type rows. Idempotent — never overwrites edits."""
	for type_name, label, color, description in SECTION_TYPES:
		if frappe.db.exists("Section Type", type_name):
			continue
		frappe.get_doc(
			{
				"doctype": "Section Type",
				"type_name": type_name,
				"label": label,
				"color": color,
				"description": description,
				"is_other": 1 if type_name == "other" else 0,
			}
		).insert(ignore_permissions=True)


def seed_uncategorized_project() -> str:
	"""Get-or-create the single default "Uncategorized" project. Idempotent.

	Keyed on `is_default` (not the name) so a renamed catch-all is still found.
	Returns the project name.
	"""
	existing = frappe.db.get_value("Wikify Project", {"is_default": 1}, "name")
	if existing:
		return existing
	doc = frappe.get_doc(
		{
			"doctype": "Wikify Project",
			"project_name": UNCATEGORIZED,
			"description": "Catch-all for unfiled imports.",
			"is_default": 1,
		}
	).insert(ignore_permissions=True)
	return doc.name
