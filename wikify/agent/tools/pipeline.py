"""Pipeline tools (0.2 Slice 14) — re-run the existing classify / generate jobs.

  - `reclassify` — re-tag the whole section tree (re-run classification after manual
    edits). Reuses `jobs/classify.py`.
  - `regenerate_wiki` — idempotently re-project the approved tree into the existing Wiki
    Space. Reuses `jobs/generate.py`.

Both are document-wide and **confirm-gated** (the loop holds them for a UI confirm card);
they enqueue the real jobs, which stream their own progress on the import.
"""

from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool


def _reclassify(ctx: Ctx, args: dict) -> str:
	import_name = ctx.default_import(args.get("source_document"))
	if not import_name:
		return _("No document specified. Open a document or pass `source_document`.")
	frappe.enqueue("wikify.jobs.classify.run", queue="long", timeout=1800, import_name=import_name)
	return _("Enqueued a reclassification of {0}. The Explore tags refresh when it finishes.").format(
		import_name
	)


def _regenerate_wiki(ctx: Ctx, args: dict) -> str:
	import_name = ctx.default_import(args.get("source_document"))
	if not import_name:
		return _("No document specified. Open a document or pass `source_document`.")
	wiki_space = frappe.db.get_value("Wikify Import", import_name, "wiki_space")
	if not wiki_space:
		return _(
			"This document has no generated wiki yet — generate it from the Wiki tab first, "
			"then I can regenerate it."
		)
	frappe.enqueue(
		"wikify.jobs.generate.run",
		queue="long",
		timeout=1800,
		import_name=import_name,
		wiki_space=wiki_space,
	)
	return _("Enqueued a wiki regeneration of {0} into its existing space.").format(import_name)


TOOLS = [
	Tool(
		name="reclassify",
		side="server",
		description=(
			"Re-run classification on the whole document (re-tag every section). Use after "
			"tree edits change titles/groupings. Expensive — confirm with the user first."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
			},
		},
		handler=_reclassify,
		confirm=True,
	),
	Tool(
		name="regenerate_wiki",
		side="server",
		description=(
			"Re-run wiki generation (idempotent) into the document's existing Wiki Space, "
			"picking up tree/tag/canonical changes. Expensive — confirm with the user first."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
			},
		},
		handler=_regenerate_wiki,
		confirm=True,
	),
]
