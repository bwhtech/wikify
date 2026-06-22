# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Slice 11 — project context threading: the engine's LLM steps (cleanup, VLM, classify)
prepend the owning project's `context_prompt` as a clearly-delimited block, and blank
context reproduces v0.1 prompts byte-for-byte (no regression).
"""

from unittest.mock import patch

from frappe.tests.utils import FrappeTestCase

from wikify.engine.loader import classifier, cleanup_llm
from wikify.engine.loader.context import context_block
from wikify.engine.parsers import vlm


def _fake_resp(content="x"):
	return {"choices": [{"message": {"content": content}}], "usage": {}}


class TestContextBlock(FrappeTestCase):
	def test_blank_context_is_empty(self):
		"""Blank / whitespace-only context contributes nothing — prompts stay v0.1."""
		self.assertEqual(context_block(""), "")
		self.assertEqual(context_block(None), "")
		self.assertEqual(context_block("   \n  "), "")

	def test_filled_context_is_delimited(self):
		block = context_block("Surgical manual; prefer 'anaesthesia'.")
		self.assertTrue(block.startswith("Project context:\n"))
		self.assertIn("Surgical manual", block)
		self.assertTrue(block.endswith("\n\n"))


class TestContextThreading(FrappeTestCase):
	def _captured_content(self, mock):
		"""The user-message content of the single chat_completion call."""
		messages = mock.call_args.args[1]
		return messages[0]["content"]

	def test_cleanup_prepends_context(self):
		with patch.object(cleanup_llm.llm, "chat_completion", return_value=_fake_resp()) as m:
			cleanup_llm.clean_markdown("# Title\nbody", project_context="House style: terse.")
		content = self._captured_content(m)
		self.assertTrue(content.startswith("Project context:\nHouse style: terse.\n\n"))

	def test_cleanup_blank_context_unchanged(self):
		"""No context → no preamble; the prompt is the original instruction verbatim."""
		with patch.object(cleanup_llm.llm, "chat_completion", return_value=_fake_resp()) as m:
			cleanup_llm.clean_markdown("# Title\nbody")
		content = self._captured_content(m)
		self.assertNotIn("Project context:", content)
		self.assertTrue(content.startswith(cleanup_llm._PROMPT))

	def test_vlm_prepends_context(self):
		with patch.object(vlm.llm, "chat_completion", return_value=_fake_resp()) as m:
			vlm.parse_page_image("data:image/png;base64,zzz", project_context="Label anatomy precisely.")
		# VLM content is a [text, image_url] array; the text part carries the preamble.
		text = self._captured_content(m)[0]["text"]
		self.assertTrue(text.startswith("Project context:\nLabel anatomy precisely.\n\n"))

	def test_classify_appends_context_after_taxonomy(self):
		with (
			patch.object(classifier.llm, "has_openrouter", return_value=True),
			patch.object(classifier.llm, "chat_completion", return_value=_fake_resp('{"type":"other"}')) as m,
		):
			classifier.classify_section(
				"Intro", "body", ["intro", "other"], project_context="Domain: nephrology."
			)
		content = self._captured_content(m)
		self.assertIn("Project context:\nDomain: nephrology.", content)
		# Context sits after the taxonomy/format lines but before the section body.
		self.assertLess(content.index("exactly one of"), content.index("Project context:"))
		self.assertLess(content.index("Project context:"), content.index("TITLE:"))

	def test_classify_blank_context_unchanged(self):
		with (
			patch.object(classifier.llm, "has_openrouter", return_value=True),
			patch.object(classifier.llm, "chat_completion", return_value=_fake_resp('{"type":"other"}')) as m,
		):
			classifier.classify_section("Intro", "body", ["intro", "other"])
		self.assertNotIn("Project context:", self._captured_content(m))
