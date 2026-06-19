"""Whitelisted APIs for the Imports flow."""

from __future__ import annotations

import frappe


@frappe.whitelist()
def start_import(pdf_file_url: str, title: str) -> str:
	"""Create a Wikify Import for an uploaded PDF and enqueue the parse job.

	Returns the new Import's name so the SPA can route to its detail page.
	"""
	imp = frappe.new_doc("Wikify Import")
	imp.import_title = title
	imp.pdf = pdf_file_url
	imp.status = "Queued"
	imp.insert()

	frappe.enqueue(
		"wikify.jobs.parse.run",
		queue="long",
		timeout=3600,
		import_name=imp.name,
	)
	return imp.name
