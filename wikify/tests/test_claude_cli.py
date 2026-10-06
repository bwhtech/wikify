# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

import json
from types import SimpleNamespace
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import claude_cli


class TestClaudeCliSwitch(FrappeTestCase):
	def is_enabled(self, use_local_models, developer_mode, in_test=False):
		frappe.db.set_single_value("Wikify Settings", "use_local_models", use_local_models)
		with (
			patch.dict(frappe.conf, {"developer_mode": developer_mode}),
			patch.dict(frappe.flags, {"in_test": in_test}),
		):
			return claude_cli.is_enabled()

	def test_on_only_when_ticked_in_developer_mode(self):
		self.assertTrue(self.is_enabled(1, 1))

	def test_off_when_not_ticked(self):
		self.assertFalse(self.is_enabled(0, 1))

	def test_off_outside_developer_mode_even_when_ticked(self):
		self.assertFalse(self.is_enabled(1, 0))

	def test_off_while_tests_run(self):
		self.assertFalse(self.is_enabled(1, 1, in_test=True))


class TestClaudeCliMessages(FrappeTestCase):
	def test_single_user_message_keeps_its_image(self):
		content = claude_cli.user_content(
			[
				{
					"role": "user",
					"content": [
						{"type": "text", "text": "Read this page"},
						{"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
					],
				}
			]
		)
		self.assertEqual(
			content,
			[
				{"type": "text", "text": "Read this page"},
				{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": "AAAA"}},
			],
		)

	def test_tool_round_trip_becomes_a_transcript(self):
		content = claude_cli.user_content(
			[
				{"role": "user", "content": "Rename section 3"},
				{
					"role": "assistant",
					"content": None,
					"tool_calls": [
						{"id": "call_1", "function": {"name": "rename_section", "arguments": '{"n": 3}'}}
					],
				},
				{"role": "tool", "tool_call_id": "call_1", "content": "renamed"},
			]
		)
		self.assertEqual(
			content[0]["text"],
			"USER:\nRename section 3\n\n"
			'ASSISTANT CALLED rename_section (call_1): {"n": 3}\n\n'
			"TOOL RESULT (call_1):\nrenamed\n\n"
			"Continue as the ASSISTANT from here.",
		)


TOOLS = [SimpleNamespace(name="read_tree", description="Read the tree", parameters={"type": "object"})]


class TestClaudeCliNativeToolRetry(FrappeTestCase):
	def completed(self, native_tools, tool_calls):
		events = [
			{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": name}]}}
			for name in [*native_tools, "StructuredOutput"]
		]
		events.append({"type": "result", "structured_output": {"text": "", "tool_calls": tool_calls}})
		return SimpleNamespace(stdout="\n".join(json.dumps(event) for event in events), stderr="")

	def tool_calls_of(self, chunks):
		return [call.function.name for chunk in chunks for call in chunk.choices[0].delta.tool_calls]

	def test_native_tool_call_is_retried_with_a_reminder(self):
		replies = [
			self.completed(["read_tree"], []),
			self.completed([], [{"name": "read_tree", "arguments": {}}]),
		]
		with patch.object(claude_cli.subprocess, "run", side_effect=replies) as run:
			chunks = list(
				claude_cli.complete_with_tools([{"role": "user", "content": "Show the tree"}], TOOLS)
			)
		self.assertEqual(run.call_count, 2)
		retry_prompt = run.call_args_list[1].args[0][
			run.call_args_list[1].args[0].index("--system-prompt") + 1
		]
		self.assertTrue(retry_prompt.endswith(claude_cli.NATIVE_CALL_RETRY))
		self.assertEqual(self.tool_calls_of(chunks), ["read_tree"])

	def test_structured_reply_runs_once(self):
		reply = self.completed([], [{"name": "read_tree", "arguments": {}}])
		with patch.object(claude_cli.subprocess, "run", return_value=reply) as run:
			chunks = list(
				claude_cli.complete_with_tools([{"role": "user", "content": "Show the tree"}], TOOLS)
			)
		self.assertEqual(run.call_count, 1)
		self.assertEqual(self.tool_calls_of(chunks), ["read_tree"])
