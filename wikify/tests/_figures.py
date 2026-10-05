# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from __future__ import annotations

import fitz
import frappe

from wikify.tests import _cleanup

PNG = (
	b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
	b"\x1f\x15\xc4\x89\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4\x00\x00"
	b"\x00\x00IEND\xaeB`\x82"
)


def add_document_with_pdf(test_case, title: str, project: str | None = None):
	source_document = frappe.get_doc(
		{"doctype": "Source Document", "title": title, "project": project}
	).insert(ignore_permissions=True)
	test_case.addCleanup(_cleanup.delete_document, source_document.name)

	pdf = fitz.open()
	pdf.new_page(width=612, height=792).insert_text((72, 90), title, fontsize=12)
	pdf_file = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"{frappe.generate_hash(length=6)}-figure-test.pdf",
			"content": pdf.tobytes(),
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	frappe.get_doc(
		{
			"doctype": "Wikify Import",
			"import_title": f"{title} Import",
			"project": project or frappe.db.get_value("Wikify Project", {"is_default": 1}, "name"),
			"pdf": pdf_file.file_url,
			"source_document": source_document.name,
		}
	).insert(ignore_permissions=True)
	return source_document
