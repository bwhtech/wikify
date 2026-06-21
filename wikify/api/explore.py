"""Whitelisted APIs for the Explore screens (Slice 6).

The headline query — *"all job descriptions across all PDFs"* — is a metadata filter
on `Source Section.section_type`, not a fuzzy search. `type_summary` drives the filter
chips / type rail (counts incl. the `other` catch-all and an `untagged` bucket);
`sections_by_type` returns the matching sections grouped by Source Document with
page-range provenance. Both work globally (no `source_document`) or per-document.

Mirrors the POC `graph.section_type_counts` / `graph.sections_by_type`.
"""

from __future__ import annotations

import frappe
from frappe.query_builder.functions import Count

_UNTAGGED = "__untagged__"  # sentinel for sections classification hasn't reached yet


def _scope(source_document: str | None) -> dict:
	return {"source_document": source_document} if source_document else {}


@frappe.whitelist()
def type_summary(source_document: str | None = None) -> list[dict]:
	"""Per-type section counts for the chips/rail (global or per-doc).

	Returns every Section Type in display order with its count (incl. zero, so chips are
	stable), then an `untagged` bucket appended only when some section has no type yet.
	"""
	table = frappe.qb.DocType("Source Section")
	query = (
		frappe.qb.from_(table)
		.select(table.section_type, Count(table.name).as_("count"))
		.groupby(table.section_type)
	)
	if source_document:
		query = query.where(table.source_document == source_document)
	rows = query.run(as_dict=True)
	counts = {r["section_type"] or _UNTAGGED: r["count"] for r in rows}

	types = frappe.get_all(
		"Section Type",
		fields=["type_name", "label", "color", "is_other"],
		order_by="is_other asc, creation asc",
	)
	summary = [
		{
			"type_name": t["type_name"],
			"label": t["label"] or t["type_name"],
			"color": t["color"] or "#9ca3af",
			"is_other": t["is_other"],
			"count": counts.get(t["type_name"], 0),
		}
		for t in types
	]
	if counts.get(_UNTAGGED):
		summary.append(
			{
				"type_name": _UNTAGGED,
				"label": "Untagged",
				"color": "#cbd5e1",
				"is_other": 0,
				"count": counts[_UNTAGGED],
			}
		)
	return summary


@frappe.whitelist()
def sections_by_type(section_type: str, source_document: str | None = None) -> list[dict]:
	"""Sections of one type, grouped by Source Document (the cross-document headline).

	Each group: `{source_document, doc_title, sections: [...]}`, ordered by document
	title then tree position. `section_type` may be the `untagged` sentinel.
	"""
	filters = _scope(source_document)
	filters["section_type"] = ["is", "not set"] if section_type == _UNTAGGED else section_type

	rows = frappe.get_all(
		"Source Section",
		filters=filters,
		fields=[
			"name",
			"source_document",
			"title",
			"hierarchy_path",
			"level",
			"page_start",
			"page_end",
		],
		order_by="source_document asc, lft asc",
	)
	if not rows:
		return []

	docs = {
		d["name"]: d
		for d in frappe.get_all(
			"Source Document",
			filters={"name": ["in", list({r["source_document"] for r in rows})]},
			fields=["name", "title", "import"],
		)
	}

	groups: dict[str, dict] = {}
	for r in rows:
		sd = r["source_document"]
		doc = docs.get(sd, {})
		group = groups.setdefault(
			sd,
			{
				"source_document": sd,
				"doc_title": doc.get("title") or sd,
				"import_name": doc.get("import"),
				"sections": [],
			},
		)
		group["sections"].append(r)
	return sorted(groups.values(), key=lambda g: (g["doc_title"] or "").lower())
