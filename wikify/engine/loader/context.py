"""Project-context preamble for the LLM steps (0.2 Slice 11).

A `Wikify Project` carries a free-text `context_prompt` (domain, audience, terminology,
house style). The jobs resolve it once and thread it down to the engine's LLM calls as a
plain `project_context: str` argument — the engine stays pure (string in, no DocType
reads). Each LLM step prepends/embeds a clearly-delimited block built here.

Blank context returns the empty string, so a project with no context produces prompts
byte-identical to v0.1 — the steering is purely additive (see 0.2/01-project-hierarchy).
"""

from __future__ import annotations


def context_block(project_context: str | None) -> str:
	"""A delimited `Project context:` preamble for an LLM prompt, or `""` when blank."""
	context = (project_context or "").strip()
	if not context:
		return ""
	return f"Project context:\n{context}\n\n"


def instruction_block(instruction: str | None) -> str:
	"""A delimited one-off steering instruction (0.2 Slice 14 — agent re-parse).

	The agent's `reparse_page` / `reparse_document` tools pass a plain-English instruction
	("keep the table as a real markdown table", "don't make this a mermaid diagram") that
	steers a single re-parse on top of the project context. Blank returns `""`, so a
	re-parse with no instruction reproduces the plain cleanup/VLM prompt byte-for-byte.
	"""
	instruction = (instruction or "").strip()
	if not instruction:
		return ""
	return f"Additional instruction from the user (follow it precisely):\n{instruction}\n\n"
