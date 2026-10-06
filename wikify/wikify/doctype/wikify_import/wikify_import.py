# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document

RUNNING_STATUSES = ("Queued", "Parsing", "Remediating", "Generating Wiki")


class WikifyImport(Document):
	def after_insert(self) -> None:
		_bump_import_count(self.project, +1)

	def on_trash(self) -> None:
		if self.status in RUNNING_STATUSES:
			frappe.throw(
				_("{0} is still {1}. Delete it once the job finishes.").format(
					frappe.bold(self.import_title), self.status.lower()
				),
				title=_("Cannot delete"),
			)
		frappe.db.delete("Import Log Entry", {"import": self.name})
		if self.source_document:
			frappe.delete_doc("Source Document", self.source_document, ignore_permissions=True, force=True)
		_bump_import_count(self.project, -1)


def _bump_import_count(project: str | None, delta: int) -> None:
	"""Keep `Wikify Project.import_count` denormalized for the project cards."""
	if not project:
		return
	current = frappe.db.get_value("Wikify Project", project, "import_count") or 0
	frappe.db.set_value("Wikify Project", project, "import_count", max(0, current + delta))
