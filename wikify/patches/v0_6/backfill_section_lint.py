"""0.6 Slice 30: backfill `Source Section.lint_issues` for pre-lint rows.

Read-compute-write only — markdown is NOT modified (the auto-fix boundary is the
pipeline, never existing content). Idempotent: recomputing over already-linted rows
lands the same values.
"""

import frappe

from wikify.engine.store import lint_json


def execute():
	rows = frappe.get_all("Source Section", fields=["name", "markdown"], limit=0)
	for row in rows:
		frappe.db.set_value(
			"Source Section",
			row.name,
			"lint_issues",
			lint_json(row.markdown or ""),
			update_modified=False,
		)
