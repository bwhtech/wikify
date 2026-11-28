"""The review queue of one Source Document: detected issues minus accepted ones, plus the
dashboard numbers. Built fresh on every read, so it never goes stale."""

from __future__ import annotations

from functools import cached_property

from wikify.engine import settings, store
from wikify.engine.review import rules
from wikify.engine.review.suggestions import suggestions_for

IMPROVED_SOURCES = ("cleanup", "vlm")
MAX_SECTION_IMAGES = 20


class ReviewQueue:
	def __init__(self, source_document: str, import_name: str | None = None):
		self.source_document = source_document
		self.import_name = import_name
		self.pages = store.get_review_pages(source_document)
		self.sections = store.get_review_sections(source_document)
		self.decisions = store.get_review_decisions(source_document)
		self.detected = rules.detect(
			source_document,
			self.pages,
			self.sections,
			float(settings.get("escalate_threshold")),
			store.get_other_section_type(),
		)

	def overview(self) -> dict:
		return {
			"summary": self.summary(),
			"page_map": self.page_map(),
			"issues": [self._listed(i) for i in self.open],
			"reviewed": self.reviewed(),
		}

	def detail(self, issue_id: str) -> dict | None:
		found = self.find(issue_id)
		if not found:
			return None
		row = self.row(found)
		return {
			**found,
			"status": "open" if found in self.open else "settled",
			"suggestions": suggestions_for(found, row, self.taxonomy),
			"history": [d for d in self.decisions if d.issue_id == issue_id],
			"markdown": self._markdown(found, row),
			"images": self._images(found),
		}

	def find(self, issue_id: str) -> dict | None:
		return next((i for i in self.detected if i["id"] == issue_id), None)

	@cached_property
	def open(self) -> list[dict]:
		settled = {(d.issue_id, d.fingerprint) for d in self.decisions if d.fingerprint}
		return [i for i in self.detected if (i["id"], i["fingerprint"]) not in settled]

	@cached_property
	def taxonomy(self) -> list[str]:
		return store.get_section_taxonomy()

	def summary(self) -> dict:
		open_pages = {n for i in self.open for n in i["pages"]}
		return {
			"pages": len(self.pages),
			"ready": len(self.pages) - len(open_pages),
			"improved": sum(1 for p in self.pages if p.canonical_source in IMPROVED_SOURCES),
			"open": len(self.open),
			"blocks": sum(1 for i in self.open if i["severity"] == "blocks"),
			"reviewed": len(self.reviewed()),
			"safe": len(self.safe_issues()),
		}

	def safe_issues(self) -> list[tuple[dict, dict]]:
		"""(issue, suggestion) pairs whose recommended fix is safe to apply in bulk."""
		pairs = []
		for found in self.open:
			recommended = self._recommended(found)
			if recommended and recommended["safe"]:
				pairs.append((found, recommended))
		return pairs

	def page_map(self) -> list[dict]:
		worst: dict[int, dict] = {}
		for found in sorted(self.open, key=lambda i: (i["severity"] == "blocks", len(i["pages"]) == 1)):
			for page_no in found["pages"]:
				worst[page_no] = found
		reviewed = self._reviewed_pages()
		return [self._tile(p, worst.get(p.page_no), reviewed) for p in self.pages]

	def reviewed(self) -> list[dict]:
		open_ids = {i["id"] for i in self.open}
		latest: dict[str, dict] = {}
		for decision in self.decisions:
			if decision.issue_id not in open_ids:
				latest.setdefault(decision.issue_id, decision)
		return list(latest.values())

	def _listed(self, found: dict) -> dict:
		recommended = self._recommended(found)
		return {**found, "suggested": recommended["label"] if recommended else None}

	def _recommended(self, found: dict) -> dict | None:
		options = suggestions_for(found, self.row(found), self.taxonomy)
		return next((s for s in options if s["recommended"]), None)

	def row(self, found: dict) -> dict | None:
		rows = {"Source Page": self.pages, "Source Section": self.sections}.get(
			found["target"]["doctype"], []
		)
		return next((r for r in rows if r.name == found["target"]["name"]), None)

	def _markdown(self, found: dict, row: dict | None) -> str | None:
		if found["target"]["doctype"] == "Source Page":
			return row.canonical_markdown
		if found["target"]["doctype"] == "Source Section":
			return row.markdown
		return None

	def _images(self, found: dict) -> list[dict]:
		wanted = set(found["pages"][:MAX_SECTION_IMAGES])
		if found["target"]["doctype"] == "Source Document":
			return []
		return [
			{"page_no": p.page_no, "image": p.image} for p in self.pages if p.page_no in wanted and p.image
		]

	def _reviewed_pages(self) -> set[int]:
		by_name = {p.name: p.page_no for p in self.pages}
		sections = {s.name: rules.page_range(s) for s in self.sections}
		pages: set[int] = set()
		for decision in self.reviewed():
			if decision.target_name in by_name:
				pages.add(by_name[decision.target_name])
			pages.update(sections.get(decision.target_name, []))
		return pages

	def _tile(self, page: dict, found: dict | None, reviewed: set[int]) -> dict:
		if found:
			return {"page_no": page.page_no, "state": found["severity"], "issue_id": found["id"]}
		if page.page_no in reviewed:
			state = "reviewed"
		elif page.canonical_source in IMPROVED_SOURCES:
			state = "improved"
		else:
			state = "clean"
		return {"page_no": page.page_no, "state": state, "issue_id": None}
