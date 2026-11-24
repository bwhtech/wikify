from __future__ import annotations

import frappe
from frappe import _

from wikify.api.permission import assert_readable
from wikify.api.sections import assert_not_published
from wikify.engine import store
from wikify.engine.review import ReviewQueue, actions


@frappe.whitelist()
def get_review(import_name: str) -> dict:
	imp = _get_import(import_name, "read")
	return {**ReviewQueue(imp.source_document).overview(), "read_only": imp.status == "Completed"}


@frappe.whitelist()
def get_issue(import_name: str, issue_id: str) -> dict:
	imp = _get_import(import_name, "read")
	detail = ReviewQueue(imp.source_document).detail(issue_id)
	if not detail:
		frappe.throw(_("This item no longer exists. It may have been fixed."), frappe.DoesNotExistError)
	return {**detail, "read_only": imp.status == "Completed"}


@frappe.whitelist(methods=["POST"])
def accept(import_name: str, issue_id: str) -> dict:
	imp = _get_import(import_name, "write")
	found = _find(ReviewQueue(imp.source_document), issue_id)
	store.add_review_decision(imp.source_document, found, "accepted")
	return {"ok": True}


@frappe.whitelist(methods=["POST"])
def apply(import_name: str, issue_id: str, suggestion: str, value: str = "") -> dict:
	imp = _get_import(import_name, "write")
	queue = ReviewQueue(imp.source_document, import_name)
	found = _find(queue, issue_id)
	result = actions.apply(queue, found, suggestion, value)
	_record_applied(imp.source_document, found, suggestion, result["outcome"])
	return {"follow_up": result["follow_up"], "follow_up_kind": result.get("follow_up_kind")}


@frappe.whitelist(methods=["POST"])
def apply_safe(import_name: str) -> dict:
	imp = _get_import(import_name, "write")
	queue = ReviewQueue(imp.source_document, import_name)
	pairs = queue.safe_issues()
	for found, suggestion in pairs:
		result = actions.apply(queue, found, suggestion["id"])
		_record_applied(imp.source_document, found, suggestion["id"], result["outcome"])
	return {"applied": len(pairs)}


@frappe.whitelist(methods=["POST"])
def reopen(import_name: str, issue_id: str) -> dict:
	imp = _get_import(import_name, "write")
	store.reopen_decisions(imp.source_document, issue_id)
	return {"ok": True}


def _get_import(import_name: str, ptype: str):
	imp = frappe.get_doc("Wikify Import", import_name)
	imp.check_permission(ptype)
	assert_readable(imp.project, imp.source_document)
	if not imp.source_document:
		frappe.throw(_("Nothing to review yet. The import has not produced a document."))
	if ptype == "write":
		assert_not_published(imp.status)
	return imp


def _record_applied(source_document: str, found: dict, suggestion: str, outcome: str) -> None:
	"""Keep the fingerprint of the content after the fix, so a fix that leaves the signal in
	place (a page image does not raise the score) still closes the item. Background jobs
	have not run yet, so their items stay open until the signal itself goes."""
	after = ReviewQueue(source_document).find(found["id"])
	fingerprint = after["fingerprint"] if after and suggestion not in actions.BACKGROUND else ""
	store.add_review_decision(
		source_document,
		{**found, "fingerprint": fingerprint},
		"applied",
		suggestion=suggestion,
		outcome=outcome,
	)


def _find(queue: ReviewQueue, issue_id: str) -> dict:
	found = queue.find(issue_id)
	if not found:
		frappe.throw(_("This item changed. Reload to see the latest."))
	return found
