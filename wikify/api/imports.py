from __future__ import annotations

import frappe
from frappe import _
from frappe.utils.background_jobs import is_job_enqueued

from wikify.engine import preview_wiki as _preview_wiki
from wikify.jobs import generate as generate_job
from wikify.jobs._util import log, publish_progress
from wikify.seed import seed_uncategorized_project

MAX_BATCH = 25


def assert_readable_file(file_url: str) -> None:
	names = frappe.get_all("File", filters={"file_url": file_url}, pluck="name")
	if not any(frappe.has_permission("File", ptype="read", doc=name) for name in names):
		frappe.throw(_("You are not allowed to import {0}.").format(file_url), frappe.PermissionError)


def _create_import(pdf_file_url: str, title: str, project: str) -> str:
	imp = frappe.new_doc("Wikify Import")
	imp.import_title = title or pdf_file_url.rsplit("/", 1)[-1].removesuffix(".pdf")
	imp.pdf = pdf_file_url
	imp.project = project
	imp.status = "Queued"
	imp.insert()

	frappe.enqueue(
		"wikify.jobs.parse.run",
		queue="long",
		timeout=3600,
		import_name=imp.name,
	)
	return imp.name


@frappe.whitelist(methods=["POST"])
def start_import(pdf_file_url: str, title: str, project: str | None = None) -> str:
	assert_readable_file(pdf_file_url)
	return _create_import(pdf_file_url, title, project or seed_uncategorized_project())


@frappe.whitelist(methods=["POST"])
def start_imports(files: list[dict] | str, project: str | None = None) -> list[str]:
	if isinstance(files, str):
		files = frappe.parse_json(files)
	if not files:
		frappe.throw(_("No files to import."))
	if len(files) > MAX_BATCH:
		frappe.throw(f"Import at most {MAX_BATCH} PDFs at a time (got {len(files)}).")

	if any(not f.get("file_url") for f in files):
		frappe.throw(_("Every file needs a file_url."))

	for uploaded_file in files:
		assert_readable_file(uploaded_file["file_url"])

	project = project or seed_uncategorized_project()
	return [_create_import(f["file_url"], f.get("title"), project) for f in files]


@frappe.whitelist(methods=["POST"])
def trigger_remediation(import_name: str, scope: str = "flagged") -> str:
	if scope not in ("flagged", "all"):
		frappe.throw(f"Invalid scope: {scope!r} (expected 'flagged' or 'all').")

	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw(_("Nothing to remediate — parse hasn't produced a document yet."))
	if imp.status != "Review":
		frappe.throw(f"Can only remediate from Review (current status: {imp.status}).")

	imp.db_set("status", "Remediating")
	frappe.enqueue(
		"wikify.jobs.remediate.run",
		queue="long",
		timeout=3600,
		import_name=import_name,
		scope=scope,
	)
	return import_name


@frappe.whitelist(methods=["POST"])
def reclassify(import_name: str) -> str:
	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw(_("Nothing to classify — parse hasn't produced a document yet."))

	frappe.enqueue(
		"wikify.jobs.classify.run",
		queue="long",
		timeout=1800,
		import_name=import_name,
	)
	return import_name


@frappe.whitelist()
def preview_wiki(import_name: str) -> dict:
	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw(_("Nothing to preview — parse hasn't produced a document yet."))
	preview = _preview_wiki(imp.source_document)
	preview["wiki_space"] = frappe.db.get_value("Source Document", imp.source_document, "wiki_space")
	return preview


@frappe.whitelist(methods=["POST"])
def generate_wiki(
	import_name: str,
	wiki_space: str | None = None,
	new_space: dict | str | None = None,
) -> str:
	imp = frappe.get_doc("Wikify Import", import_name)
	if not imp.source_document:
		frappe.throw(_("Nothing to generate — parse hasn't produced a document yet."))
	if imp.status not in ("Graphed", "Completed", "Stopped"):
		frappe.throw(
			f"Approve the section tree first — can only generate from Graphed, Completed or Stopped "
			f"(current status: {imp.status})."
		)
	if is_job_enqueued(generate_job.job_id(import_name)):
		frappe.throw(_("The previous wiki generation is still stopping. Try again in a moment."))
	if isinstance(new_space, str):
		new_space = frappe.parse_json(new_space)
	if not wiki_space and not new_space:
		frappe.throw(_("Choose an existing Wiki Space or provide a new one."))

	frappe.cache().delete_value(generate_job.stop_key(import_name))
	publish_progress(import_name, 0, "Queued for wiki generation", status="Generating Wiki")
	frappe.enqueue(
		"wikify.jobs.generate.run",
		queue="long",
		timeout=3600,
		job_id=generate_job.job_id(import_name),
		import_name=import_name,
		wiki_space=wiki_space,
		new_space=new_space,
	)
	return import_name


@frappe.whitelist(methods=["POST"])
def stop_wiki_generation(import_name: str) -> str:
	imp = frappe.get_doc("Wikify Import", import_name)
	imp.check_permission("write")
	if imp.status != "Generating Wiki":
		frappe.throw(_("Wiki generation isn't running (current status: {0}).").format(imp.status))

	frappe.cache().set_value(generate_job.stop_key(import_name), "1")
	publish_progress(import_name, imp.stage_progress, "Wiki generation stopped", status="Stopped")
	log(import_name, "info", "generate", "Wiki generation stopped")
	return import_name
