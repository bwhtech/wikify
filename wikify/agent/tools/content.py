"""Content tools (0.3 Slices 17-19) — edit the layer the user actually sees.

The content chain is `Source Page.canonical_markdown → Source Section.markdown →
Wiki Document.content`; the wiki preview renders the middle layer. These tools write
and propagate along it:

  - `edit_section_content` — surgical write to `Source Section.markdown` (whole-body
    replace or an exact, unique find/replace). The primary "fix what the preview shows".
  - `edit_page_content` — the same surgical write to `Source Page.canonical_markdown`,
    the layer Page Review renders. Downstream section/wiki stay stale until propagated.
  - `rebuild_section_from_pages` — re-derive one section's markdown from its pages'
    canonical (after a page re-parse), tree untouched.
  - `sync_wiki_page` — push one section's content into its existing generated
    `Wiki Document` (content-only; structure stays owned by `regenerate_wiki`).

Every result string states which layers are now current and which are stale — no
silent half-fixes.
"""

from __future__ import annotations

import frappe
from frappe import _

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool
from wikify.engine import store


def _section_row(name: str):
	return frappe.db.get_value(
		"Source Section",
		name,
		["title", "source_document", "markdown", "wiki_document", "include_in_wiki"],
		as_dict=True,
	)


def _wiki_hint(sec) -> str:
	"""The layer-status tail every content edit reports."""
	if sec.wiki_document and frappe.db.exists("Wiki Document", sec.wiki_document):
		return _(
			"The wiki preview shows this immediately; the GENERATED wiki page is now stale — "
			"call sync_wiki_page to update it."
		)
	return _("The wiki preview shows this immediately (no wiki generated yet for this section).")


def _apply_edit(old: str, args: dict) -> tuple[str | None, str | None]:
	"""Shared edit contract for section/page content: (new_body, None) or (None, error)."""
	mode = args.get("mode") or ("find_replace" if args.get("find") is not None else "replace")

	if mode == "find_replace":
		find, replace = args.get("find") or "", args.get("replace")
		if not find:
			return None, _("Provide the exact `find` text (and `replace`) for find_replace mode.")
		if replace is None:
			return None, _("Provide `replace` (may be empty to delete the matched text).")
		count = old.count(find)
		if count != 1:
			return None, _(
				"find_replace needs exactly ONE occurrence; found {0}. Nothing was changed. "
				"Include more surrounding context in `find` to make it unique."
			).format(count)
		return old.replace(find, replace, 1), None
	if mode == "replace":
		content = args.get("content")
		if content is None:
			return None, _("Provide `content` (the full new markdown body) for replace mode.")
		return content, None
	return None, _("`mode` must be 'replace' or 'find_replace'.")


def _edit_section_content(ctx: Ctx, args: dict) -> str:
	name = args.get("name")
	if not name:
		return _("Provide the section `name` (the id shown in <angle brackets> in the tree).")
	sec = _section_row(name)
	if not sec:
		return _("Section {0} not found.").format(name)

	old = sec.markdown or ""
	new, error = _apply_edit(old, args)
	if error:
		return error

	store.set_section_markdown(name, new, update_modified=False)
	return _("Updated '{0}' ({1} → {2} chars). {3}").format(sec.title, len(old), len(new), _wiki_hint(sec))


def _edit_page_content(ctx: Ctx, args: dict) -> str:
	from wikify.engine.sectionize import sections_covering_page

	source_document = ctx.default_document(args.get("source_document"))
	page_no = args.get("page_no")
	if not source_document:
		return _("No document specified. Open a document or pass `source_document`.")
	if page_no is None:
		return _("Provide the `page_no` to edit.")
	page_no = int(page_no)
	row = frappe.db.get_value(
		"Source Page",
		{"source_document": source_document, "page_no": page_no},
		["name", "canonical_markdown", "baseline_markdown"],
		as_dict=True,
	)
	if not row:
		return _("Page {0} of {1} not found.").format(page_no, source_document)

	# Edit what Page Review (and read_page) actually shows: canonical, else baseline.
	old = row.canonical_markdown or row.baseline_markdown or ""
	new, error = _apply_edit(old, args)
	if error:
		return error

	store.set_canonical_markdown(row.name, new)
	owners = ", ".join(
		f"'{s['title']}' <{s['name']}>" for s in sections_covering_page(source_document, page_no)
	)
	msg = _(
		"Updated page {0} canonical markdown ({1} → {2} chars). Page Review shows this immediately."
	).format(page_no, len(old), len(new))
	if owners:
		msg += " " + _(
			"Downstream is now STALE: section(s) {0} were built from this page earlier. "
			"Apply the same fix there with edit_section_content (surgical, keeps section-level "
			"edits), or rebuild_section_from_pages to re-derive the whole body; then "
			"sync_wiki_page if a wiki was generated."
		).format(owners)
	return msg


