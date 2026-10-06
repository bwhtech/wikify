from __future__ import annotations

import frappe
from frappe.utils import add_to_date, now_datetime
from frappe.utils.background_jobs import is_job_enqueued

from wikify.jobs._util import import_job_id, log, publish_progress

RUNNING_STATUSES = ("Queued", "Parsing", "Remediating")
# Running jobs bump `modified` with every progress tick; this grace also covers a just-enqueued job.
STALLED_AFTER_MINUTES = 10


def fail_stalled_imports() -> None:
	cutoff = add_to_date(now_datetime(), minutes=-STALLED_AFTER_MINUTES)
	imports = frappe.get_all(
		"Wikify Import",
		filters={"status": ("in", RUNNING_STATUSES), "modified": ("<", cutoff)},
		fields=["name", "status", "stage_progress", "source_document"],
	)
	for row in imports:
		if is_job_enqueued(import_job_id(row.name)):
			continue
		retry_hint = "Click Remediate to retry." if row.source_document else "Import the PDF again to retry."
		error = (
			"The background job stopped before finishing (it timed out or the worker restarted). "
			+ retry_hint
		)
		frappe.db.set_value("Wikify Import", row.name, "error", error)
		publish_progress(row.name, row.stage_progress, "Background job stopped", status="Failed")
		log(row.name, "error", "remediate" if row.status == "Remediating" else "parse", error)
