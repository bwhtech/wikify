# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe.model.document import Document


class WikifyImport(Document):
	def after_insert(self) -> None:
		_bump_import_count(self.project, +1)

	def on_trash(self) -> None:
		_bump_import_count(self.project, -1)


def _bump_import_count(project: str | None, delta: int) -> None:
	"""Keep `Wikify Project.import_count` denormalized for the project cards."""
	if not project:
		return
	current = frappe.db.get_value("Wikify Project", project, "import_count") or 0
	frappe.db.set_value("Wikify Project", project, "import_count", max(0, current + delta))
