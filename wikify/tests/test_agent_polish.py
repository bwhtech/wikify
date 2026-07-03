# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""Slice 16 — polish: model resolution (project → settings → built-in) + the picker list,
session rename/archive (with ownership guard), the no-op guard (`claims_unbacked_action`
+ the corrective round), and the Anthropic prompt-cache markers. litellm is mocked.
"""

from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.agent import llm, session
from wikify.agent.loop import AgentRunner, claims_unbacked_action
from wikify.api import agent as agent_api
from wikify.engine import store
from wikify.tests import _cleanup
from wikify.tests.test_agent import FakeLLM, _sec, _text_chunk, _tool_chunk


class TestAgentPolish(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Polish Test"}).insert(
			ignore_permissions=True
		)
		# The loop commits mid-turn, defeating rollback — raw-delete what we insert.
		self.addCleanup(_cleanup.delete_document, self.sd.name)
		_cleanup.register_session_sweep(self)
		store.replace_sections(self.sd.name, [_sec("1. Alpha", 1, ["1. Alpha"], 1, 1)])

	def _settings_agent_model(self, value):
		doc = frappe.get_doc("Wikify Settings")
		doc.agent_model = value
		doc.save(ignore_permissions=True)

	# --- model resolution ----------------------------------------------------------------

	def test_resolve_model_prefers_explicit(self):
		self.assertEqual(llm.resolve_model("foo/bar"), "foo/bar")

	def test_resolve_model_project_overrides_settings(self):
		self._settings_agent_model("settings/model")
		proj = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"P {frappe.generate_hash(length=6)}",
				"agent_model": "project/model",
			}
		).insert(ignore_permissions=True)
		self.assertEqual(llm.resolve_model(project=proj.name), "project/model")

	def test_resolve_model_falls_back_to_settings_then_builtin(self):
		self._settings_agent_model("settings/model")
		self.assertEqual(llm.resolve_model(), "settings/model")
		self._settings_agent_model("")
		self.assertEqual(llm.resolve_model(), llm.DEFAULT_AGENT_MODEL)

	def test_agent_models_lists_default_first(self):
		self._settings_agent_model("")
		models = llm.agent_models()
		self.assertEqual(models[0], llm.DEFAULT_AGENT_MODEL)
		# Distinct, and includes the pipeline models from Settings.
		self.assertEqual(len(models), len(set(models)))
		self.assertEqual(agent_api.get_agent_models(), models)

	# --- session management --------------------------------------------------------------

	def test_rename_session(self):
		sess = session.get_or_create(None, user="Administrator", scope="global")
		agent_api.rename_session(sess.name, "  Cleanup chat  ")
		self.assertEqual(frappe.db.get_value("Wikify Agent Session", sess.name, "title"), "Cleanup chat")

	def test_rename_session_rejects_blank(self):
		sess = session.get_or_create(None, user="Administrator", scope="global")
		with self.assertRaises(frappe.ValidationError):
			agent_api.rename_session(sess.name, "   ")

	def test_archive_session_drops_from_list(self):
		sess = session.get_or_create(None, user="Administrator", scope="global")
		frappe.db.set_value("Wikify Agent Session", sess.name, "last_interaction_on", frappe.utils.now())
		self.assertTrue(any(s["name"] == sess.name for s in agent_api.list_sessions()))
		agent_api.archive_session(sess.name)
		self.assertEqual(frappe.db.get_value("Wikify Agent Session", sess.name, "status"), "Archived")
		self.assertFalse(any(s["name"] == sess.name for s in agent_api.list_sessions()))

	def test_session_management_ownership_guard(self):
		other = frappe.get_doc({"doctype": "Wikify Agent Session", "user": "Guest", "scope": "global"})
		other.insert(ignore_permissions=True)
		with self.assertRaises(frappe.PermissionError):
			agent_api.archive_session(other.name)

	# --- no-op guard ---------------------------------------------------------------------

	def test_claims_unbacked_action_detection(self):
		self.assertTrue(claims_unbacked_action("Done — I've moved the Anaesthesia section."))
		self.assertTrue(claims_unbacked_action("I have retagged those pages for you."))
		self.assertFalse(claims_unbacked_action("The tree has Alpha and Beta."))
		self.assertFalse(claims_unbacked_action("I've reviewed the structure and it looks fine."))

	def _running_session(self, prompt="Move Alpha under Beta."):
		sess = session.get_or_create(
			None, user="Administrator", scope="document", source_document=self.sd.name
		)
		session.append_message(sess.name, "user", prompt, status="done")
		session.set_running(sess.name, True)
		return sess

	def test_no_op_guard_spends_one_corrective_round(self):
		"""A claimed-but-untooled action triggers exactly one corrective round."""
		sess = self._running_session()
		fake = FakeLLM(
			[
				[_text_chunk("Done — I've moved the section.")],  # claim, no tool
				[_text_chunk("Sorry, I actually haven't. Tell me the target.")],  # corrected
			]
		)
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()
		self.assertEqual(len(fake.calls), 2)
		# The corrective round was fed the nudge as the trailing user message.
		self.assertEqual(fake.calls[1]["messages"][-1]["role"], "user")
		self.assertIn("did not call any tool", fake.calls[1]["messages"][-1]["content"])

	def test_no_op_guard_fires_only_once(self):
		"""Two consecutive unbacked claims don't loop forever — only one correction."""
		sess = self._running_session()
		fake = FakeLLM(
			[
				[_text_chunk("I've moved it.")],
				[_text_chunk("I've moved it again.")],  # would need a 3rd stream if it re-corrected
			]
		)
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()  # no IndexError → no 3rd round
		self.assertEqual(len(fake.calls), 2)

	def test_real_tool_call_skips_the_guard(self):
		"""When a mutating tool actually runs, a past-tense claim is not second-guessed."""
		sess = self._running_session(prompt="Rename Alpha to Alpha Prime.")
		name = frappe.get_all("Source Section", filters={"source_document": self.sd.name}, limit=1)[0].name
		fake = FakeLLM(
			[
				[_tool_chunk(0, "c1", "rename_section", f'{{"name": "{name}", "title": "Alpha Prime"}}')],
				[_text_chunk("I've renamed it to Alpha Prime.")],  # backed by the tool → no correction
			]
		)
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()
		self.assertEqual(len(fake.calls), 2)
		self.assertEqual(frappe.db.get_value("Source Section", name, "title"), "Alpha Prime")

	# --- prompt-cache markers ------------------------------------------------------------

	def test_cache_marker_for_anthropic_only(self):
		runner = AgentRunner(sess_id := self._running_session().name, "Administrator")
		frappe.db.set_value("Wikify Agent Session", sess_id, "model", "anthropic/claude-sonnet-4.6")
		runner.model = "anthropic/claude-sonnet-4.6"
		marked = runner._cacheable("big block")
		self.assertIsInstance(marked, list)
		self.assertEqual(marked[0]["cache_control"], {"type": "ephemeral"})

		runner.model = "google/gemini-2.5-flash"
		self.assertEqual(runner._cacheable("big block"), "big block")
