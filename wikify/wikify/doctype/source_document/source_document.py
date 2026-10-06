# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

import frappe
from frappe.model.document import Document

from wikify.rag.events import document_structure_changed


class SourceDocument(Document):
	def on_trash(self) -> None:
		document_structure_changed(self.name)
		pages = frappe.get_all("Source Page", filters={"source_document": self.name}, pluck="name")
		if self.wiki_root_group:
			self.detach_files(pages)
		page_files = (
			frappe.get_all(
				"File",
				filters={"attached_to_doctype": "Source Page", "attached_to_name": ["in", pages]},
				pluck="name",
			)
			if pages
			else []
		)

		frappe.db.delete("Section Reference", {"source_document": self.name})
		frappe.db.delete("Source Section", {"source_document": self.name})
		# Pages go before their images: File refuses to delete a URL still set in Source Page.image.
		frappe.db.delete("Source Page", {"source_document": self.name})
		frappe.db.set_value("Wikify Agent Session", {"source_document": self.name}, "source_document", None)
		for file_name in page_files:
			frappe.delete_doc("File", file_name, ignore_permissions=True)

	def detach_files(self, pages: list[str]) -> None:
		# Published wiki pages embed these images by URL, so they outlive the document.
		detached = {"attached_to_doctype": None, "attached_to_name": None}
		frappe.db.set_value(
			"File", {"attached_to_doctype": "Source Document", "attached_to_name": self.name}, detached
		)
		if pages:
			frappe.db.set_value(
				"File", {"attached_to_doctype": "Source Page", "attached_to_name": ["in", pages]}, detached
			)
