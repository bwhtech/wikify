"""Detection rules: turn the signals already on Source Page / Source Section rows into
review issues. Pure: takes rows, returns dicts, no frappe calls, no writes."""

from __future__ import annotations

import hashlib
import json
from textwrap import shorten

from wikify.engine.remediate import MIN_CANONICAL_CHARS, content_chars
from wikify.engine.review.text import DEFAULT_TITLES, QUESTIONS, SOURCE_LABELS, match_reasons

DOC_FAILURE_MIN_PAGES = 10
DOC_FAILURE_MIN_SHARE = 0.2
UNCLASSIFIED_MIN_SHARE = 0.8
UNCLASSIFIED_MIN_SECTIONS = 3
VISION_FAILED = "vlm failed:"
# Remediation errors worth surfacing even when the page kept its baseline.
FAILURE_MARKERS = ("vlm failed", "every candidate scored below")


def detect(
	source_document: str, pages: list[dict], sections: list[dict], escalate_threshold: float, other_type: str
) -> list[dict]:
	detector = Detector(source_document, pages, sections, escalate_threshold, other_type)
	return detector.issues()


def fingerprint(*parts: str | None) -> str:
	return hashlib.sha1("\x00".join(p or "" for p in parts).encode()).hexdigest()[:12]


class Detector:
	def __init__(self, source_document, pages, sections, escalate_threshold, other_type):
		self.source_document = source_document
		self.pages = pages
		self.sections = sections
		self.escalate_threshold = escalate_threshold
		self.other_type = other_type
		self.vision_failed = [p for p in pages if is_vision_failed(p)]
		self.doc_vision_failed = self._is_doc_wide(len(self.vision_failed))

	def issues(self) -> list[dict]:
		issues = self._document_issues()
		issues += [issue for page in self.pages if (issue := self._page_issue(page))]
		issues += [issue for section in self.sections if (issue := self._section_issue(section))]
		return issues

	def _document_issues(self) -> list[dict]:
		issues = []
		if self.doc_vision_failed:
			issues.append(self._doc_vision_failed_issue())
		if self._unclassified_sections() is not None:
			issues.append(self._doc_unclassified_issue())
		return issues

	def _page_issue(self, page: dict) -> dict | None:
		if score(page) is not None and score(page) < self.escalate_threshold:
			return self._low_score_issue(page)
		if not self.doc_vision_failed and is_vision_failed(page):
			return self._page_issue_base(page, "page_vision_failed", "review", "text")
		if page.kind == "visual" and content_chars(page.canonical_markdown) < MIN_CANONICAL_CHARS:
			return self._page_issue_base(page, "page_image_only", "review", "visuals")
		return None

	def _low_score_issue(self, page: dict) -> dict:
		notes = page_notes(page)
		severity = "blocks" if "low text recall" in notes else "review"
		category = "tables" if "table" in notes else "text"
		return self._page_issue_base(page, "page_low_score", severity, category)

	def _page_issue_base(self, page: dict, code: str, severity: str, category: str) -> dict:
		return issue(
			code=code,
			severity=severity,
			category=category,
			target={
				"doctype": "Source Page",
				"name": page.name,
				"page_no": page.page_no,
				"section": self._owner_title(page.page_no),
			},
			reasons=match_reasons([page_notes(page)]),
			signals=page_signals(page),
			pages=[page.page_no],
			fingerprint=fingerprint(page.canonical_markdown),
		)

	def _section_issue(self, section: dict) -> dict | None:
		lint = parse_lint(section.lint_issues)
		if lint:
			return self._lint_issue(section, lint)
		if self._needs_type(section) and self._unclassified_sections() is None:
			return self._section_issue_base(section, "section_other_type", "review", "structure", [])
		return None

	def _lint_issue(self, section: dict, lint: list[dict]) -> dict:
		codes = [i["code"] for i in lint]
		category = "formatting" if set(codes) == {"unclosed_fence"} else "tables"
		found = self._section_issue_base(section, "section_lint", "blocks", category, codes)
		found["lint"] = lint
		found["signals"] = [{"label": "Lint", "value": ", ".join(dict.fromkeys(codes)), "mono": True}]
		return found

	def _section_issue_base(self, section, code, severity, category, reason_texts) -> dict:
		return issue(
			code=code,
			severity=severity,
			category=category,
			target={
				"doctype": "Source Section",
				"name": section.name,
				"page_no": section.page_start,
				"page_end": section.page_end if section.page_end != section.page_start else None,
				"section": section.title,
			},
			reasons=match_reasons(reason_texts),
			signals=[{"label": "Type", "value": section.section_type or "none", "mono": True}],
			pages=page_range(section),
			fingerprint=fingerprint(section.markdown, section.section_type),
		)

	def _doc_vision_failed_issue(self) -> dict:
		first_error = first_line(self.vision_failed[0].remediation_notes, VISION_FAILED)
		count = len(self.vision_failed)
		return issue(
			code="doc_vision_failed",
			severity="review",
			category="document",
			target={"doctype": "Source Document", "name": self.source_document},
			reasons=[
				(
					f"The vision pass failed on {count} pages",
					f"Every one of these pages uses the first parse. The first error was: {first_error}",
				)
			],
			signals=[
				{"label": "Pages failed", "value": f"{count} of {len(self.pages)}"},
			],
			# The pages still render their first parse, so they stay off the page map.
			pages=[],
			fingerprint=fingerprint(*(p.name for p in self.vision_failed)),
		)

	def _doc_unclassified_issue(self) -> dict:
		untyped, total = self._unclassified_sections()
		return issue(
			code="doc_unclassified",
			severity="review",
			category="document",
			target={"doctype": "Source Document", "name": self.source_document},
			reasons=[
				(
					"Sections have no type yet",
					f"{untyped} of {total} sections have no type, so classification probably did not run.",
				)
			],
			signals=[{"label": "Untyped sections", "value": f"{untyped} of {total}"}],
			pages=[],
			fingerprint=fingerprint(str(untyped), str(total)),
		)

	def _unclassified_sections(self) -> tuple[int, int] | None:
		"""(untyped, total) when most sections are untyped, else None."""
		candidates = [s for s in self.sections if s.include_in_wiki and not s.is_group]
		untyped = sum(1 for s in candidates if self._needs_type(s))
		if untyped < UNCLASSIFIED_MIN_SECTIONS or untyped < UNCLASSIFIED_MIN_SHARE * len(candidates):
			return None
		return untyped, len(candidates)

	def _needs_type(self, section: dict) -> bool:
		return (
			bool(section.include_in_wiki)
			and not section.is_group
			and section.section_type in (None, "", self.other_type)
		)

	def _is_doc_wide(self, count: int) -> bool:
		return count >= DOC_FAILURE_MIN_PAGES or (
			count > 0 and count >= DOC_FAILURE_MIN_SHARE * len(self.pages)
		)

	def _owner_title(self, page_no: int) -> str | None:
		covering = [s for s in self.sections if (s.page_start or 0) <= page_no <= (s.page_end or 0)]
		if not covering:
			return None
		return min(reversed(covering), key=lambda s: s.page_end - s.page_start).title


