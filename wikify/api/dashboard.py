from __future__ import annotations

from collections import Counter, defaultdict

import frappe

from wikify.api.explore import UNTAGGED, UNTAGGED_COLOR, UNTAGGED_LABEL
from wikify.api.permission import assert_readable, readable_projects


def _subcategory(section: dict, by_name: dict[str, dict]) -> str:
	while (parent := by_name.get(section["parent_source_section"])) is not None:
		section = parent
	return (section["title"] or "").strip() or "Untitled"


@frappe.whitelist()
def summary(
	project: str | None = None, section_type: str | None = None, subcategory: str | None = None
) -> dict:
	assert_readable(project)
	projects = frappe.get_all(
		"Wikify Project",
		filters={"name": ["in", readable_projects()]},
		fields=["name", "project_name"],
		order_by="is_default desc, project_name asc",
	)
	docs = frappe.get_all(
		"Source Document",
		filters={"project": ["in", [p["name"] for p in projects]]},
		fields=["name", "title", "project"],
	)
	doc_by_name = {d["name"]: d for d in docs}
	sections = (
		frappe.get_all(
			"Source Section",
			filters={"source_document": ["in", list(doc_by_name)]},
			fields=[
				"name",
				"title",
				"hierarchy_path",
				"section_type",
				"source_document",
				"parent_source_section",
				"page_start",
				"page_end",
			],
			order_by="source_document asc, lft asc",
		)
		if docs
		else []
	)
	by_name = {s["name"]: s for s in sections}
	for s in sections:
		s["project"] = doc_by_name[s["source_document"]]["project"]
		s["section_type"] = s["section_type"] or UNTAGGED
		s["subcategory"] = _subcategory(s, by_name)
	docs_with_sections = {s["source_document"] for s in sections}
	parsed_docs = [d for d in docs if d["name"] in docs_with_sections]

	in_project = [s for s in sections if not project or s["project"] == project]
	in_type = [s for s in in_project if not section_type or s["section_type"] == section_type]
	in_sub = [s for s in in_type if not subcategory or s["subcategory"] == subcategory]

	docs_per_project = Counter(d["project"] for d in parsed_docs)
	return {
		"cards": {
			"projects": len(projects),
			"documents": len(parsed_docs),
			"categories": len({s["section_type"] for s in sections}),
			"sections": len(sections),
		},
		"projects": [{**p, "documents": docs_per_project[p["name"]]} for p in projects],
		"categories": _category_rows(in_project),
		"subcategories": _subcategory_rows(in_type) if section_type else [],
		"sections": _section_rows(in_sub, doc_by_name) if subcategory else [],
	}


def _group(sections: list[dict], key: str) -> dict[str, list[dict]]:
	groups: dict[str, list[dict]] = defaultdict(list)
	for s in sections:
		groups[s[key]].append(s)
	return groups


def _category_rows(sections: list[dict]) -> list[dict]:
	by_type = _group(sections, "section_type")
	types = frappe.get_all(
		"Section Type",
		fields=["type_name", "label", "color"],
		order_by="is_other asc, creation asc",
	)
	types.append({"type_name": UNTAGGED, "label": UNTAGGED_LABEL, "color": UNTAGGED_COLOR})
	rows = [
		{
			"type_name": t["type_name"],
			"label": t["label"] or t["type_name"],
			"color": t["color"] or "#9ca3af",
			"sections": len(own),
			"documents": len({s["source_document"] for s in own}),
			"subcategories": len({s["subcategory"] for s in own}),
		}
		for t in types
		if (own := by_type.get(t["type_name"]))
	]
	return sorted(rows, key=lambda r: -r["sections"])


def _subcategory_rows(sections: list[dict]) -> list[dict]:
	rows = [
		{
			"title": title,
			"sections": len(own),
			"documents": len({s["source_document"] for s in own}),
		}
		for title, own in _group(sections, "subcategory").items()
	]
	return sorted(rows, key=lambda r: (-r["sections"], r["title"].lower()))


def _section_rows(sections: list[dict], doc_by_name: dict[str, dict]) -> list[dict]:
	return [
		{
			"name": s["name"],
			"title": s["title"],
			"hierarchy_path": s["hierarchy_path"],
			"page_start": s["page_start"],
			"page_end": s["page_end"],
			"doc_title": doc_by_name[s["source_document"]]["title"] or s["source_document"],
		}
		for s in sections
	]


@frappe.whitelist()
def section_detail(name: str) -> dict:
	section = frappe.db.get_value(
		"Source Section",
		name,
		[
			"name",
			"title",
			"hierarchy_path",
			"page_start",
			"page_end",
			"markdown",
			"section_type",
			"source_document",
			"lft",
			"rgt",
		],
		as_dict=True,
	)
	if not section:
		raise frappe.DoesNotExistError
	doc = frappe.db.get_value(
		"Source Document", section.source_document, ["title", "project", "pdf", "page_count"], as_dict=True
	)
	assert_readable(doc.project)
	section_type = (
		frappe.get_cached_value("Section Type", section.section_type, ["label", "color"], as_dict=True)
		if section.section_type
		else {"label": UNTAGGED_LABEL, "color": UNTAGGED_COLOR}
	) or {}
	ancestors = frappe.get_all(
		"Source Section",
		filters={
			"source_document": section.source_document,
			"lft": ["<", section.lft],
			"rgt": [">", section.rgt],
		},
		pluck="title",
		order_by="lft asc",
	)
	children = frappe.get_all(
		"Source Section",
		filters={"parent_source_section": name},
		fields=["name", "title", "page_start", "page_end"],
		order_by="lft asc",
	)
	return {
		"name": section.name,
		"title": section.title,
		"hierarchy_path": section.hierarchy_path,
		"page_start": section.page_start,
		"page_end": section.page_end,
		"ancestors": ancestors,
		"markdown": section.markdown,
		"doc_title": doc.title,
		"pdf": doc.pdf,
		"page_count": doc.page_count,
		"project_name": frappe.get_cached_value("Wikify Project", doc.project, "project_name"),
		"category": section_type.get("label") or section.section_type,
		"category_color": section_type.get("color"),
		"children": children,
	}
