import os
import shutil
import time

import frappe

from wikify.agent import session
from wikify.agent.loop import AgentRunner
from wikify.engine import claude_cli


def environment():
	print("which claude:", shutil.which("claude"), "| HOME:", os.environ.get("HOME"))
	print("token set:", bool(os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")), "| is_enabled:", claude_cli.is_enabled())
	try:
		print("plain run:", claude_cli.run([{"role": "user", "content": "Reply with exactly: ok"}])["result"])
	except Exception as exception:
		print("plain run FAILED:", repr(exception))


def start_turn():
	sess = session.get_or_create(None, user="Administrator", scope="global")
	session.append_message(sess.name, "user", "List my projects", status="done", attachments=[])
	session.set_running(sess.name, True)
	frappe.db.commit()
	return sess.name


def report(session_id, started):
	print("turn took", round(time.time() - started, 1), "s")
	for row in frappe.get_all(
		"Wikify Agent Message",
		filters={"session": session_id},
		fields=["role", "status", "content", "tool_name"],
		order_by="creation asc",
	):
		print(f"  {row.role:9} {row.status or '':9} {row.tool_name or '':12} {(row.content or '')[:160]!r}")
	for error in frappe.get_all(
		"Error Log", filters={"method": "Wikify agent run failed"}, fields=["error"], order_by="creation desc"
	):
		print(error.error[-3000:])


def direct():
	frappe.set_user("Administrator")
	environment()
	session_id = start_turn()
	started = time.time()
	AgentRunner(session_id, "Administrator", attachments=[]).run()
	report(session_id, started)


def worker():
	frappe.set_user("Administrator")
	frappe.db.delete("Error Log", {"method": "Wikify agent run failed"})
	frappe.enqueue("wikify.agent_ci_probe.environment", queue="long")
	session_id = start_turn()
	started = time.time()
	frappe.enqueue(
		"wikify.jobs.agent.run_agent_job", queue="long", session_id=session_id, user="Administrator"
	)
	frappe.db.commit()
	while time.time() - started < 300:
		time.sleep(5)
		frappe.db.rollback()
		if not frappe.db.get_value("Wikify Agent Session", session_id, "is_running"):
			break
	report(session_id, started)
