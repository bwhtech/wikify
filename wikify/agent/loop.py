"""The agent round loop — `AgentRunner.run()`.

One streaming, tool-calling completion per round: stream text deltas to the panel,
accumulate any tool calls (Builder's by-index pattern), run server tools, feed results
back, and finish when the model answers with no tool call. Everything is `server` or
`terminal` (no live canvas → no `client` side).
"""

from __future__ import annotations

import json
import re

import frappe
from frappe import _

from wikify.agent import llm, session
from wikify.agent.context import Ctx, resolve_attachments
from wikify.agent.prompts import system_prompt
from wikify.agent.registry import build_default_registry

MAX_ROUNDS = 25

# Which data plane each write tool touches — the aggregated mutation payload names the
# changed layers so open views refetch only what they show (0.4 slice 25).
_TOOL_LAYERS = {
	"edit_section_content": ["section"],
	"rebuild_section_from_pages": ["section"],
	"sync_wiki_page": ["wiki"],
	"read_wiki_page": [],
	"reparse_page": ["page", "section"],
	"use_page_image": ["page", "section"],
	"reparse_document": ["document"],
	"reclassify": ["tree"],
	"regenerate_wiki": ["wiki"],
	"move_section": ["tree"],
	"rename_section": ["tree"],
	"set_section_type": ["tree"],
	"toggle_include_in_wiki": ["tree"],
	"create_section": ["tree"],
	"delete_section": ["tree"],
	"split_section": ["tree"],
	"merge_sections": ["tree"],
	"create_section_type": ["taxonomy"],
}

# No-op guard (Builder's `claims_unbacked_action`): the model narrates a mutating action
# in the past tense but called no tool, so nothing actually changed. We only trip on the
# verbs that map to a real write tool to avoid false positives on benign phrasing.
_UNBACKED_CLAIM_RE = re.compile(
	r"\bI(?:'ve| have)\s+(?:now\s+|just\s+|already\s+)?"
	r"(?:moved|renamed|re-?tagged|re-?parsed|re-?classified|regenerated|embedded|"
	r"toggled|set the (?:section )?type|created the (?:section )?type)\b",
	re.IGNORECASE,
)
_NO_OP_NUDGE = (
	"You described performing an action (moving, renaming, retagging, re-parsing, etc.) "
	"but did not call any tool, so nothing actually changed. If the user asked for that "
	"change, call the appropriate tool now to make it real. If no change was requested, "
	"correct yourself and do not claim one."
)


def claims_unbacked_action(text: str) -> bool:
	return bool(text and _UNBACKED_CLAIM_RE.search(text))


def cancel_key(session_id: str) -> str:
	return f"wikify_agent_cancel:{session_id}"


def request_cancel(session_id: str) -> None:
	frappe.cache().set_value(cancel_key(session_id), "1", expires_in_sec=600)