def _rebuild_section_from_pages(ctx: Ctx, args: dict) -> str:
	from wikify.engine.sectionize import rebuild_section_markdown

	name = args.get("name")
	if not name:
		return _("Provide the section `name`.")
	try:
		res = rebuild_section_markdown(name)
	except ValueError as e:
		return _("Couldn't rebuild: {0}").format(str(e))

	sec = _section_row(name)
	msg = _("Rebuilt '{0}' from pages {1}-{2} canonical markdown ({3} chars). {4}").format(
		res["title"], res["pages"][0], res["pages"][1], res["chars"], _wiki_hint(sec)
	)
	if res["overlaps"]:
		neighbors = ", ".join(f"'{o['title']}' <{o['name']}>" for o in res["overlaps"])
		msg += " " + _(
			"WARNING: boundary page(s) are shared with {0} — the whole page range was adopted, "
			"so this section may now include content belonging to them. Verify with "
			"read_rendered_preview and use edit_section_content to trim if needed."
		).format(neighbors)
	return msg


def _sync_wiki_page(ctx: Ctx, args: dict) -> str:
	from wikify.engine.generate import sync_section

	name = args.get("name")
	if not name:
		return _("Provide the section `name`.")
	if not frappe.db.exists("Source Section", name):
		return _("Section {0} not found.").format(name)
	res = sync_section(name)
	title = frappe.db.get_value("Source Section", name, "title")
	if res.get("synced"):
		return _(
			"Synced '{0}' to its generated wiki page ({1} chars, {2} page-ref link(s), route /{3}). "
			"Both the preview and the live wiki now show the current content."
		).format(title, res["chars"], res["links"], res.get("route") or "")
	reason = res.get("reason")
	if reason == "no_wiki_document":
		return _(
			"'{0}' has no generated wiki page yet — nothing to sync. If the document's wiki "
			"was generated, run regenerate_wiki to project this section into it."
		).format(title)
	if reason == "excluded":
		return _("'{0}' is excluded from the wiki (include_in_wiki off) — nothing to sync.").format(title)
	return _(
		"'{0}' needs a full regenerate_wiki: an included child has no wiki page yet "
		"(structural change only full generation can project)."
	).format(title)


TOOLS = [
	Tool(
		name="edit_section_content",
		side="server",
		description=(
			"Edit a section's markdown directly — the content the wiki preview and wiki page "
			"render. Use this to fix broken tables, typos, or stray content the user sees. "
			"mode 'replace' swaps the whole body with `content`; mode 'find_replace' swaps ONE "
			"exact, unique occurrence of `find` with `replace` (fails with the match count if "
			"0 or >1 — make `find` unique)."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
				"mode": {"type": "string", "enum": ["replace", "find_replace"]},
				"content": {"type": "string", "description": "Full new markdown (replace mode)."},
				"find": {"type": "string", "description": "Exact text to find (find_replace mode)."},
				"replace": {"type": "string", "description": "Replacement text (find_replace mode)."},
			},
			"required": ["name"],
		},
		handler=_edit_section_content,
		mutates=True,
	),
	Tool(
		name="edit_page_content",
		side="server",
		description=(
			"Edit a page's canonical markdown directly — the content Page Review renders. "
			"Use this when the user points at something wrong on a PAGE (page context / Page "
			"Review); it does NOT update the owning section or wiki — the result names them "
			"so you can propagate. Same modes as edit_section_content: 'replace' swaps the "
			"whole body with `content`; 'find_replace' swaps ONE exact, unique occurrence."
		),
		parameters={
			"type": "object",
			"properties": {
				"source_document": {
					"type": "string",
					"description": "Source Document id (defaults to the attached document).",
				},
				"page_no": {"type": "integer", "description": "Page number to edit."},
				"mode": {"type": "string", "enum": ["replace", "find_replace"]},
				"content": {"type": "string", "description": "Full new markdown (replace mode)."},
				"find": {"type": "string", "description": "Exact text to find (find_replace mode)."},
				"replace": {"type": "string", "description": "Replacement text (find_replace mode)."},
			},
			"required": ["page_no"],
		},
		handler=_edit_page_content,
		mutates=True,
	),
	Tool(
		name="rebuild_section_from_pages",
		side="server",
		description=(
			"Re-derive a section's markdown from its pages' canonical markdown (page_start-"
			"page_end), without touching the tree. Use after re-parsing a page so the fix "
			"reaches the wiki preview. Warns when boundary pages are shared with neighboring "
			"sections."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
			},
			"required": ["name"],
		},
		handler=_rebuild_section_from_pages,
		mutates=True,
	),
	Tool(
		name="sync_wiki_page",
		side="server",
		description=(
			"Push one section's current content into its existing generated wiki page "
			"(content-only, cheap, idempotent). Use after fixing a section on a document whose "
			"wiki was already generated. Structural changes (new/renamed/moved pages) need "
			"regenerate_wiki instead."
		),
		parameters={
			"type": "object",
			"properties": {
				"name": {"type": "string", "description": "Source Section id."},
			},
			"required": ["name"],
		},
		handler=_sync_wiki_page,
		mutates=True,
	),
]
