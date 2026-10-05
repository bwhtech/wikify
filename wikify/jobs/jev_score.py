from __future__ import annotations

import frappe

from wikify.engine import llm, settings
from wikify.engine.verify import jev
from wikify.jobs._util import log, publish_progress


def job_id(import_name: str) -> str:
	return f"wikify-jev-score::{import_name}"


def run(import_name: str) -> dict | None:
	if not settings.jev_enabled():
		return None
	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		return None
	if not llm.has_openrouter():
		log(import_name, "warn", "jev", "Jev scoring skipped: no OPENROUTER_KEY")
		return None

	try:
		pdf_path = frappe.get_doc("File", {"file_url": imp.pdf}).get_full_path()
		log(import_name, "info", "jev", "Scoring conversion with Jev")

		def progress_cb(done: int, total: int) -> None:
			publish_progress(import_name, 100, f"Jev scoring page {done}/{total}", persist=False)

		summary = jev.score_document(imp.source_document, pdf_path, progress_cb=progress_cb)
	except Exception:
		frappe.log_error(title=f"Jev scoring failed for {import_name}")
		log(import_name, "error", "jev", "Jev scoring failed — see Error Log")
		return None

	if summary["score"] is None:
		message = f"Jev score unavailable — 0/{summary['total']} pages scored"
	else:
		message = (
			f"Jev score {round(summary['score'] * 100)}/100 — "
			f"{summary['scored']}/{summary['total']} pages scored, {summary['low']} low"
		)
	if summary["skipped"]:
		message += f", {summary['skipped']} skipped"
	if summary["errors"]:
		message += f", {summary['errors']} failed"
	if summary["cost"]:
		message += f" (${summary['cost']:.4f})"
	log(import_name, "warn" if summary["errors"] else "info", "jev", message, meta=summary)
	return summary
