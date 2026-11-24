"""Apply a review suggestion. Each action is a thin call into an existing engine path."""

from __future__ import annotations

import frappe
from frappe import _

from wikify.engine import store
from wikify.engine.reparse import recompute_canonical_mean
from wikify.engine.review.suggestions import LINT_FIX_FUNCTIONS, USE_LABELS, alternative, suggestions_for
from wikify.engine.sectionize import propagate_page, rebuild_section_markdown

LINT_OUTCOMES = {
	"fix_separators": "Added the missing table header lines",
	"close_fence": "Closed the code block",
	"pad_rows": "Filled short table rows",
}


def apply(queue, issue: dict, suggestion_id: str, value: str = "") -> dict:
	"""Run one suggestion on a fresh `issue` from `queue`. Returns `{outcome, follow_up}`."""
	offered = {s["id"] for s in suggestions_for(issue, queue.row(issue), queue.taxonomy)}
	if suggestion_id not in offered:
		frappe.throw(_("That fix is not offered for this item any more. Reload to see the latest."))
	action = ACTIONS[suggestion_id]
	return action(queue, issue, value)


def use_candidate(queue, issue: dict, value: str) -> dict:
	page = queue.row(issue)
	candidate = alternative(page)
	store.set_canonical(page.name, candidate["markdown"], candidate["composite"], candidate["source"])
	store.set_remediation_adopted(page.name, candidate["source"] != "baseline")
	recompute_canonical_mean(queue.source_document)
	outcome = f"{USE_LABELS[candidate['source']]}, score {score_text(page.canonical_composite)} to {score_text(candidate['composite'])}"
	return {"outcome": outcome, **propagate(queue, page)}


def page_image(queue, issue: dict, value: str) -> dict:
	page = queue.row(issue)
	tag = f"![Page {page.page_no}]({page.image})"
	store.set_canonical_markdown(
		page.name, f"{(page.canonical_markdown or '').rstrip()}\n\n{tag}".lstrip(), keep_audit=True
	)
	return {"outcome": "Added the page as an image", **propagate(queue, page)}


def lint_fix(suggestion_id: str):
	def run(queue, issue: dict, value: str) -> dict:
		section = queue.row(issue)
		store.set_section_markdown(section.name, LINT_FIX_FUNCTIONS[suggestion_id](section.markdown or ""))
		return {"outcome": LINT_OUTCOMES[suggestion_id], "follow_up": []}

	return run


def rebuild_from_pages(queue, issue: dict, value: str) -> dict:
	rebuild_section_markdown(issue["target"]["name"])
	return {"outcome": "Rebuilt the section from its pages", "follow_up": []}


def set_type(queue, issue: dict, value: str) -> dict:
	if value not in queue.taxonomy:
		frappe.throw(_("Choose one of the section types."))
	store.set_section_type(issue["target"]["name"], value)
	return {"outcome": f"Set the type to {value}", "follow_up": []}


def retry_remediation(queue, issue: dict, value: str) -> dict:
	from wikify.api.imports import trigger_remediation

	trigger_remediation(queue.import_name, scope="failed")
	return {"outcome": "Queued the vision pass again on the failed pages", "follow_up": []}


def reclassify(queue, issue: dict, value: str) -> dict:
	from wikify.api.imports import reclassify as start_reclassify

	start_reclassify(queue.import_name)
	return {"outcome": "Queued classification for every section", "follow_up": []}


def propagate(queue, page: dict) -> dict:
	"""Push the page change into its section. `follow_up` names sections a person should
	check: the owners of a shared page (nothing rebuilt), or the neighbours of a rebuilt
	section that share one of its pages (their text may now repeat)."""
	result = propagate_page(queue.source_document, page.page_no)
	if result["rebuilt"]:
		return {"follow_up": result["rebuilt"]["overlaps"], "follow_up_kind": "overlap"}
	if len(result["owners"]) > 1:
		owners = [{"name": o.name, "title": o.title} for o in result["owners"]]
		return {"follow_up": owners, "follow_up_kind": "shared_page"}
	return {"follow_up": []}


def score_text(value: float | None) -> str:
	return "none" if value is None else f"{value:.2f}"


BACKGROUND = {"retry_remediation", "reclassify"}

ACTIONS = {
	"use_candidate": use_candidate,
	"page_image": page_image,
	**{fix_id: lint_fix(fix_id) for fix_id in LINT_FIX_FUNCTIONS},
	"rebuild_from_pages": rebuild_from_pages,
	"set_type": set_type,
	"retry_remediation": retry_remediation,
	"reclassify": reclassify,
}
