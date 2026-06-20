"""LLM-as-judge via OpenRouter: page image + parsed markdown -> rubric scores.

Returns a 0..1 judge_score plus per-criterion detail. Opt-in (cost/latency).
Ported from the POC `verify/judge.py`; the only changes are the I/O boundary
(`engine.llm.chat_completion` instead of the openai SDK, and the page image arrives
as a data URL) and the judge model id coming from `engine.settings`.
"""

from __future__ import annotations

import json
import re

from wikify.engine import llm, settings


def _extract_json(raw: str) -> dict | None:
	"""Salvage a JSON object from a model reply (handles code fences / prose)."""
	if not raw:
		return None
	text = raw.strip()
	# strip ```json ... ``` fences
	fence = re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL)
	if fence:
		text = fence.group(1).strip()
	try:
		return json.loads(text)
	except json.JSONDecodeError:
		pass
	# last resort: grab the outermost { ... }
	brace = re.search(r"\{.*\}", text, re.DOTALL)
	if brace:
		try:
			return json.loads(brace.group(0))
		except json.JSONDecodeError:
			return None
	return None


_RUBRIC = (
	"You are grading how faithfully a Markdown transcription reproduces a PDF page image.\n"
	"Give an OVERALL faithfulness score 1-5 (this is the main score):\n"
	"  5 = every piece of visible content AND its structure is captured\n"
	"  3 = roughly half the content/structure captured\n"
	"  1 = most content missing, empty, or just a placeholder like 'picture omitted'\n"
	"For a diagram/flowchart, faithful means the boxes, labels, and their relationships "
	"are captured (e.g. as nested lists/headings). An empty or placeholder transcription "
	"must score 1 even if it 'added nothing wrong'.\n"
	"Also rate 1-5: completeness (content present), structure (headings/tables/order), "
	"no_hallucination (nothing invented).\n"
	'Respond ONLY with JSON: {"overall":n,"completeness":n,"structure":n,'
	'"no_hallucination":n,"note":"<one short sentence>"}'
)


def judge_page(image_data_url: str, markdown: str) -> dict:
	resp = llm.chat_completion(
		settings.get("judge_model"),
		[
			{
				"role": "user",
				"content": [
					{"type": "text", "text": _RUBRIC},
					{"type": "image_url", "image_url": {"url": image_data_url}},
					{"type": "text", "text": f"MARKDOWN:\n{markdown[:8000]}"},
				],
			}
		],
		label="judge",
		response_format={"type": "json_object"},
		max_tokens=1024,
	)
	raw = resp["choices"][0]["message"].get("content") or ""
	data = _extract_json(raw)
	if data is None:
		return {"judge_score": None, "note": "judge unparseable", "raw": raw[:300]}

	# Holistic 'overall' score is the signal (an average of sub-criteria inflates
	# empty output via trivially-satisfied criteria). Fall back to completeness.
	primary = data.get("overall", data.get("completeness"))
	score = (float(primary) / 5.0) if primary is not None else None
	return {
		"judge_score": round(score, 3) if score is not None else None,
		"note": data.get("note", ""),
		**data,
	}
