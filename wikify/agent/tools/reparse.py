from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool
from wikify.engine.store import get_import_pdf_path


def _project_context(ctx: Ctx) -> str:
	if not ctx.project:
		return ""
	return frappe.db.get_value("Wikify Project", ctx.project, "context_prompt") or ""


# nosemgrep
def _page_no(args: dict) -> int | None:
	raw = args.get("page_no")
	try:
		return int(raw) if raw is not None else None
	except (TypeError, ValueError):
		return None


def _propagate_page(source_document: str, page_no: int) -> str:
	from wikify.engine.sectionize import rebuild_section_markdown, sections_covering_page

	owners = sections_covering_page(source_document, page_no)
	if len(owners) == 1:
		try:
			res = rebuild_section_markdown(owners[0].name)
		except ValueError as e:
			return " " + _("Section propagation failed ({0}) — the wiki preview is still stale.").format(
				str(e)
			)
		return " " + _(
			"Propagated into section '{0}' ({1} chars) — the wiki preview now shows it. If this "
			"document has a generated wiki, finish with sync_wiki_page on <{2}>."
		).format(res["title"], res["chars"], owners[0].name)
	if not owners:
		return " " + _(
			"No section's page range covers this page, so the wiki preview is NOT updated. "
			"Use edit_section_content on the right section if the fix must reach the wiki."
		)
	names = ", ".join(f"'{o.title}' <{o.name}>" for o in owners)
	return " " + _(
		"NOT yet visible in the wiki preview — this is a boundary page shared by {0}. "
		"Run rebuild_section_from_pages or edit_section_content on the right one, then "
		"sync_wiki_page if a wiki is generated."
	).format(names)


def _use_page_image(ctx: Ctx, args: dict) -> str:
	from wikify.agent.tools.content import _edit_section_content
	from wikify.engine import embed_page_image

	source_document = ctx.default_document(args.get("source_document"))
	page_no = _page_no(args)
	caption = (args.get("caption") or "").strip() or None
	section = args.get("section")
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if page_no is None:
		return _("Provide the `page_no` to embed as an image.")
	if bool(caption) == bool(section):
		return _(
			"Pass exactly one of `section` (add the page image to the end of that section) or "
			"`caption` (replace that image tag on the page). Nothing was changed."
		)
	if section:
		section_row = frappe.db.get_value(
			"Source Section", section, ["source_document", "markdown"], as_dict=True
		)
		if not section_row or section_row.source_document != source_document:
			return _("Section {0} not found in {1}.").format(section, source_document)
		image_url = frappe.db.get_value(
			"Source Page", {"source_document": source_document, "page_no": page_no}, "image"
		)
		if not image_url:
			return _("Page {0} has no rendered image to embed.").format(page_no)
		markdown = (section_row.markdown or "").rstrip()
		return (
			_edit_section_content(
				ctx,
				{
					"name": section,
					"mode": "replace",
					"content": f"{markdown}\n\n![Page {page_no}]({image_url})".lstrip(),
				},
			)
			+ " "
			+ _("Page {0} itself is unchanged.").format(page_no)
		)
	try:
		embed_page_image(source_document, page_no, caption)
	except (ValueError, RuntimeError) as e:
		return _("Couldn't embed the page image: {0}").format(str(e))
	return _(
		"Replaced the '{0}' image tag on page {1} with the full page photo — the rest of "
		"the page's content is untouched. The user can crop the figure out of it later on "
		"the wiki."
	).format(caption, page_no) + _propagate_page(source_document, page_no)


