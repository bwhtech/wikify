"""Conversational tool (0.2 Slice 14) — `ask_clarification`.

A **terminal** tool: when the model needs a decision it can't make alone, it calls this to
end the turn with a question (and optional option chips). The loop special-cases terminal
tools — it doesn't feed a result back; it persists the question and emits
`wikify_agent_clarify`, then waits for the user's next message.
"""

from __future__ import annotations

from wikify.agent.context import Ctx
from wikify.agent.registry import Tool


def _ask_clarification(ctx: Ctx, args: dict) -> str:
	# The loop handles terminal tools directly from the call args; this is unused but kept
	# so the registry shape stays uniform (every Tool has a handler).
	return args.get("question") or ""


TOOLS = [
	Tool(
		name="ask_clarification",
		side="terminal",
		description=(
			"Ask the user a clarifying question and end your turn. Use ONLY when you genuinely "
			"can't proceed without a decision. Provide concise options when the answer is a "
			"choice between a few alternatives."
		),
		parameters={
			"type": "object",
			"properties": {
				"question": {"type": "string", "description": "The question to ask the user."},
				"options": {
					"type": "array",
					"items": {"type": "string"},
					"description": "Optional short answer choices, rendered as chips.",
				},
			},
			"required": ["question"],
		},
		handler=_ask_clarification,
	),
]