class AgentRunner:
	"""Runs one user turn end-to-end: stream → tool loop → persist → realtime."""

	def __init__(
		self,
		session_id: str,
		user: str,
		*,
		attachments: list | None = None,
		approved_tools: list | None = None,
	):
		self.session_id = session_id
		self.user = user
		self.doc = frappe.get_doc("Wikify Agent Session", session_id)
		self.attachments = attachments or []
		# Resolve the turn's attachments into scoping defaults + a context block. The most
		# specific attachment wins; fall back to the session's opening scope.
		self.resolved = resolve_attachments(self.attachments)
		project = self.resolved.project or self.doc.project
		source_document = self.resolved.source_document or self.doc.source_document
		self.model = self.doc.model or llm.resolve_model(project=project)
		self.registry = build_default_registry()
		# No-op guard bookkeeping: did any mutating tool actually run this turn, and have
		# we already spent the single corrective round?
		self._turn_mutated = False
		self._correction_used = False
		# Mutations queue up per turn and flush as ONE aggregated event when the answer
		# lands (or at a confirm pause / error) — 0.4 slice 25.
		self._pending_mutations: list[dict] = []
		self.ctx = Ctx(
			session=session_id,
			user=user,
			project=project,
			source_document=source_document,
			attachments=self.attachments,
			approved=set(approved_tools or []),
		)

	# --- realtime ---------------------------------------------------------------------

	def _emit(self, event: str, payload: dict) -> None:
		frappe.publish_realtime(f"{event}:{self.session_id}", payload, user=self.user)

	def _cancelled(self) -> bool:
		return bool(frappe.cache().get_value(cancel_key(self.session_id)))

	def _clear_cancel(self) -> None:
		frappe.cache().delete_value(cancel_key(self.session_id))

	# --- entry point ------------------------------------------------------------------

	def run(self) -> None:
		self._clear_cancel()
		try:
			messages = self._build_messages()
			for _round in range(MAX_ROUNDS):
				if self._cancelled():
					self._finish_cancelled()
					return
				done = self._run_round(messages)
				if done:
					return
			# Ran out of rounds without a final answer.
			self._emit_error(_("The assistant took too many steps without finishing."))
		except Exception:
			frappe.log_error(title="Wikify agent run failed")
			self._emit_error(_("The assistant hit an error. Please try again."))
		finally:
			session.set_running(self.session_id, False)

	# --- one round --------------------------------------------------------------------

	def _run_round(self, messages: list[dict]) -> bool:
		"""Stream one completion. Returns True when the turn is finished."""
		tools = list(self.registry.values())
		assistant_msg = session.append_message(self.session_id, "assistant", "", status="streaming")
		message_id = assistant_msg.name

		text_acc = ""
		tool_calls: dict[int, dict] = {}

		response = llm.complete_with_tools(self.model, messages, tools, stream=True)
		for chunk in response:
			if self._cancelled():
				break
			delta = chunk.choices[0].delta if chunk.choices else None
			if not delta:
				continue
			if getattr(delta, "content", None):
				text_acc += delta.content
				self._emit("wikify_agent_stream", {"message_id": message_id, "chunk": delta.content})
			for tcd in getattr(delta, "tool_calls", None) or []:
				self._accumulate_tool_call(tool_calls, tcd)

		if self._cancelled():
			session.update_message(message_id, content=text_acc, status="done")
			self._finish_cancelled()
			return True

		ordered = [tool_calls[i] for i in sorted(tool_calls)]

		if not ordered:
			if not self._turn_mutated and not self._correction_used and claims_unbacked_action(text_acc):
				# The model claimed a mutating action but called no tool. Spend one
				# corrective round so it actually invokes the tool (or retracts the claim).
				self._correction_used = True
				session.update_message(message_id, content=text_acc, status="done")
				messages.append({"role": "assistant", "content": text_acc})
				messages.append({"role": "user", "content": _NO_OP_NUDGE})
				return False
			# Model answered — turn ends: flush the mutation batch, then complete (the
			# complete payload carries the counts so the chat card renders immediately).
			session.update_message(message_id, content=text_acc, status="done")
			session.touch(self.session_id)
			mutated_tools = [m["tool"] for m in self._pending_mutations]
			self._flush_mutations()
			self._emit(
				"wikify_agent_complete",
				{
					"message_id": message_id,
					"mutation_count": len(mutated_tools),
					"mutated_tools": mutated_tools,
				},
			)
			return True

		# A terminal tool (ask_clarification) ends the turn with a question — no result is
		# fed back. Persist it as a `clarification` message (history skips those, so the
		# assistant turn's tool_calls never dangle) and emit the clarify event.
		terminal = next(
			(c for c in ordered if (t := self.registry.get(c["name"])) and t.side == "terminal"), None
		)
		if terminal:
			targs = _safe_json(terminal["arguments"])
			question = targs.get("question") or text_acc or _("Could you clarify?")
			options = targs.get("options") or []
			session.update_message(
				message_id, content=question, status="clarification", metadata={"options": options}
			)
			session.touch(self.session_id)
			self._flush_mutations()  # the turn pauses on a question — show work done so far
			self._emit(
				"wikify_agent_clarify",
				{"message_id": message_id, "question": question, "options": options},
			)
			return True

		# Persist the assistant turn (text + requested tool calls), then run them.
		persisted_calls = [
			{"id": c["id"], "name": c["name"], "args": _safe_json(c["arguments"])} for c in ordered
		]
		session.update_message(message_id, content=text_acc, tool_calls=persisted_calls, status="done")
		messages.append(
			{
				"role": "assistant",
				"content": text_acc or None,
				"tool_calls": [
					{
						"id": c["id"],
						"type": "function",
						"function": {"name": c["name"], "arguments": c["arguments"]},
					}
					for c in ordered
				],
			}
		)

		for call in ordered:
			result = self._run_tool(call)
			messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
		return False

	def _accumulate_tool_call(self, acc: dict[int, dict], tcd) -> None:
		idx = tcd.index or 0
		entry = acc.setdefault(idx, {"id": "", "name": "", "arguments": ""})
		if getattr(tcd, "id", None):
			entry["id"] = tcd.id
		fn = getattr(tcd, "function", None)
		if fn:
			if getattr(fn, "name", None):
				entry["name"] = fn.name
			if getattr(fn, "arguments", None):
				entry["arguments"] += fn.arguments

	def _run_tool(self, call: dict) -> str:
		"""Run one tool call and return the result string fed back to the model."""
		name = call["name"]
		args = _safe_json(call["arguments"])
		tool = self.registry.get(name)
		self._emit(
			"wikify_agent_tool",
			{"name": name, "args": args, "status": "running", "call_id": call["id"]},
		)

		# Expensive/destructive tools hold for a UI confirm card. Until the user approves
		# (the tool name arrives in `ctx.approved` on the follow-up run) the tool does NOT
		# run — we feed back a sentinel so the model asks the user to confirm.
		if tool and tool.confirm and name not in self.ctx.approved:
			# The user is about to look at the screen to decide — flush what's applied so
			# far so the views behind the confirm card are current.
			self._flush_mutations()
			self._emit(
				"wikify_agent_confirm",
				{"name": name, "args": args, "call_id": call["id"], "summary": tool.description},
			)
			result = _(
				"[NOT EXECUTED — awaiting user confirmation] This is an expensive/destructive "
				"operation. Briefly tell the user exactly what it will do and that they need to "
				"confirm it; it will run only when they approve."
			)
			self._finish_tool(call, args, result, status="needs_confirmation")
			return result

		if not tool:
			result = _("Unknown tool: {0}").format(name)
		else:
			try:
				result = tool.handler(self.ctx, args)
				if tool.mutates:
					self._turn_mutated = True
					# Commit now (durability + confirm-gating rely on it) but queue the
					# frontend signal — open views refresh once, when the answer lands.
					self._record_mutation(name)
			except Exception:
				frappe.log_error(title=f"Wikify agent tool failed: {name}")
				result = _("Tool {0} failed: {1}").format(name, frappe.get_traceback(with_context=False))
		self._finish_tool(call, args, result)
		return result

	def _finish_tool(self, call: dict, args: dict, result: str, status: str = "done") -> None:
		"""Persist the tool result row + emit the tool-card 'done' event."""
		summary = result if len(result) <= 200 else result[:200] + "…"
		session.append_message(
			self.session_id,
			"tool",
			result,
			tool_name=call["name"],
			tool_call_id=call["id"],
			metadata={"args": args},
		)
		self._emit(
			"wikify_agent_tool",
			{"name": call["name"], "args": args, "status": status, "summary": summary, "call_id": call["id"]},
		)

	def _record_mutation(self, tool_name: str) -> None:
		"""Commit a mutating tool's write and queue it for the end-of-turn batch.

		Commit now: the loop runs in a background job whose transaction would otherwise
		not be visible until the turn ends, so the eventual frontend refetch would read
		stale rows — and a crash mid-turn must not lose applied edits. Only the *signal*
		is deferred (0.4 slice 25), not the write.
		"""
		frappe.db.commit()
		self._pending_mutations.append(
			{"tool": tool_name, "source_document": self.ctx.source_document}
		)

	def _flush_mutations(self) -> None:
		"""Emit ONE aggregated `wikify_agent_mutation` for everything queued this turn.

		Fired at turn completion (and at confirm pauses / clarify questions / errors, so
		partial-but-committed work is never invisible). `source_document` is kept beside
		the aggregate fields for any listener still on the per-tool payload shape.
		"""
		if not self._pending_mutations:
			return
		batch, self._pending_mutations = self._pending_mutations, []
		layers: list[str] = []
		for m in batch:
			for layer in _TOOL_LAYERS.get(m["tool"], ["document"]):
				if layer not in layers:
					layers.append(layer)
		frappe.publish_realtime(
			"wikify_agent_mutation",
			{
				"source_documents": sorted({m["source_document"] for m in batch if m["source_document"]}),
				"source_document": self.ctx.source_document,
				"layers": layers,
				"mutations": batch,
				"count": len(batch),
			},
			user=self.user,
		)

	# --- message assembly -------------------------------------------------------------

	def _build_messages(self) -> list[dict]:
		# Project context comes from the attached project (or the attached document's
		# project), falling back to the session's project.
		project_context = self.resolved.project_context
		if not project_context and self.doc.project:
			project_context = frappe.db.get_value("Wikify Project", self.doc.project, "context_prompt") or ""
		messages: list[dict] = [
			{"role": "system", "content": self._cacheable(system_prompt(project_context))}
		]
		if self.resolved.block:
			# A second system message carries the bounded attachment context for this turn.
			messages.append({"role": "system", "content": self._cacheable(self.resolved.block)})
		messages.extend(session.history_messages(self.session_id))
		return messages

	def _cacheable(self, content: str):
		"""Mark a large, stable block for Anthropic prompt caching.

		The system prompt + attachment context block are big and identical across the
		turn's rounds, so a cache breakpoint cuts cost/latency on the tool loop. Only
		Anthropic models read `cache_control`; other models get the plain string (and
		`litellm.drop_params` would strip it anyway).
		"""
		model = (self.model or "").lower()
		if "claude" in model or "anthropic" in model:
			return [{"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}]
		return content

	# --- terminal states --------------------------------------------------------------

	def _finish_cancelled(self) -> None:
		self._clear_cancel()
		session.touch(self.session_id)
		self._flush_mutations()
		self._emit("wikify_agent_complete", {"message_id": None, "cancelled": True})

	def _emit_error(self, message: str) -> None:
		session.append_message(self.session_id, "assistant", message, status="error")
		self._flush_mutations()  # partial work is committed — the UI must reflect it
		self._emit("wikify_agent_error", {"message": message})


def _safe_json(raw: str) -> dict:
	try:
		return json.loads(raw) if raw else {}
	except (ValueError, TypeError):
		return {}
