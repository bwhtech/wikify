# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from types import SimpleNamespace
from unittest.mock import patch

import frappe
import litellm
from frappe.tests.utils import FrappeTestCase
from frappe.utils import now_datetime

from wikify.agent import session
from wikify.agent.context import Ctx, resolve_attachments
from wikify.agent.loop import AgentRunner, cancel_key, request_cancel
from wikify.agent.tools.read import (
	_list_section_types,
	_read_page,
	_read_section,
	_read_tree,
	_search_sections,
	read_history,
)
from wikify.api import agent as agent_api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.tests import _cleanup


def _sec(title, level, path, p_start, p_end):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=f"body of {title}",
	)


def _system_text(content):
	if isinstance(content, list):
		return "".join(part.get("text", "") for part in content)
	return content or ""


def _text_chunk(text):
	return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=text, tool_calls=None))])


def _tool_chunk(index, call_id, name, arguments):
	tc = SimpleNamespace(
		index=index,
		id=call_id,
		function=SimpleNamespace(name=name, arguments=arguments),
	)
	return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=None, tool_calls=[tc]))])


class FakeLLM:
	def __init__(self, streams):
		self.streams = list(streams)
		self.calls = []

	def __call__(self, model, messages, tools, *, stream=True):
		self.calls.append({"model": model, "messages": list(messages)})
		return iter(self.streams.pop(0))


