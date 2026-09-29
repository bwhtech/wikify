from __future__ import annotations

import time

import requests

from wikify.engine import llm

SYSTEM_ONE_URL = f"{llm.OPENROUTER_BASE_URL}/systemone"
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504, 529})
MAX_ATTEMPTS = 3
MAX_BACKOFF_SECONDS = 30.0


def _backoff(attempt: int, retry_after: str | None = None) -> float:
	try:
		return min(float(retry_after), MAX_BACKOFF_SECONDS)
	except (TypeError, ValueError):
		return min(2.0**attempt, MAX_BACKOFF_SECONDS)


def system_one(
	state,
	questions: dict,
	*,
	model: str,
	api_key: str,
	timeout: int = 60,
) -> dict:
	if not api_key:
		raise RuntimeError("OPENROUTER key not set; Jev scoring unavailable.")

	body = {"state": state, "model": model, "questions": questions}
	headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
	for attempt in range(MAX_ATTEMPTS):
		last = attempt == MAX_ATTEMPTS - 1
		try:
			resp = requests.post(SYSTEM_ONE_URL, headers=headers, json=body, timeout=timeout)
		except (requests.ConnectionError, requests.Timeout):
			if last:
				raise
			time.sleep(_backoff(attempt))
			continue
		if resp.status_code in RETRY_STATUSES and not last:
			time.sleep(_backoff(attempt, resp.headers.get("retry-after")))
			continue
		resp.raise_for_status()
		return resp.json()
	raise RuntimeError("unreachable")