def issue(*, code, severity, category, target, reasons, signals, pages, fingerprint) -> dict:
	title, reason = DEFAULT_TITLES.get(code, ("", ""))
	if reasons:
		title = reasons[0][0]
		reason = " ".join(r for _, r in reasons)
	return {
		"id": f"{code}:{target['name']}",
		"code": code,
		"category": category,
		"severity": severity,
		"target": target,
		"title": title,
		"reason": reason,
		"question": QUESTIONS[code],
		"signals": signals,
		"pages": pages,
		"fingerprint": fingerprint,
	}


def score(page: dict) -> float | None:
	return page.canonical_composite if page.canonical_composite is not None else page.composite


def uses_baseline(page: dict) -> bool:
	return page.canonical_source in (None, "", "baseline")


def page_notes(page: dict) -> str:
	"""Notes of the markdown that will be published, plus remediation failures."""
	if not uses_baseline(page):
		return page.remediation_notes or ""
	failures = [n for n in (page.remediation_notes or "").split("; ") if n.startswith(FAILURE_MARKERS)]
	return "; ".join([page.notes or "", *failures])


def is_vision_failed(page: dict) -> bool:
	return VISION_FAILED in (page.remediation_notes or "") and uses_baseline(page)


def page_signals(page: dict) -> list[dict]:
	signals = [
		{"label": "Overall score", "value": f"{score(page):.2f}" if score(page) is not None else "none"},
		{"label": "Used version", "value": SOURCE_LABELS.get(page.canonical_source or "baseline", "")},
	]
	if not uses_baseline(page):
		return signals
	metrics = [
		("Text match", page.text_recall, "{:.0%}"),
		("Extra text", page.extra_ratio, "{:.0%}"),
		("Table score", page.table_score, "{:.2f}"),
		("Judge score", page.judge_score, "{:.2f}"),
	]
	return signals + [{"label": label, "value": fmt.format(v)} for label, v, fmt in metrics if v is not None]


def parse_lint(lint_issues: str | None) -> list[dict]:
	try:
		return json.loads(lint_issues) if lint_issues else []
	except ValueError:
		return []


def page_range(section: dict) -> list[int]:
	if not (section.page_start and section.page_end):
		return []
	return list(range(section.page_start, section.page_end + 1))


def first_line(notes: str | None, prefix: str) -> str:
	for note in (notes or "").split("; "):
		if note.startswith(prefix):
			return shorten(note.removeprefix(prefix), width=400, placeholder="…")
	return ""