class TestAgent(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Agent Test"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(_cleanup.delete_document, self.sd.name)
		_cleanup.register_session_sweep(self)
		store.replace_sections(
			self.sd.name,
			[
				_sec("1. Alpha", 1, ["1. Alpha"], 1, 2),
				_sec("1.1 Alpha-One", 2, ["1. Alpha", "1.1 Alpha-One"], 1, 1),
				_sec("2. Beta", 1, ["2. Beta"], 3, 3),
			],
		)

	def test_read_tree_renders_hierarchy(self):
		ctx = Ctx(session="x", user="Administrator", source_document=self.sd.name)
		out = _read_tree(ctx, {})
		self.assertIn("1. Alpha", out)
		self.assertIn("  1.1 Alpha-One".strip(), out)
		self.assertIn("[p.1-2]", out)

	def test_read_tree_without_document_asks(self):
		ctx = Ctx(session="x", user="Administrator")
		out = _read_tree(ctx, {})
		self.assertIn("No document", out)

	def _make_session(self):
		sess = session.get_or_create(
			None, user="Administrator", scope="document", source_document=self.sd.name
		)
		session.append_message(sess.name, "user", "Summarize the tree.", status="done")
		session.set_running(sess.name, True)
		return sess

	def test_loop_calls_tool_then_answers(self):
		sess = self._make_session()
		fake = FakeLLM(
			[
				[_tool_chunk(0, "call_1", "read_tree", "{}")],
				[_text_chunk("The tree has "), _text_chunk("Alpha and Beta.")],
			]
		)
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()

		msgs = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": sess.name},
			fields=["role", "status", "tool_name", "content", "tool_calls"],
			order_by="creation asc",
		)
		roles = [m.role for m in msgs]
		self.assertEqual(roles, ["user", "assistant", "tool", "assistant"])

		self.assertIn("read_tree", msgs[1].tool_calls)
		self.assertEqual(msgs[2].tool_name, "read_tree")
		self.assertIn("Alpha", msgs[2].content)
		self.assertEqual(msgs[3].content, "The tree has Alpha and Beta.")
		self.assertEqual(msgs[3].status, "done")

		self.assertEqual(frappe.db.get_value("Wikify Agent Session", sess.name, "is_running"), 0)
		self.assertEqual(len(fake.calls), 2)
		self.assertEqual(fake.calls[1]["messages"][-1]["role"], "tool")

	def test_loop_clears_old_tool_results_within_a_turn(self):
		sess = self._make_session()
		tree = _read_tree(Ctx(session=sess.name, user="Administrator", source_document=self.sd.name), {})
		fake = FakeLLM(
			[
				[_tool_chunk(0, "call_1", "read_tree", "{}")],
				[_tool_chunk(0, "call_2", "read_tree", "{}")],
				[_text_chunk("Done.")],
			]
		)
		with (
			patch.multiple(session, TOOL_RESULT_BUDGET_CHARS=len(tree) + 10, TOOL_RESULT_CLEAR_STEP_CHARS=1),
			patch("wikify.agent.llm.complete_with_tools", fake),
		):
			AgentRunner(sess.name, "Administrator").run()

		second_round, third_round = (
			[message["content"] for message in call["messages"] if message["role"] == "tool"]
			for call in fake.calls[1:]
		)
		self.assertEqual(second_round, [tree])
		self.assertEqual(third_round, [session.cleared_result_note("call_1"), tree])

	def test_loop_streams_realtime(self):
		sess = self._make_session()
		fake = FakeLLM([[_text_chunk("hi "), _text_chunk("there")]])
		events = []
		with (
			patch("wikify.agent.llm.complete_with_tools", fake),
			patch("frappe.publish_realtime", lambda event, *a, **k: events.append(event)),
		):
			AgentRunner(sess.name, "Administrator").run()
		self.assertTrue(any(e.startswith("wikify_agent_stream") for e in events))
		self.assertTrue(any(e.startswith("wikify_agent_complete") for e in events))

	def test_cancel_stops_mid_stream(self):
		sess = self._make_session()

		def cancelling_stream():
			request_cancel(sess.name)
			yield _text_chunk("should not finish")

		fake = FakeLLM([cancelling_stream()])
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()
		answers = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": sess.name, "role": "assistant"},
			pluck="content",
		)
		self.assertNotIn("should not finish", answers)
		self.assertFalse(frappe.cache().get_value(cancel_key(sess.name)))

	def test_cancel_from_another_process_stops_the_tool_loop(self):
		sess = self._make_session()

		def tool_round_then_cancel():
			yield _tool_chunk(0, "call_1", "read_tree", "{}")
			with patch.dict(frappe.local.cache):
				request_cancel(sess.name)

		fake = FakeLLM(
			[tool_round_then_cancel()]
			+ [[_tool_chunk(0, f"call_{i}", "read_tree", "{}")] for i in range(2, 30)]
		)
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()

		self.assertEqual(len(fake.calls), 1)
		tool_rows = frappe.get_all("Wikify Agent Message", filters={"session": sess.name, "role": "tool"})
		self.assertEqual(tool_rows, [])
		self.assertEqual(frappe.db.get_value("Wikify Agent Session", sess.name, "is_running"), 0)

	def test_run_rejects_when_already_running(self):
		sess = session.get_or_create(None, user="Administrator", scope="global")
		session.set_running(sess.name, True)
		with patch("frappe.enqueue"), self.assertRaises(frappe.ValidationError):
			agent_api.run(prompt="hello", session_id=sess.name)

	def test_run_enqueues_and_returns_ids(self):
		with patch("frappe.enqueue") as enq:
			result = agent_api.run(prompt="hello", scope="document", source_document=self.sd.name)
		self.assertIn("session_id", result)
		self.assertIn("message_id", result)
		enq.assert_called_once()
		self.assertEqual(frappe.db.get_value("Wikify Agent Session", result["session_id"], "is_running"), 1)

	def _first_section(self):
		return frappe.get_all(
			"Source Section", filters={"source_document": self.sd.name}, order_by="lft asc", limit=1
		)[0].name

	def test_read_section_returns_body_and_meta(self):
		name = self._first_section()
		out = _read_section(Ctx(session="x", user="Administrator"), {"name": name})
		self.assertIn("1. Alpha", out)
		self.assertIn("body of 1. Alpha", out)

	def test_read_page_uses_attached_document(self):
		frappe.get_doc(
			{
				"doctype": "Source Page",
				"source_document": self.sd.name,
				"page_no": 1,
				"verdict": "pass",
				"canonical_markdown": "Canonical page one body.",
			}
		).insert(ignore_permissions=True)
		ctx = Ctx(session="x", user="Administrator", source_document=self.sd.name)
		out = _read_page(ctx, {"page_no": 1})
		self.assertIn("Canonical page one body.", out)
		self.assertIn("Page 1", out)

	def _make_type(self, **kwargs):
		tname = f"t_{frappe.generate_hash(length=6)}"
		kwargs.setdefault("label", f"Test Type {tname}")
		doc = frappe.get_doc({"doctype": "Section Type", "type_name": tname, **kwargs}).insert(
			ignore_permissions=True
		)
		self.addCleanup(frappe.db.delete, "Section Type", {"name": tname})
		return doc

	def test_list_section_types_lists_taxonomy(self):
		st = self._make_type()
		out = _list_section_types(Ctx(session="x", user="Administrator"), {})
		self.assertIn(st.type_name, out)

	def test_search_sections_by_type_spans_documents(self):
		st = self._make_type()
		name = self._first_section()
		frappe.db.set_value("Source Section", name, "section_type", st.type_name)
		out = _search_sections(Ctx(session="x", user="Administrator"), {"section_type": st.type_name})
		self.assertIn("1. Alpha", out)
		self.assertIn(self.sd.name, out)

	def test_explicit_bad_document_falls_back_to_attached(self):
		ctx = Ctx(session="x", user="Administrator", source_document=self.sd.name)
		out = _read_tree(ctx, {"source_document": f"Agent Test ({self.sd.name})"})
		self.assertIn("1. Alpha", out)

	def test_resolve_document_attachment_sets_scope_and_block(self):
		resolved = resolve_attachments([{"type": "document", "name": self.sd.name}])
		self.assertEqual(resolved.source_document, self.sd.name)
		self.assertIn("1. Alpha", resolved.block)

	def test_resolve_section_attachment_pins_its_document(self):
		name = self._first_section()
		resolved = resolve_attachments([{"type": "section", "name": name}])
		self.assertEqual(resolved.source_document, self.sd.name)
		self.assertIn("body of 1. Alpha", resolved.block)

	def test_resolve_wiki_view_section_adds_framing_line(self):
		name = self._first_section()
		wiki = resolve_attachments([{"type": "section", "name": name, "view": "wiki"}])
		self.assertIn("rendered wiki page", wiki.block)
		plain = resolve_attachments([{"type": "section", "name": name}])
		self.assertNotIn("rendered wiki page", plain.block)

	def test_resolve_project_attachment_injects_context_prompt(self):
		proj = frappe.get_doc(
			{
				"doctype": "Wikify Project",
				"project_name": f"Proj {frappe.generate_hash(length=6)}",
				"context_prompt": "Use UK spelling.",
			}
		).insert(ignore_permissions=True)
		self.addCleanup(_cleanup.delete_project, proj.name)
		resolved = resolve_attachments([{"type": "project", "name": proj.name}])
		self.assertEqual(resolved.project, proj.name)
		self.assertEqual(resolved.project_context, "Use UK spelling.")

	def test_stale_attachment_is_skipped(self):
		resolved = resolve_attachments([{"type": "document", "name": "does-not-exist"}])
		self.assertEqual(resolved.block, "")

	def test_loop_prepends_attachment_block(self):
		sess = session.get_or_create(None, user="Administrator", scope="global")
		session.append_message(sess.name, "user", "What's in this doc?", status="done")
		session.set_running(sess.name, True)
		fake = FakeLLM([[_text_chunk("It has Alpha and Beta.")]])
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(
				sess.name, "Administrator", attachments=[{"type": "document", "name": self.sd.name}]
			).run()
		systems = [_system_text(m["content"]) for m in fake.calls[0]["messages"] if m["role"] == "system"]
		self.assertTrue(any("1. Alpha" in s for s in systems))

	def test_list_and_new_session(self):
		created = agent_api.new_session(scope="document", source_document=self.sd.name)
		self.assertIn("session_id", created)
		frappe.db.set_value("Wikify Agent Session", created["session_id"], "title", "My chat")
		listed = agent_api.list_sessions()
		self.assertTrue(any(s["name"] == created["session_id"] for s in listed))

	def run_failing_turn(self, exception):
		agent_session = self._make_session()
		started = now_datetime()
		events = []

		def failing_llm(model, messages, tools, *, stream=True):
			raise exception

		with (
			patch("wikify.agent.llm.complete_with_tools", failing_llm),
			patch(
				"frappe.publish_realtime", lambda event, payload, **kwargs: events.append((event, payload))
			),
			patch("frappe.db.commit"),
		):
			AgentRunner(agent_session.name, "Administrator").run()
		messages = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": agent_session.name, "role": "assistant"},
			fields=["content", "status"],
		)
		error_logs = frappe.get_all(
			"Error Log",
			filters={"method": "Wikify agent run failed", "creation": (">=", started)},
			pluck="name",
		)
		errors = [payload["message"] for event, payload in events if event.startswith("wikify_agent_error")]
		return messages, error_logs, errors

	def test_insufficient_credits_names_the_cause(self):
		exception = litellm.APIError(
			status_code=402,
			message='OpenrouterException - {"error": {"message": "This request requires more credits"}}',
			llm_provider="openrouter",
			model="openrouter/anthropic/claude-sonnet-4.6",
		)
		messages, error_logs, errors = self.run_failing_turn(exception)
		self.assertEqual([message.status for message in messages], ["error"])
		self.assertIn("credits", messages[0].content)
		self.assertEqual(errors, [messages[0].content])
		self.assertEqual(len(error_logs), 1)

	def test_failed_run_logs_error_without_streaming_row(self):
		messages, error_logs, errors = self.run_failing_turn(RuntimeError("model call failed"))
		self.assertEqual([message.status for message in messages], ["error"])
		self.assertEqual(errors, [messages[0].content])
		self.assertEqual(len(error_logs), 1)


