"""Background-job helpers: stream progress + log lines to the SPA over realtime.

Two channels (see 03-backend-plan):
- `wikify_import_progress` — coarse percent + stage label (drives <Progress>).
- `wikify_import_log` — one Import Log Entry per line (streams the Overview log).
"""

from __future__ import annotations

import json

import frappe


def publish_progress(
	import_name: str,
	percent: float,
	label: str,
	status: str | None = None,
) -> None:
	"""Persist + broadcast progress so both live listeners and refetches agree."""
	values = {"stage_progress": percent, "stage_label": label}
	if status:
		values["status"] = status
	frappe.db.set_value("Wikify Import", import_name, values)
	frappe.db.commit()

	payload = {"import": import_name, "percent": percent, "stage_label": label}
	if status:
		payload["status"] = status
	frappe.publish_realtime("wikify_import_progress", payload)


def log(
	import_name: str,
	level: str,
	stage: str,
	message: str,
	meta: dict | None = None,
) -> None:
	"""Append an Import Log Entry and stream it live."""
	seq = frappe.db.count("Import Log Entry", {"import": import_name})
	entry = frappe.get_doc(
		{
			"doctype": "Import Log Entry",
			"import": import_name,
			"idx_seq": seq,
			"level": level,
			"stage": stage,
			"message": message,
			"meta": json.dumps(meta) if meta else None,
		}
	)
	entry.insert(ignore_permissions=True)
	frappe.db.commit()

	frappe.publish_realtime(
		"wikify_import_log",
		{
			"import": import_name,
			"idx_seq": seq,
			"level": level,
			"stage": stage,
			"message": message,
			"meta": meta,
		},
	)
