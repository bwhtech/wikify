"""Resolved access to the `Wikify Settings` Single.

Lifts the POC `config.py` model ids + tunables into a site-config doctype so they're
switchable without code (the POC already made them env-overridable). Code-side
constants that we don't expose for tuning (the composite weights, visual detection
heuristics) stay in `engine.config`.

The OpenRouter key resolves in priority order: the `Wikify Settings` password →
`site_config.json` (`openrouter_key`) → process env (`OPENROUTER_KEY` /
`OPENROUTER_API_KEY`) → the app's `.env` file. The Settings password is the intended
home; the rest are dev conveniences so a key in `apps/wikify/.env` still works.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

import frappe

# Defaults mirror the POC `config.py` (used when a Settings field is blank).
DEFAULTS = {
	"vlm_model": "mistralai/mistral-medium-3.1",
	"cleanup_model": "google/gemini-2.5-flash",
	"judge_model": "anthropic/claude-sonnet-4.6",
	"classifier_model": "google/gemini-2.5-flash",
	"pass_threshold": 0.90,
	"escalate_threshold": 0.70,
	"cleanup_recall_tolerance": 0.12,
	"render_dpi": 150,
	"visual_min_chars": 250,
	"visual_min_drawings": 40,
	"remediation_workers": 6,
	"classify_workers": 8,
	"judge_all_pages": 0,
}


def get_settings():
	"""The `Wikify Settings` Single doc (cached per request)."""
	return frappe.get_cached_doc("Wikify Settings")


def get(field: str):
	"""A single setting, falling back to the POC default when blank."""
	try:
		value = get_settings().get(field)
	except Exception:
		value = None
	if value in (None, ""):
		return DEFAULTS.get(field)
	return value


@lru_cache(maxsize=1)
def _dotenv_key() -> str:
	"""Last-resort: read OPENROUTER_KEY from the app's .env without python-dotenv."""
	env_path = Path(frappe.get_app_path("wikify")).parent / ".env"
	if not env_path.exists():
		return ""
	for line in env_path.read_text(encoding="utf-8").splitlines():
		line = line.strip()
		if line.startswith(("OPENROUTER_KEY", "OPENROUTER_API_KEY")) and "=" in line:
			return line.split("=", 1)[1].strip().strip("\"'")
	return ""


def openrouter_key() -> str:
	"""Resolve the OpenRouter API key (Settings → site config → env → .env)."""
	try:
		key = get_settings().get_password("openrouter_api_key")
	except Exception:
		key = None
	key = (
		key
		or frappe.conf.get("openrouter_key")
		or os.environ.get("OPENROUTER_KEY")
		or os.environ.get("OPENROUTER_API_KEY")
		or _dotenv_key()
	)
	return (key or "").strip()
