# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""0.5 Slice 26 — backfill `Section Reference` rows for every existing document.

Extraction is idempotent (wipe + rebuild per document), so re-running is safe.
"""

import frappe

from wikify.engine.refs import extract_references


def execute():
	for name in frappe.get_all("Source Document", pluck="name"):
		extract_references(name)
