from __future__ import annotations

import frappe

MIN_QUERY_LENGTH = 2
IMPORT_LIMIT = 20
CONVERSATION_LIMIT = 10


@frappe.whitelist()
def search(query: str) -> dict:
	query = query.strip()
	if len(query) < MIN_QUERY_LENGTH:
		return {"imports": [], "conversations": []}
	return {
		"imports": find("Wikify Import", query, ["project", "status"], IMPORT_LIMIT),
		"conversations": find("Wikify Ask Session", query, ["project"], CONVERSATION_LIMIT),
	}


def find(doctype: str, query: str, extra_fields: list[str], limit: int) -> list[dict]:
	if not frappe.has_permission(doctype, "read"):
		return []
	meta = frappe.get_meta(doctype)
	search_fields = [f for f in dict.fromkeys([meta.title_field, *meta.get_search_fields()]) if f != "name"]
	pattern = f"%{query}%"
	return frappe.get_list(
		doctype,
		or_filters={field: ["like", pattern] for field in search_fields},
		fields=["name", f"{meta.title_field} as title", *search_fields, *extra_fields],
		order_by=f"{meta.sort_field} {meta.sort_order}",
		limit_page_length=limit,
	)
