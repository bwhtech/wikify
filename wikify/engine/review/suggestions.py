"""The fixes offered for each issue, with their previews. Each one maps onto an existing
engine path; `apply` lives in `review.actions`."""

from __future__ import annotations

from wikify.engine.lint import close_fences, fix_table_separators, lint_markdown, pad_ragged_rows

USE_LABELS = {
	"vlm": "Use the vision model version",
	"cleanup": "Use the cleaned-up version",
	"baseline": "Use the first parse",
}


def suggestions_for(issue: dict, row: dict | None, taxonomy: list[str]) -> list[dict]:
	builder = {
		"page_low_score": page_suggestions,
		"page_vision_failed": page_suggestions,
		"page_image_only": page_suggestions,
		"section_lint": lint_suggestions,
		"section_other_type": lambda issue, row: type_suggestions(taxonomy),
		"doc_vision_failed": lambda issue, row: [retry_remediation()],
		"doc_unclassified": lambda issue, row: [reclassify()],
	}[issue["code"]]
	found = builder(issue, row)
	if found and not any(s["recommended"] for s in found):
		found[0]["recommended"] = True
	return found


def page_suggestions(issue: dict, page: dict) -> list[dict]:
	found = []
	if candidate := alternative(page):
		found.append(use_candidate(page, candidate))
	if page.image and page.image not in (page.canonical_markdown or ""):
		found.append(page_image(page))
	return found


# (lint code, suggestion id, label, detail, safe, fix)
LINT_FIXES = [
	(
		"missing_separator",
		"fix_separators",
		"Add the missing table header lines",
		"Inserts one |---| row under each table header. Nothing else changes.",
		True,
		fix_table_separators,
	),
	(
		"unclosed_fence",
		"close_fence",
		"Close the code block at the end",
		"Adds the closing fence after the last line. Nothing else changes.",
		True,
		close_fences,
	),
	(
		"ragged_row",
		"pad_rows",
		"Fill short rows with empty cells",
		"Makes the table render, but a cell can still sit in the wrong column. Check the preview.",
		False,
		pad_ragged_rows,
	),
]
LINT_FIX_FUNCTIONS = {fix_id: fix for _, fix_id, _, _, _, fix in LINT_FIXES}


def lint_suggestions(issue: dict, section: dict) -> list[dict]:
	codes = {i["code"] for i in issue["lint"]}
	markdown = section.markdown or ""
	found = [
		suggestion(fix_id, label, detail, safe=safe, recommended=safe, preview=fix(markdown))
		for code, fix_id, label, detail, safe, fix in LINT_FIXES
		if code in codes
	]
	if rebuilt := rebuilt_markdown(section):
		found.append(
			suggestion(
				"rebuild_from_pages",
				"Rebuild this section from its pages",
				"Re-reads the section from the page text it came from. Edits made to this section are replaced.",
				recommended=not any(s["safe"] for s in found),
				preview=rebuilt,
			)
		)
	return found


def rebuilt_markdown(section: dict) -> str | None:
	"""The section rebuilt from its pages, when that is lint-clean and actually different.
	Skipped when a neighbour shares a page: whole-page adoption would copy its text in."""
	from wikify.engine.sectionize import overlapping_sections, rebuild_section_markdown

	if not (section.page_start and section.page_end) or overlapping_sections(section.name):
		return None
	markdown = rebuild_section_markdown(section.name, dry_run=True)["markdown"]
	if markdown.strip() == (section.markdown or "").strip() or lint_markdown(markdown):
		return None
	return markdown


def type_suggestions(taxonomy: list[str]) -> list[dict]:
	return [
		suggestion(
			"set_type",
			"Pick a section type",
			"Sets the type used by Explore and the wiki templates.",
			options=taxonomy,
		)
	]


def retry_remediation() -> dict:
	return suggestion(
		"retry_remediation",
		"Retry the vision pass on the failed pages",
		"Runs remediation again on only these pages. Progress shows on the import.",
		recommended=True,
	)


def reclassify() -> dict:
	return suggestion(
		"reclassify",
		"Classify all sections",
		"Runs the classifier over every section. Progress shows on the import.",
		recommended=True,
	)


def use_candidate(page: dict, candidate: dict) -> dict:
	current = page.canonical_composite
	better = candidate["composite"] is not None and (current is None or candidate["composite"] > current)
	detail = f"Scores {candidate['composite']:.2f}" if candidate["composite"] is not None else "Not scored"
	if current is not None:
		detail += f". The version in use scores {current:.2f}."
	return suggestion(
		"use_candidate",
		USE_LABELS.get(candidate["source"], "Use the other version"),
		detail,
		recommended=better,
		confidence=candidate["composite"],
		preview=candidate["markdown"],
	)


def page_image(page: dict) -> dict:
	return suggestion(
		"page_image",
		"Add the page as an image",
		"Keeps the text and adds a picture of the whole page under it, so nothing on the page is lost.",
		preview=f"{(page.canonical_markdown or '').rstrip()}\n\n![Page {page.page_no}]({page.image})".strip(),
	)


def alternative(page: dict) -> dict | None:
	"""The stored version of the page that is not the one in use, if it has content."""
	if page.canonical_source in (None, "", "baseline"):
		markdown, composite, source = (
			page.remediation_markdown,
			page.remediation_composite,
			page.remediation_method,
		)
	else:
		markdown, composite, source = page.baseline_markdown, page.composite, "baseline"
	if not (markdown or "").strip() or markdown.strip() == (page.canonical_markdown or "").strip():
		return None
	return {"markdown": markdown, "composite": composite, "source": source}


def suggestion(
	id: str,
	label: str,
	detail: str,
	*,
	safe: bool = False,
	recommended: bool = False,
	confidence: float | None = None,
	preview: str | None = None,
	options: list[str] | None = None,
) -> dict:
	return {
		"id": id,
		"label": label,
		"detail": detail,
		"safe": safe,
		"recommended": recommended,
		"confidence": confidence,
		"preview": preview,
		"options": options,
	}