class TestSearchSectionsRecall(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Recall Test"}).insert(
			ignore_permissions=True
		)
		self.addCleanup(_cleanup.delete_document, self.sd.name)
		_cleanup.register_session_sweep(self)
		self.section_type = self._make_type(
			label=f"Staff Roles {frappe.generate_hash(length=6)}",
			description="Job descriptions and role profiles — one section per post.",
		)
		titles = [
			"Job Description — Ward Sister",
			"Job Description — Staff Nurse (Band 5)",
			"Job Description — Healthcare Assistant",
		]
		store.replace_sections(
			self.sd.name,
			[_sec("Roles and Responsibilities", 1, ["Roles and Responsibilities"], 1, 1)]
			+ [
				_sec(title, 2, ["Roles and Responsibilities", title], page, page)
				for page, title in enumerate(titles, start=2)
			],
		)
		for row in frappe.get_all(
			"Source Section", filters={"source_document": self.sd.name, "level": 2}, pluck="name"
		):
			frappe.db.set_value("Source Section", row, "section_type", self.section_type.type_name)

	def _make_type(self, **kwargs):
		type_name = f"t_{frappe.generate_hash(length=6)}"
		kwargs.setdefault("label", f"Test Type {type_name}")
		doc = frappe.get_doc({"doctype": "Section Type", "type_name": type_name, **kwargs}).insert(
			ignore_permissions=True
		)
		self.addCleanup(_cleanup.delete_section_type, type_name)
		return doc

	def _search(self, **args):
		return _search_sections(Ctx(session="x", user="Administrator"), args)

	def test_plural_query_matches_singular_titles(self):
		out = self._search(section_type=self.section_type.type_name, query="job descriptions")
		self.assertIn("Ward Sister", out)
		self.assertIn("Staff Nurse", out)
		self.assertIn("Healthcare Assistant", out)
		self.assertNotIn("no sections", out.lower())

	def test_unmatched_query_returns_everything_and_says_so(self):
		out = self._search(section_type=self.section_type.type_name, query="aardvark husbandry")
		self.assertIn("IGNORED", out)
		self.assertIn("3 section(s)", out)
		self.assertIn("Ward Sister", out)

	def test_empty_type_reads_as_empty_not_as_a_filter_miss(self):
		empty_type = self._make_type()
		out = self._search(section_type=empty_type.type_name, source_document=self.sd.name)
		self.assertIn("no sections in", out)
		self.assertIn(self.section_type.type_name, out)

	def test_project_title_resolves_to_its_id(self):
		project = frappe.get_doc(
			{"doctype": "Wikify Project", "project_name": f"Recall Corpus {frappe.generate_hash(length=6)}"}
		).insert(ignore_permissions=True)
		self.addCleanup(_cleanup.delete_project, project.name)
		frappe.db.set_value("Source Document", self.sd.name, "project", project.name)

		out = self._search(section_type=self.section_type.type_name, project=project.project_name)
		self.assertIn(project.name, out)
		self.assertIn("Ward Sister", out)

	def test_unresolvable_project_says_the_scope_was_dropped(self):
		out = self._search(section_type=self.section_type.type_name, project="No Such Corpus")
		self.assertIn("matches no project", out)
		self.assertIn("Ward Sister", out)

	def test_unknown_type_names_near_matches(self):
		out = self._search(section_type="job_description")
		self.assertIn("not a Section Type", out)
		self.assertIn(self.section_type.type_name, out)

	def test_agent_loop_sees_the_count_for_the_failing_question(self):
		sess = session.get_or_create(None, user="Administrator", scope="global")
		session.append_message(
			sess.name, "user", "How many job descriptions are in this project?", status="done"
		)
		session.set_running(sess.name, True)
		fake = FakeLLM(
			[
				[
					_tool_chunk(
						0,
						"call_1",
						"search_sections",
						'{"section_type": "%s", "query": "job descriptions"}' % self.section_type.type_name,
					)
				],
				[_text_chunk("There are 3 job descriptions.")],
			]
		)
		with patch("wikify.agent.llm.complete_with_tools", fake):
			AgentRunner(sess.name, "Administrator").run()
		tool_result = frappe.get_all(
			"Wikify Agent Message",
			filters={"session": sess.name, "role": "tool"},
			pluck="content",
		)[0]
		self.assertIn("3 of 3 section(s)", tool_result)
		self.assertIn("Ward Sister", tool_result)


