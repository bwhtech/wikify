"""Reclassify job (Slice 6) — re-tag a doc's Source Sections with a `section_type`.

Parse/remediate classify eagerly; this job re-runs classification on demand after
manual tree edits (rename/reparent/delete change titles + content groupings, so the
types can shift). It streams per-section log lines over the Overview channel and emits
a `wikify_classify_done` event the SPA listens for to refresh Explore — it does **not**
change the import's persisted status (a doc stays in Review or Graphed while re-tagging).
"""

from __future__ import annotations

import frappe

from wikify.engine import classify_document
from wikify.jobs._util import log, project_context, publish_progress


def run(import_name: str) -> None:
	imp = frappe.get_doc("Wikify Import", import_name)
	source_document = imp.source_document
	if not source_document:
		return
	try:
		publish_progress(import_name, 0, "Classifying sections")
		log(import_name, "info", "classify", f"Classifying sections of {imp.import_title}")

		context = project_context(imp)
		if context:
			log(import_name, "info", "classify", f"Using project context ({len(context)} chars)")

		def progress_cb(done: int, total: int, title: str, section_type: str) -> None:
			percent = (done / total * 100) if total else 100
			publish_progress(import_name, percent, f"Classifying section {done}/{total}")
			log(
				import_name,
				"info",
				"classify",
				f"{title} → {section_type}",
				meta={"section_type": section_type},
			)

		result = classify_document(source_document, progress_cb=progress_cb, project_context=context)

		publish_progress(import_name, 100, f"Classified {result['sections']} sections")
		by_type = ", ".join(f"{t}: {n}" for t, n in sorted(result["by_type"].items()))
		log(
			import_name,
			"info",
			"classify",
			f"Done — {result['sections']} sections classified ({by_type})",
		)
		frappe.publish_realtime(
			"wikify_classify_done",
			{
				"import": import_name,
				"source_document": source_document,
				"by_type": result["by_type"],
			},
		)
	except Exception:
		log(import_name, "error", "classify", "Reclassification failed — see error logs")
		frappe.publish_realtime(
			"wikify_classify_done", {"import": import_name, "source_document": source_document, "error": True}
		)
		raise
