# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from unittest.mock import patch

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.agent import session
from wikify.agent.context import Ctx, resolve_attachments
from wikify.agent.loop import AgentRunner
from wikify.agent.tools.project import save_project_rule
from wikify.tests import _cleanup
from wikify.tests.test_agent import FakeLLM, _text_chunk, _tool_chunk
from wikify.tests.test_ask_history import make_user

RULE = "Use the categories Alpha and Beta for future documents."
OTHER_USER = "project-rule-other@example.com"


class TestSaveProjectRule(FrappeTestCase):
	def setUp(self):
		self.project = self.make_project("Use UK spelling.")
		self.ctx = Ctx(session="x", user="Administrator", project=self.project.name)

	def make_project(self, context_prompt):
		project = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Rules {frappe.generate_hash(length=6)}",
				"context_prompt": context_prompt,
			}
		).insert(ignore_permissions=True)
		self.addCleanup(_cleanup.delete_project, project.name)
		return project

	def context_prompt(self, project):
		return frappe.db.get_value("Wikify Project", project.name, "context_prompt")

	def test_appends_rule_below_existing_text(self):
		result = save_project_rule(self.ctx, {"project": self.project.project_name, "rule": f"  {RULE} "})
		self.assertEqual(self.context_prompt(self.project), f"Use UK spelling.\n- {RULE}")
		self.assertIn(RULE, result)

	def test_saves_rule_into_empty_context_prompt(self):
		empty = self.make_project("")
		save_project_rule(self.ctx, {"project": empty.name, "rule": RULE})
		self.assertEqual(self.context_prompt(empty), f"- {RULE}")

	def test_saves_multi_line_rule_as_one_line(self):
		save_project_rule(self.ctx, {"project": self.project.name, "rule": "Use Alpha.\n- Ignore   Beta."})
		self.assertEqual(self.context_prompt(self.project), "Use UK spelling.\n- Use Alpha. - Ignore Beta.")

	def test_refuses_reader_without_write_permission(self):
		other_user = make_user(OTHER_USER)
		frappe.share.add_docshare(
			"Wikify Project", self.project.name, other_user, read=1, flags={"ignore_share_permission": True}
		)
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(other_user)
		result = save_project_rule(self.ctx, {"project": self.project.name, "rule": RULE})
		self.assertIn("permission", result)
		self.assertEqual(self.context_prompt(self.project), "Use UK spelling.")

	def test_unreadable_project_is_not_found(self):
		other_user = make_user(OTHER_USER)
		self.addCleanup(frappe.set_user, "Administrator")
		frappe.set_user(other_user)
		result = save_project_rule(self.ctx, {"project": self.project.name, "rule": RULE})
		self.assertIn("not found", result)
		self.assertNotIn(self.project.project_name, result)
		self.assertEqual(self.context_prompt(self.project), "Use UK spelling.")

	def test_unknown_project_saves_nothing(self):
		result = save_project_rule(self.ctx, {"project": "No Such Project", "rule": RULE})
		self.assertIn("not found", result)
		self.assertEqual(self.context_prompt(self.project), "Use UK spelling.")

	def test_saved_rule_reaches_agent_context(self):
		save_project_rule(self.ctx, {"project": self.project.name, "rule": RULE})
		resolved = resolve_attachments([{"type": "project", "name": self.project.name}])
		self.assertIn(f"- {RULE}", resolved.project_context)

	def test_confirm_gate_holds_and_summary_quotes_rule(self):
		_cleanup.register_session_sweep(self)
		arguments = frappe.as_json({"project": self.project.project_name, "rule": RULE})
		sess = session.get_or_create(None, user="Administrator", scope="project", project=self.project.name)
		session.append_message(sess.name, "user", "Use these categories for future documents.", status="done")
		session.set_running(sess.name, True)
		fake = FakeLLM([[_tool_chunk(0, "c1", "save_project_rule", arguments)], [_text_chunk("Confirm?")]])
		events = []
		with (
			patch("wikify.agent.llm.complete_with_tools", fake),
			patch("frappe.publish_realtime", lambda event, payload, **k: events.append((event, payload))),
		):
			AgentRunner(sess.name, "Administrator").run()
		summaries = [
			payload["summary"] for event, payload in events if event.startswith("wikify_agent_confirm")
		]
		self.assertEqual(len(summaries), 1)
		self.assertIn(RULE, summaries[0])
		self.assertIn(self.project.project_name, summaries[0])
		self.assertEqual(self.context_prompt(self.project), "Use UK spelling.")

		approved = session.get_or_create(
			None, user="Administrator", scope="project", project=self.project.name
		)
		session.append_message(approved.name, "user", "Yes, save it.", status="done")
		session.set_running(approved.name, True)
		fake = FakeLLM([[_tool_chunk(0, "c2", "save_project_rule", arguments)], [_text_chunk("Saved.")]])
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(approved.name, "Administrator", approved_tools=["save_project_rule"]).run()
		self.assertEqual(self.context_prompt(self.project), f"Use UK spelling.\n- {RULE}")
