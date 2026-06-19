"""Parse job — the walking skeleton's spine (Slice 1b).

Render + baseline-parse every page of an Import's PDF into Source Document +
Source Page rows, streaming progress + log lines, and land the Import in `Review`.
Scoring, sectionizing, and classification are added in later slices.
"""

from __future__ import annotations

import frappe
from frappe.utils import now_datetime

from wikify.engine import parse_pdf
from wikify.jobs._util import log, publish_progress


def run(import_name: str) -> None:
	imp = frappe.get_doc("Wikify Import", import_name)
	try:
		imp.db_set("status", "Parsing")
		imp.db_set("started_at", now_datetime())
		publish_progress(import_name, 0, "Starting parse", status="Parsing")
		log(import_name, "info", "parse", f"Starting parse of {imp.import_title}")

		pdf_path = frappe.get_doc("File", {"file_url": imp.pdf}).get_full_path()

		def progress_cb(done: int, total: int) -> None:
			publish_progress(import_name, done / total * 100, f"Parsing page {done}/{total}")

		def log_cb(page_no: int, total: int, kind: str) -> None:
			log(import_name, "info", "parse", f"Parsed page {page_no}/{total} ({kind})")

		source_document = parse_pdf(
			pdf_path,
			title=imp.import_title,
			import_name=import_name,
			pdf_url=imp.pdf,
			progress_cb=progress_cb,
			log_cb=log_cb,
		)

		page_count = frappe.db.get_value("Source Document", source_document, "page_count")
		imp.db_set("source_document", source_document)
		imp.db_set("page_count", page_count)
		imp.db_set("completed_at", now_datetime())
		publish_progress(import_name, 100, f"Parsed {page_count} pages", status="Review")
		log(import_name, "info", "parse", f"Done — {page_count} pages, status Review")
	except Exception:
		error = frappe.get_traceback()
		imp.db_set("status", "Failed")
		imp.db_set("error", error)
		frappe.db.commit()
		log(import_name, "error", "parse", "Parse failed — see error on the import")
		raise
