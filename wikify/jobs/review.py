"""Review previews: re-read one page with the vision model and keep the result as the
page's candidate. The SPA hears `wikify_review_preview` and reloads the item."""

from __future__ import annotations

import frappe

from wikify.engine import store
from wikify.engine.reparse import reparse_page
from wikify.jobs._util import project_context

PREVIEW_TIMEOUT = 10 * 60


def preview_job_id(page_name: str) -> str:
	return f"review-preview:{page_name}"


def preview(import_name: str, page_no: int, instruction: str = "") -> None:
	imp = frappe.get_doc("Wikify Import", import_name)
	payload = {"import": import_name, "page_no": page_no}
	try:
		pdf_path = store.get_import_pdf_path(imp.source_document)
		if not pdf_path:
			raise ValueError("The source PDF of this import is missing.")
		result = reparse_page(
			imp.source_document,
			pdf_path,
			page_no,
			method="vlm",
			instruction=instruction,
			project_context=project_context(imp),
			adopt=False,
		)
		payload.update(ok=True, composite=result["composite"])
	except Exception as error:
		frappe.db.rollback()
		frappe.log_error(f"Review preview failed for page {page_no} of {import_name}")
		payload.update(ok=False, error=str(error) or type(error).__name__)
	# After commit, so the SPA's reload reads the stored candidate.
	frappe.publish_realtime("wikify_review_preview", payload, user=frappe.session.user, after_commit=True)
