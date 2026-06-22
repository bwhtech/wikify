"""Re-parse tools (0.2 Slice 14) — the headline "fix this page from plain English".

  - `use_page_image` — deterministic, no LLM: replace a page's canonical markdown with an
    embed of its rendered image. The literal "just paste the image of the PDF page".
  - `reparse_page` — single-page cleanup/VLM re-parse steered by a plain-English
    `instruction`, adopted as that page's canonical. Runs inline (page-scoped, fast).
  - `reparse_document` — document-wide instruction-steered re-parse. **Expensive →
    confirm-gated**: the loop holds it for a UI confirm card and enqueues the remediate
    job only once the user approves.

Page-scoped edits update the page's canonical (shown in the Page Review immediately)
without a full-tree rebuild, so manual tree structure is preserved; the user runs
reclassify / a document re-parse to propagate canonical into the section tree + wiki.
"""

from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool


def _pdf_path(source_document: str) -> str | None:
	"""Full path to the document's source PDF (via its Import's attached File)."""
	pdf_url = frappe.db.get_value("Wikify Import", {"source_document": source_document}, "pdf")
	if not pdf_url:
		return None
	file_name = frappe.db.get_value("File", {"file_url": pdf_url}, "name")
	return frappe.get_doc("File", file_name).get_full_path() if file_name else None


def _project_context(ctx: Ctx) -> str:
	if not ctx.project:
		return ""
	return frappe.db.get_value("Wikify Project", ctx.project, "context_prompt") or ""


def _page_no(args: dict) -> int | None:
	raw = args.get("page_no")
	try:
		return int(raw) if raw is not None else None
	except (TypeError, ValueError):
		return None


def _use_page_image(ctx: Ctx, args: dict) -> str:
	from wikify.engine import embed_page_image

	source_document = ctx.default_document(args.get("source_document"))
	page_no = _page_no(args)
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if page_no is None:
		return _("Provide the `page_no` to embed as an image.")
	try:
		embed_page_image(source_document, page_no)
	except (ValueError, RuntimeError) as e:
		return _("Couldn't embed the page image: {0}").format(str(e))
	return _("Page {0} now embeds its rendered image as canonical markdown (no re-parse).").format(page_no)


def _reparse_page(ctx: Ctx, args: dict) -> str:
	from wikify.engine import reparse_page

	source_document = ctx.default_document(args.get("source_document"))
	page_no = _page_no(args)
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if page_no is None:
		return _("Provide the `page_no` to re-parse.")
	pdf_path = _pdf_path(source_document)
	if not pdf_path:
		return _("Couldn't locate the source PDF for {0}.").format(source_document)
	method = args.get("method")
	if method not in (None, "cleanup", "vlm"):
		return _("`method` must be 'cleanup' or 'vlm' (or omit to auto-route).")
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
	)


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
		description=(
			"Deterministically replace a page's canonical markdown with an embed of its "
			"rendered image (no LLM). Use when the user wants the page shown as an image rather "
			"than transcribed/diagrammed. Defaults to the attached document."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {"type": "string", "description": "Omit to use the attached document."},
				"page_no": {"type": "integer", "description": "1-based page number."},
			},
			"required": ["page_no"],
		},
		handler=_use_page_image,
		mutates=True,
	),
	Tool(
		name="reparse_page",
		side="server",
		description=(
			"Re-parse a single page, steered by a plain-English instruction (e.g. 'keep the "
			"table as a real markdown table', 'don't make this a mermaid diagram'). method "
			"forces 'cleanup' (text) or 'vlm' (from the image); omit to auto-route. The result "
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
					"description": "Force a method; omit to auto-route.",
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
		description=(
			"Re-parse the WHOLE document, steered by a plain-English instruction. Expensive — "
			"this runs cleanup/VLM over every page and rebuilds the tree. The user must confirm "
			"before it runs."
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
