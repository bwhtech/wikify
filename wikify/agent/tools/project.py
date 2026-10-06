from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool


def find_project(project: str | None) -> frappe._dict | None:
	if not project or not frappe.has_permission("Wikify Project", ptype="read"):
		return None
	matched = frappe.get_list(
		"Wikify Project",
		or_filters={"name": project, "project_name": project},
		fields=["name", "project_name"],
		limit=1,
	)
	return matched[0] if matched else None


def save_project_rule(ctx: Ctx, args: dict) -> str:
	rule = " ".join((args.get("rule") or "").split())
	if not rule:
		return _("Provide the `rule` to save.")
	project = find_project(args.get("project"))
	if not project:
		return _("Project '{0}' not found. Pass the project name shown in the context.").format(
			args.get("project")
		)
	if not frappe.has_permission("Wikify Project", "write", project.name):
		return _("You don't have permission to edit project '{0}', so the rule was not saved.").format(
			project.project_name
		)
	doc = frappe.get_doc("Wikify Project", project.name)
	existing = doc.context_prompt or ""
	separator = "\n" if existing and not existing.endswith("\n") else ""
	doc.context_prompt = f"{existing}{separator}- {rule}"
	doc.save()
	return _(
		'Saved to the context prompt of project {0}. Future AI steps and chats in this project follow it: "{1}"'
	).format(project.project_name, rule)


def summarize_save_project_rule(tool_args: dict) -> str:
	project = find_project(tool_args.get("project"))
	return _('Add this rule to the context prompt of project {0}: "{1}"').format(
		project.project_name if project else tool_args.get("project"),
		" ".join((tool_args.get("rule") or "").split()),
	)


DESCRIPTION = (
	"Save a standing rule to a project's context prompt, which every future AI step and "
	"chat in that project reads. Appends one line; never changes existing text. Use ONLY "
	"when the user asks for something to apply to future documents in the project, not "
	"for a one-off change. After saving, quote the rule back to the user word for word."
)

TOOLS = [
	Tool(
		name="save_project_rule",
		side="server",
		description=DESCRIPTION,
		parameters={
			"type": "object",
			"properties": {
				"project": {
					"type": "string",
					"description": "The project's name as shown in the attached context.",
				},
				"rule": {
					"type": "string",
					"description": "The rule as one self-contained sentence, in the user's words.",
				},
			},
			"required": ["project", "rule"],
		},
		handler=save_project_rule,
		confirm=True,
		confirm_summary=summarize_save_project_rule,
		mutates=True,
	),
]
