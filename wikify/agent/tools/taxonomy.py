"""Taxonomy tool (0.2 Slice 14) — extend the Section Type master.

`create_section_type` lets the agent add a tag the corpus needs ("the user wants a
`consent_forms` type"). It reuses `api.sections.create_section_type`, which slugifies the
name to a snake_case machine key and is idempotent on an existing key.
"""

from __future__ import annotations

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
]
