"""Whitelisted APIs for the Source Section tree (Slice 4 — read-only)."""

from __future__ import annotations

import frappe


@frappe.whitelist()
def get_tree(source_document: str) -> list[dict]:
	"""Nested section tree for a Source Document, ordered by tree position (`lft`).

	Returns the root sections, each with a recursive `children` list — the shape the
	frappe-ui `Tree` consumes. Read-only here; drag-reorder lands in Slice 5.
	"""
	rows = frappe.get_all(
		"Source Section",
		filters={"source_document": source_document},
		fields=[
			"name",
			"parent_source_section",
			"title",
			"is_group",
			"level",
			"section_type",
			"hierarchy_path",
			"page_start",
			"page_end",
			"sort_order",
			"markdown",
		],
		order_by="lft asc",
	)

	by_name = {r["name"]: {**r, "children": []} for r in rows}
	roots: list[dict] = []
	for r in rows:
		node = by_name[r["name"]]
		parent = r["parent_source_section"]
		if parent and parent in by_name:
			by_name[parent]["children"].append(node)
		else:
			roots.append(node)
	return roots
