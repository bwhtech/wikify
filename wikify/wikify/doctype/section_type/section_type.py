# Copyright (c) 2026, Hussain Nagaria and contributors
# For license information, please see license.txt

from __future__ import annotations

import re

import frappe
from frappe import _
from frappe.model.document import Document


def normalize_label(label: str | None) -> str:
	"""Casefold + collapse whitespace. Two types whose labels normalize equal ARE the
	same type — `label` is identity for humans and the classifier alike (0.4 slice 21)."""
	return re.sub(r"\s+", " ", (label or "").strip()).casefold()


def find_by_normalized_label(label: str | None) -> str | None:
	"""Existing Section Type whose label normalizes equal, else None. Full scan — the
	taxonomy is a couple dozen rows."""
	want = normalize_label(label)
	if not want:
		return None
	for row in frappe.get_all("Section Type", fields=["name", "label"]):
		if normalize_label(row.label) == want:
			return row.name
	return None


class SectionType(Document):
	def validate(self):
		existing = find_by_normalized_label(self.label)
		if existing and existing != self.name:
			frappe.throw(
				_("A Section Type labeled '{0}' already exists as '{1}'.").format(self.label, existing),
				frappe.DuplicateEntryError,
			)