class TestAgentHistoryWindow(FrappeTestCase):
	def setUp(self):
		_cleanup.register_session_sweep(self)
		self.session = session.get_or_create(None, user="Administrator").name

	def test_history_keeps_every_conversation_message(self):
		for index in range(45):
			session.append_message(self.session, "user", f"turn {index}")

		messages = session.history_messages(self.session)

		self.assertEqual(
			[message["content"] for message in messages], [f"turn {index}" for index in range(45)]
		)

	def add_tool_turn(self, prompt, results):
		session.append_message(self.session, "user", prompt)
		call_ids = [f"call_{prompt}_{index}" for index in range(len(results))]
		session.append_message(
			self.session,
			"assistant",
			"",
			tool_calls=[{"id": call_id, "name": "read_section", "args": {}} for call_id in call_ids],
		)
		for call_id, result in zip(call_ids, results, strict=True):
			session.append_message(
				self.session, "tool", result, tool_name="read_section", tool_call_id=call_id
			)
		session.append_message(self.session, "assistant", f"finished {prompt}")
		return call_ids

	def tool_contents(self):
		messages = session.history_messages(self.session)
		requested_ids = {call["id"] for message in messages for call in message.get("tool_calls", [])}
		tool_messages = [message for message in messages if message["role"] == "tool"]
		self.assertTrue(all(message["tool_call_id"] in requested_ids for message in tool_messages))
		return [message["content"] for message in tool_messages]

	def test_window_keeps_the_conversation_before_a_tool_heavy_turn(self):
		session.append_message(self.session, "user", "re-parse page 18 keeping the table")
		session.append_message(self.session, "assistant", "Re-parsed page 18.")
		self.add_tool_turn("read every section", ["section body"] * 50)
		session.append_message(self.session, "user", "what did I ask before this?")

		messages = session.history_messages(self.session)
		contents = [message["content"] for message in messages]

		self.assertEqual(messages[0], {"role": "user", "content": "re-parse page 18 keeping the table"})
		self.assertIn("Re-parsed page 18.", contents)
		self.assertEqual(contents[-1], "what did I ask before this?")
		self.assertEqual(self.tool_contents(), ["section body"] * 50)

	@patch.multiple(session, TOOL_RESULT_BUDGET_CHARS=1000, TOOL_RESULT_CLEAR_STEP_CHARS=500)
	def test_oldest_tool_results_over_the_budget_are_cleared(self):
		call_ids = self.add_tool_turn("read six sections", ["x" * 300] * 6)

		contents = self.tool_contents()

		self.assertEqual(contents[:4], [session.cleared_result_note(call_id) for call_id in call_ids[:4]])
		self.assertEqual(contents[4:], ["x" * 300] * 2)

	@patch.multiple(session, TOOL_RESULT_BUDGET_CHARS=1000, TOOL_RESULT_CLEAR_STEP_CHARS=500)
	def test_cleared_results_change_only_when_a_step_fills(self):
		call_ids = self.add_tool_turn("first", ["x" * 300] * 6)
		self.add_tool_turn("second", ["y" * 150])
		after_small_result = self.tool_contents()
		self.add_tool_turn("third", ["z" * 100])
		after_step_filled = self.tool_contents()

		self.assertEqual(after_small_result[4], "x" * 300)
		self.assertEqual(after_step_filled[4], session.cleared_result_note(call_ids[4]))
		self.assertEqual(after_step_filled[:4], after_small_result[:4])

	@patch.multiple(session, TOOL_RESULT_BUDGET_CHARS=1000, TOOL_RESULT_CLEAR_STEP_CHARS=500)
	def test_read_history_returns_a_cleared_result(self):
		call_ids = self.add_tool_turn(
			"read six sections", [f"body of section {index} " + "x" * 300 for index in range(6)]
		)
		ctx = Ctx(session=self.session, user="Administrator")

		self.assertEqual(self.tool_contents()[0], session.cleared_result_note(call_ids[0]))
		self.assertIn("body of section 0", read_history(ctx, {"call_id": call_ids[0]}))
		self.assertIn(f"call_id {call_ids[2]}", read_history(ctx, {"query": "body of section 2"}))

	def test_read_history_stays_inside_the_session(self):
		other_session = session.get_or_create(None, user="Administrator").name
		session.append_message(other_session, "user", "the other conversation")

		found = read_history(Ctx(session=self.session, user="Administrator"), {"query": "other conversation"})

		self.assertEqual(found, "No earlier message in this conversation matches.")
