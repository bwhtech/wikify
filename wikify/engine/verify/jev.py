"""Parse-fidelity scoring with TypeSafe Jev: PDF text layer vs. a page's canonical markdown."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

import fitz
from frappe.utils import now_datetime

from wikify.engine import config, jev_client, settings, store

_IGNORE = "Ignore page numbers and running headers or footers."
_PLAIN = "`pdf_text` is plain text extracted from the PDF, so its tables and headings appear as plain lines."

QUESTIONS = {
	"completeness": {
		"type": "score",
		"instructions": f"How much of the content in `pdf_text` also appears in `markdown`? {_IGNORE}",
		"criteria": [
			"Almost none of the content in `pdf_text` appears in `markdown`, or `markdown` is empty",
			"About a quarter of the content in `pdf_text` appears in `markdown`",
			"About half of the content in `pdf_text` appears in `markdown`",
			"Most of the content in `pdf_text` appears in `markdown`; a few sentences, rows or items are missing",
			"All of the content in `pdf_text` appears in `markdown`",
		],
	},
	"structure": {
		"type": "score",
		"instructions": (
			"How well does `markdown` preserve the headings, lists, tables and reading order "
			f"of `pdf_text`? {_PLAIN} {_IGNORE}"
		),
		"criteria": [
			"Structure is lost: headings, lists and tables are flattened into plain text or the order is jumbled",
			"Some structure is kept, but major headings, lists or tables are broken or out of order",
			"Structure is mostly right, with minor issues such as a wrong heading level or a misaligned table cell",
			"Headings, lists, tables and reading order all match `pdf_text`",
		],
	},
	"accuracy": {
		"type": "score",
		"instructions": (
			"Is every piece of text in `markdown` supported by `pdf_text`? "
			"Ignore Markdown image tags and formatting syntax."
		),
		"criteria": [
			"`markdown` contains a lot of text that is not in `pdf_text`, or many garbled or misread words",
			"`markdown` contains several invented sentences or misread words or numbers",
			"`markdown` contains a few minor misreadings or small additions",
			"Every piece of text in `markdown` is supported by `pdf_text`",
		],
	},
	"placeholder": {
		"type": "noul",
		"instructions": (
			"Is `markdown` empty, or only a placeholder such as 'image omitted' instead of the page's content?"
		),
	},
}


@dataclass
class JevPageScore:
	page_no: int
	status: str
	score: float | None = None
	confidence: float | None = None
	weight: int = 0
	detail: dict = field(default_factory=dict)


def build_state(pdf_text: str, markdown: str) -> dict:
	limit = config.JEV_MAX_FIELD_CHARS
	return {"pdf_text": pdf_text[:limit], "markdown": markdown[:limit]}


def is_scorable(kind: str, pdf_text: str) -> bool:
	return kind != "visual" and len("".join(pdf_text.split())) >= config.JEV_MIN_TEXT_CHARS


def combine(answers: dict) -> tuple[float, float | None, dict]:
	detail: dict = {}
	weighted = 0.0
	confidences = []
	for key, weight in config.JEV_WEIGHTS.items():
		answer = answers[key]
		top = len(QUESTIONS[key]["criteria"]) - 1
		normalized = max(0.0, min(1.0, float(answer["score"]) / top))
		weighted += weight * normalized
		if answer.get("confidence") is not None:
			confidences.append(float(answer["confidence"]))
		detail[key] = {
			"score": round(normalized, 3),
			"level": answer["score"],
			"confidence": answer.get("confidence"),
			"probabilities": answer.get("probabilities"),
		}
	score = weighted / sum(config.JEV_WEIGHTS.values())
	placeholder = float(answers["placeholder"]["noul"])
	detail["placeholder"] = round(placeholder, 3)
	if placeholder > 0.5:
		score = min(score, config.JEV_PLACEHOLDER_CAP)
	confidence = round(min(confidences), 3) if confidences else None
	return round(score, 3), confidence, detail


def score_page(page_no: int, pdf_text: str, markdown: str, *, api_key: str, model: str) -> JevPageScore:
	weight = len("".join(pdf_text.split()))
	if not markdown.strip():
		return JevPageScore(page_no, "scored", 0.0, 1.0, weight, {"empty_markdown": True})
	try:
		resp = jev_client.system_one(build_state(pdf_text, markdown), QUESTIONS, model=model, api_key=api_key)
		score, confidence, detail = combine(resp["answers"])
	except Exception as e:
		return JevPageScore(page_no, "error", detail={"error": str(e)[:500]})
	detail["model"] = resp.get("model")
	usage = resp.get("usage") or {}
	detail["input_tokens"] = usage.get("input_tokens")
	detail["cost"] = usage.get("cost")
	return JevPageScore(page_no, "scored", score, confidence, weight, detail)


def document_score(results: list[JevPageScore]) -> float | None:
	scored = [r for r in results if r.status == "scored" and r.weight > 0]
	total = sum(r.weight for r in scored)
	if not total:
		return None
	return round(sum(r.score * r.weight for r in scored) / total, 3)


def score_document(
	source_document: str,
	pdf_path: str,
	progress_cb: Callable[[int, int], None] | None = None,
) -> dict:
	api_key = settings.openrouter_key()
	model = settings.get("jev_model")
	workers = max(1, int(settings.get("remediation_workers") or 1))
	pages = store.get_jev_pages(source_document)

	with fitz.open(pdf_path) as doc:
		texts = {p["page_no"]: doc[p["page_no"] - 1].get_text("text") for p in pages}

	results: list[JevPageScore] = []
	jobs = []
	for p in pages:
		if is_scorable(p["kind"], texts[p["page_no"]]):
			jobs.append(p)
		else:
			results.append(JevPageScore(p["page_no"], "skipped"))

	names = {p["page_no"]: p["name"] for p in pages}
	for r in results:
		store.set_page_jev(names[r.page_no], r)

	total = len(jobs)
	with ThreadPoolExecutor(max_workers=workers) as pool:
		futures = [
			pool.submit(
				score_page,
				p["page_no"],
				texts[p["page_no"]],
				p["markdown"],
				api_key=api_key,
				model=model,
			)
			for p in jobs
		]
		for done, future in enumerate(futures, start=1):
			result = future.result()
			store.set_page_jev(names[result.page_no], result)
			results.append(result)
			if progress_cb:
				progress_cb(done, total)

	scored = [r for r in results if r.status == "scored"]
	cost = round(sum(r.detail.get("cost") or 0 for r in scored), 6)
	store.add_document_cost(source_document, cost)
	summary = {
		"score": document_score(results),
		"scored": len(scored),
		"skipped": sum(r.status == "skipped" for r in results),
		"errors": sum(r.status == "error" for r in results),
		"low": sum(r.score < config.JEV_LOW_THRESHOLD for r in scored),
		"total": len(results),
		"cost": cost,
	}
	store.set_document_jev(source_document, summary, now_datetime())
	return summary
