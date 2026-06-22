# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

from __future__ import annotations

import frappe
from frappe import _
from frappe.model.document import Document


class WikifyProject(Document):
	def validate(self) -> None:
		self._enforce_single_default()

	def _enforce_single_default(self) -> None:
		"""Exactly one project may carry `is_default` (the "Uncategorized" catch-all)."""
		if not self.is_default:
			return
		other = frappe.db.exists("Wikify Project", {"is_default": 1, "name": ["!=", self.name or ""]})
		if other:
			frappe.throw(
				_("A default project already exists ({0}).").format(other),
				title=_("Duplicate default project"),
			)

	def on_trash(self) -> None:
		"""Block deleting the default catch-all or any project that still owns Imports."""
		if self.is_default:
			frappe.throw(
				_('The "Uncategorized" project cannot be deleted.'),
				title=_("Cannot delete"),
			)
		count = frappe.db.count("Wikify Import", {"project": self.name})
		if count:
			frappe.throw(
				_("Move or delete this project's {0} import(s) first.").format(count),
				title=_("Project not empty"),
			)
