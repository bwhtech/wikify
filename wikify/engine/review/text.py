"""The plain-language copy of the review queue: one place, so wording stays consistent."""

from __future__ import annotations

# (note substring or lint code, title, reason). The first match names the issue; every
# match adds its reason.
REASONS = [
	(
		"low text recall",
		"Some text on this page is missing",
		"The page has more text than we extracted, so part of it would not reach the wiki.",
	),
	(
		"table present but not reproduced",
		"A table on this page did not come through",
		"The page has a table, but the extracted text has no table.",
	),
	(
		"high extra ratio",
		"This page has text that is not on the PDF",
		"We extracted text that does not appear on the page. It may be a repeated header or made-up text.",
	),
	(
		"flattened <br> blob",
		"The layout of this page was flattened",
		"Lines were merged into one block, so lists and tables lost their shape.",
	),
	(
		"picture-text wrapper",
		"Text inside a picture was mixed into the page",
		"Text read from an image was dropped into the page as raw text.",
	),
	(
		"picture omitted",
		"A picture on this page was left out",
		"The parser skipped an image on this page.",
	),
	(
		"every candidate scored below",
		"We could not get a good read of this page",
		"We tried several ways to read this page, and none of them scored well.",
	),
	(
		"vlm failed",
		"The vision model could not read this page",
		"The call to the vision model failed, so the page uses the first parse.",
	),
	(
		"missing_separator",
		"A table lost its header line",
		"The wiki will show this table as plain text with pipe characters.",
	),
	(
		"ragged_row",
		"Table rows have the wrong number of cells",
		"Some rows have more or fewer cells than the header, so columns do not line up.",
	),
	(
		"lone_pipe_row",
		"A table row is cut off from its table",
		"One table row is on its own, so the wiki will show it as text with pipes.",
	),
	(
		"unclosed_fence",
		"A code block is never closed",
		"Everything after it on the wiki page would show as code.",
	),
]

DEFAULT_TITLES = {
	"page_low_score": (
		"This page scored low",
		"The extracted text does not match the page well enough to publish without a check.",
	),
	"page_image_only": (
		"This page became a picture only",
		"We kept the page as an image. Text inside the picture is not searchable.",
	),
	"section_other_type": (
		"This section has no type",
		"The classifier could not place this section in any type, so it will not show up when you filter by type.",
	),
}

QUESTIONS = {
	"page_low_score": "Which version of this page should go to the wiki?",
	"page_vision_failed": "Which version of this page should go to the wiki?",
	"page_image_only": "Is the picture enough, or do you need the text from it?",
	"section_lint": "How should we fix this section?",
	"section_other_type": "What kind of section is this?",
	"doc_vision_failed": "Try the vision pass again on these pages?",
	"doc_unclassified": "Classify the sections now?",
}

SOURCE_LABELS = {
	"baseline": "First parse",
	"cleanup": "Cleaned-up version",
	"vlm": "Vision model version",
	"image": "Page image",
}


def match_reasons(texts: list[str]) -> list[tuple[str, str]]:
	"""(title, reason) for every REASONS entry found in any of `texts`, in table order."""
	haystack = "\n".join(t for t in texts if t)
	return [(title, reason) for needle, title, reason in REASONS if needle in haystack]
