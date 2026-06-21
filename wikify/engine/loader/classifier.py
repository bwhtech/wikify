"""Tag a section with a `section_type` via OpenRouter.

Ported from the POC `loader/classifier.py`. I/O-boundary changes only: the taxonomy
comes from the `Section Type` master (passed in, so the corpus can extend it) instead
of a hard-coded `config.SECTION_TYPES`, the model id comes from `Wikify Settings`, and
the call goes through `engine.llm` (dict-shaped REST response). The prompt + fallback
logic are unchanged: any failure (or no API key) falls back to `"other"` — never an
error, so a parse without a key still completes with everything typed `other`.
"""

from __future__ import annotations

import json
import time

from wikify.engine import llm, settings

# Bulk eager classification fires one call per section back-to-back, so a big manual can
# trip the provider's rate limit. A transient failure must NOT be silently recorded as
# "other" (that conflates a real verdict with a dropped call and tanks Explore quality),
# so retry with backoff first; only fall back to "other" once the call genuinely can't
# complete or the model actually returns an off-taxonomy / "other" label.
_RETRIES = 3
_BACKOFF_SECONDS = 2.0


def classify_section(title: str, content: str, taxonomy: list[str]) -> str:
	"""Return one taxonomy label for a section, or `"other"` on a genuine miss.

	Retries transient API failures (rate limits) with backoff before giving up — a
	dropped call shouldn't masquerade as the `"other"` catch-all.
	"""
	if not llm.has_openrouter() or not taxonomy:
		return "other"
	labels = ", ".join(taxonomy)
	prompt = (
		f"Classify this document section into exactly one of: {labels}.\n"
		f'Respond ONLY as JSON: {{"type":"<one of the labels>"}}.\n\n'
		f"TITLE: {title}\nCONTENT (truncated):\n{content[:1500]}"
	)
	for attempt in range(_RETRIES + 1):
		try:
			resp = llm.chat_completion(
				settings.get("classifier_model"),
				[{"role": "user", "content": prompt}],
				label="classify",
				response_format={"type": "json_object"},
				max_tokens=64,
			)
			raw = resp["choices"][0]["message"]["content"] or "{}"
			label = json.loads(raw).get("type", "other")
			return label if label in taxonomy else "other"
		except Exception:
			if attempt < _RETRIES:
				time.sleep(_BACKOFF_SECONDS * (attempt + 1))  # linear backoff for rate limits
				continue
			return "other"
