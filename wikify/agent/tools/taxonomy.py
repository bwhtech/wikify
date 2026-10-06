"""Taxonomy tool (0.2 Slice 14) — extend the Section Type master.

`create_section_type` lets the agent add a tag the corpus needs ("the user wants a
`consent_forms` type"). It reuses `api.sections.create_section_type`, which slugifies the
name to a snake_case machine key and is idempotent on an existing key.
"""

from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool
from wikify.api import sections


def _create_section_type(ctx: Ctx, args: dict) -> str:
	type_name = args.get("type_name")
	if not type_name:
		return _("Provide a `type_name` for the new Section Type.")
	res = sections.create_section_type(
		type_name,
		label=args.get("label"),
		description=args.get("description"),
		color=args.get("color"),
	)
	if res["existed"]:
		return _("Section Type '{0}' already exists.").format(res["type_name"])
	return _("Created Section Type '{0}'. Use set_section_type to tag sections with it.").format(
		res["type_name"]
	)


def rename_section_type(ctx: Ctx, args: dict) -> str:
	type_name, new_label = args.get("type_name"), args.get("new_label")
	if not type_name or not (new_label or "").strip():
		return _("Provide the `type_name` and a non-empty `new_label`.")
	try:
		result = sections.rename_section_type(type_name, new_label)
	except (frappe.ValidationError, frappe.PermissionError, frappe.DuplicateEntryError) as error:
		return _("Couldn't rename Section Type: {0}").format(str(error))
	return _("Renamed Section Type '{0}' to '{1}'. Its sections keep the type.").format(
		result["old_label"], result["label"]
	)


def summarize_rename_section_type(tool_args: dict) -> str:
	type_name = tool_args.get("type_name")
	return _("Rename Section Type '{0}' to '{1}' ({2} section(s) use it).").format(
		type_name, tool_args.get("new_label"), frappe.db.count("Source Section", {"section_type": type_name})
	)


def merge_section_types(ctx: Ctx, args: dict) -> str:
	source, target = args.get("source"), args.get("target")
	if not source or not target:
		return _("Provide the `source` type to remove and the `target` type its sections move to.")
	try:
		result = sections.merge_section_types(source, target)
	except (frappe.ValidationError, frappe.PermissionError) as error:
		return _("Couldn't merge Section Types: {0}").format(str(error))
	return _("Moved {0} section(s) from '{1}' to '{2}' and deleted '{1}'.").format(
		result["moved"], source, target
	)


def summarize_merge_section_types(tool_args: dict) -> str:
	source, target = tool_args.get("source"), tool_args.get("target")
	return _("Move {0} section(s) from Section Type '{1}' to '{2}', then delete '{1}'.").format(
		frappe.db.count("Source Section", {"section_type": source}), source, target
	)


TOOLS = [
	Tool(
		name="create_section_type",
		side="server",
		description=(
			"Add a new Section Type (tag) to the taxonomy. type_name is slugified to a "
			"snake_case machine key; give a human label and optionally a description/color."
		),
		parameters={
			"type": "object",
			"properties": {
				"type_name": {"type": "string", "description": "New type name (slugified to a machine key)."},
				"label": {"type": "string", "description": "Human-readable label."},
				"description": {
					"type": "string",
					"description": "What this type covers (steers the classifier).",
				},
				"color": {"type": "string", "description": "Optional chip color (hex)."},
			},
			"required": ["type_name"],
		},
		handler=_create_section_type,
		mutates=True,
	),
	Tool(
		name="rename_section_type",
		side="server",
		description=(
			"Change a Section Type's human label. The machine key stays, so every section "
			"tagged with it keeps the type. The user must confirm before it runs."
		),
		parameters={
			"type": "object",
			"properties": {
				"type_name": {"type": "string", "description": "Machine key of the type to rename."},
				"new_label": {"type": "string", "description": "New human-readable label."},
			},
			"required": ["type_name", "new_label"],
		},
		handler=rename_section_type,
		mutates=True,
		confirm=True,
		confirm_summary=summarize_rename_section_type,
	),
	Tool(
		name="merge_section_types",
		side="server",
		description=(
			"Move every section tagged `source` to `target`, then delete `source` from the "
			"taxonomy. Also the way to delete a type: its sections need a replacement type. "
			"The user must confirm before it runs."
		),
		parameters={
			"type": "object",
			"properties": {
				"source": {"type": "string", "description": "Machine key of the type to remove."},
				"target": {"type": "string", "description": "Machine key of the type its sections move to."},
			},
			"required": ["source", "target"],
		},
		handler=merge_section_types,
		mutates=True,
		confirm=True,
		confirm_summary=summarize_merge_section_types,
	),
]