def _reparse_page(ctx: Ctx, args: dict) -> str:
	from wikify.engine import reparse_page

	source_document = ctx.default_document(args.get("source_document"))
	page_no = _page_no(args)
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if page_no is None:
		return _("Provide the `page_no` to re-parse.")
	pdf_path = get_import_pdf_path(source_document)
	if not pdf_path:
		return _("Couldn't locate the source PDF for {0}.").format(source_document)
	method = args.get("method")
	if method not in (None, "cleanup", "vlm"):
		return _("`method` must be 'cleanup' or 'vlm' (or omit for vlm, the default).")
	try:
		res = reparse_page(
			source_document,
			pdf_path,
			page_no,
			method=method,
			instruction=args.get("instruction") or "",
			project_context=_project_context(ctx),
		)
	except (ValueError, RuntimeError) as e:
		return _("Re-parse failed: {0}").format(str(e))
	return _("Re-parsed page {0} via {1} (composite {2}, {3} chars). Canonical updated.").format(
		res["page_no"], res["method"], res["composite"], res["chars"]
	) + _propagate_page(source_document, page_no)


def _reparse_document(ctx: Ctx, args: dict) -> str:
	import_name = ctx.default_import(args.get("source_document"))
	if not import_name:
		return _("No document specified. Open a document or pass `source_document`.")
	instruction = (args.get("instruction") or "").strip()
	frappe.enqueue(
		"wikify.jobs.remediate.run",
		queue="long",
		timeout=3600,
		import_name=import_name,
		scope="all",
		instruction=instruction,
	)
	hint = f" steered by: {instruction}" if instruction else ""
	return _(
		"Enqueued a document-wide re-parse of {0}{1}. Progress streams on the import; the "
		"tree and pages refresh when it finishes."
	).format(import_name, hint)


TOOLS = [
	Tool(
		name="use_page_image",
		side="server",
		# nosemgrep
		description=(
			"Deterministically embed a page's rendered photo (no LLM, no cropping). Pass "
			"`section` to add the full page photo to the END of that section; the page's own "
			"text is kept — use this when the user asks to add/show page N as an image in a "
			"section. Pass `caption` instead to replace ONLY that one existing `![caption](...)` "
			"image tag on the page with the full page photo, leaving the rest of the page's "
			"content untouched — use this when a figure/screenshot placeholder needs a real image "
			"and precise cropping isn't reliable; the user can crop the exact figure out of the "
			"full page photo themselves later, in the wiki. `caption` must exactly match the alt "
			"text of an existing tag on that page, as seen via read_page. Pass exactly one of "
			"`section` or `caption`. Defaults to the attached document."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
				"page_no": {"type": "integer", "description": "1-based page number."},
				"section": {
					"type": "string",
					"description": "Source Section id to append the page image to.",
				},
				"caption": {
					"type": "string",
					"description": "Exact alt text of the existing image tag on the page to replace.",
				},
			},
			"required": ["page_no"],
		},
		handler=_use_page_image,
		mutates=True,
	),
	Tool(
		name="reparse_page",
		side="server",
		# nosemgrep
		description=(
			"Re-parse a single page, steered by a plain-English instruction (e.g. 'keep the "
			"table as a real markdown table', 'don't make this a mermaid diagram'). method "
			"forces 'cleanup' (text) or 'vlm' (from the image); omit for vlm (the default). The result "
			"becomes the page's canonical markdown. Defaults to the attached document."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
				"page_no": {"type": "integer", "description": "1-based page number."},
				"method": {
					"type": "string",
					"enum": ["cleanup", "vlm"],
					"description": "Force a method; omit for vlm (the default).",
				},
				"instruction": {"type": "string", "description": "Plain-English steering for the re-parse."},
			},
			"required": ["page_no"],
		},
		handler=_reparse_page,
		mutates=True,
	),
	Tool(
		name="reparse_document",
		side="server",
		# nosemgrep
		description=(
			"Re-parse the WHOLE document, steered by a plain-English instruction. Expensive — "
			"this runs cleanup/VLM over every page and rebuilds the tree, so MANUAL TREE EDITS "
			"AND SECTION-LEVEL CONTENT EDITS ARE LOST. The user must confirm before it runs."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
				"instruction": {"type": "string", "description": "Plain-English steering for the re-parse."},
			},
		},
		handler=_reparse_document,
		confirm=True,
	),
]
