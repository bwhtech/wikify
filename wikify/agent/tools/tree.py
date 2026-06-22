"""Write / tree tools (0.2 Slice 14) — reuse the `api.sections` NestedSet mutations.

These apply directly (cheap, reversible by re-editing): move, rename, retag, and the
include-in-wiki toggle. Each calls the existing whitelisted mutation so `lft`/`rgt`/
`level`/`hierarchy_path`/`is_group` stay consistent, then returns a short confirming
summary. All are flagged `mutates=True` so the loop tells open Tree views to refetch.
"""

from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool
from wikify.api import sections


def _title(name: str) -> str:
	return frappe.db.get_value("Source Section", name, "title") or name


def _move_section(ctx: Ctx, args: dict) -> str:
	name = args.get("name")
	if not name:
		return _("Provide the section `name` (the id shown in <angle brackets> in the tree).")
	new_parent = args.get("new_parent") or None
	new_index = args.get("new_index")
	try:
		sections.move_section(name, new_parent=new_parent, new_index=new_index)
	except frappe.ValidationError as e:
		return _("Couldn't move section: {0}").format(str(e))
	where = f"under {_title(new_parent)}" if new_parent else "to the top level"
	return _("Moved '{0}' {1}.").format(_title(name), where)


def _rename_section(ctx: Ctx, args: dict) -> str:
	name, title = args.get("name"), args.get("title")
	if not name or not (title or "").strip():
		return _("Provide the section `name` and a non-empty `title`.")
	old = _title(name)
	try:
		sections.rename_section(name, title)
	except frappe.ValidationError as e:
		return _("Couldn't rename section: {0}").format(str(e))
	return _("Renamed '{0}' → '{1}'.").format(old, title.strip())


def _set_section_type(ctx: Ctx, args: dict) -> str:
	name, section_type = args.get("name"), args.get("section_type")
	if not name:
		return _("Provide the section `name`.")
	try:
		res = sections.set_section_type(name, section_type)
	except frappe.ValidationError as e:
		return _("Couldn't retag section: {0}").format(str(e))
	tag = res["section_type"] or "(untagged)"
	return _("Tagged '{0}' as {1}.").format(_title(name), tag)


def _toggle_include_in_wiki(ctx: Ctx, args: dict) -> str:
	name = args.get("name")
	if not name:
		return _("Provide the section `name`.")
	include = args.get("include")
	include = True if include is None else bool(include)
	res = sections.toggle_include(name, include)
	verb = "Included" if include else "Excluded"
	return _("{0} '{1}' (and {2} section(s) in its subtree) {3} wiki generation.").format(
		verb, _title(name), res["count"], "in" if include else "from"
	)


TOOLS = [
	Tool(
		name="move_section",
		side="server",
		description=(
			"Reparent and/or reorder a section in the tree. Pass the section id and the new "
			"parent id (omit new_parent to move it to the top level); new_index is the 0-based "
			"position among the destination's children (omit to append)."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id to move."},
				"new_parent": {
					"type": "string",
					"description": "Destination parent section id. Omit for top level.",
				},
				"new_index": {"type": "integer", "description": "0-based position among siblings."},
			},
			"required": ["name"],
		},
		handler=_move_section,
		mutates=True,
	),
	Tool(
		name="rename_section",
		side="server",
		description="Rename a section (updates its hierarchy path and its descendants').",
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
				"title": {"type": "string", "description": "New title."},
			},
			"required": ["name", "title"],
		},
		handler=_rename_section,
		mutates=True,
	),
	Tool(
		name="set_section_type",
		side="server",
		description=(
			"Retag a section with a Section Type (the machine key, e.g. surgical_procedures). "
			"Use list_section_types to see the taxonomy; create_section_type to add a new tag. "
			"Pass an empty section_type to clear the tag."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
				"section_type": {
					"type": "string",
					"description": "Section Type machine key, or empty to clear.",
				},
			},
			"required": ["name"],
		},
		handler=_set_section_type,
		mutates=True,
	),
	Tool(
		name="toggle_include_in_wiki",
		side="server",
		description="Include or exclude a section (and its whole subtree) from wiki generation.",
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
				"include": {"type": "boolean", "description": "true to include, false to exclude."},
			},
			"required": ["name", "include"],
		},
		handler=_toggle_include_in_wiki,
		mutates=True,
	),
]
