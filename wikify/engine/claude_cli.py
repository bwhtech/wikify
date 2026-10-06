from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from types import SimpleNamespace

import frappe

from wikify.engine import settings

MODEL = "sonnet"
MIN_TIMEOUT = 300
ANY_OBJECT_SCHEMA = {"type": "object"}
TOOL_PROTOCOL = (
	"\n\nThe application runs these tools for you:\n{tools}\n\n"
	"You cannot invoke them yourself; any direct tool call fails. To use one, request it in your "
	"structured reply: `tool_calls` lists the tools to run now as {{name, arguments}}, and `text` is "
	"what you say to the user. The application runs them and sends the results back in the "
	"transcript. Leave `tool_calls` empty only when you are done."
)
NATIVE_CALL_RETRY = (
	"\n\nYour previous attempt called these tools directly and every call failed. Request each tool "
	"you need in `tool_calls` instead."
)


def is_enabled() -> bool:
	return (
		bool(frappe.conf.get("developer_mode"))
		and bool(settings.get("use_local_models"))
		and not frappe.flags.in_test
	)


def chat_completion(messages: list, response_format: dict | None = None, timeout: int = 120) -> dict:
	result = run(messages, schema=ANY_OBJECT_SCHEMA if response_format else None, timeout=timeout)
	content = json.dumps(result["structured_output"]) if response_format else result["result"]
	return {
		"choices": [{"message": {"role": "assistant", "content": content}}],
		"usage": read_usage(result),
	}


def complete_with_tools(messages: list, tools: list, *, stream: bool = True):
	if not tools:
		text = run(messages)["result"]
		if not stream:
			return SimpleNamespace(
				choices=[SimpleNamespace(message=SimpleNamespace(content=text))], usage=None
			)
		return iter([stream_chunk(text, [])])

	tool_specs = [{"name": t.name, "description": t.description, "parameters": t.parameters} for t in tools]
	schema = {
		"type": "object",
		"properties": {
			"text": {"type": "string"},
			"tool_calls": {
				"type": "array",
				"items": {
					"type": "object",
					"properties": {
						"name": {"type": "string", "enum": [t.name for t in tools]},
						"arguments": {"type": "object"},
					},
					"required": ["name", "arguments"],
				},
			},
		},
		"required": ["text", "tool_calls"],
	}
	protocol = TOOL_PROTOCOL.format(tools=json.dumps(tool_specs, indent=1))
	result = run(messages, schema=schema, system_suffix=protocol)
	if result["native_tool_calls"]:
		result = run(messages, schema=schema, system_suffix=protocol + NATIVE_CALL_RETRY)
	reply = result["structured_output"]
	return iter([stream_chunk(reply.get("text") or "", reply.get("tool_calls") or [])])


def stream_chunk(text: str, tool_calls: list) -> SimpleNamespace:
	deltas = [
		SimpleNamespace(
			index=index,
			id=f"call_{frappe.generate_hash(length=12)}",
			function=SimpleNamespace(name=call["name"], arguments=json.dumps(call.get("arguments") or {})),
		)
		for index, call in enumerate(tool_calls)
	]
	delta = SimpleNamespace(content=text, tool_calls=deltas)
	return SimpleNamespace(choices=[SimpleNamespace(delta=delta)], usage=None)


def run(messages: list, schema: dict | None = None, system_suffix: str = "", timeout: int = 120) -> dict:
	system = "\n\n".join(text_of(message["content"]) for message in messages if message["role"] == "system")
	conversation = [message for message in messages if message["role"] != "system"]
	user_message = {"type": "user", "message": {"role": "user", "content": user_content(conversation)}}

	command = [
		shutil.which("claude") or os.path.expanduser("~/.local/bin/claude"),
		"-p",
		"--model",
		MODEL,
		"--tools",
		"",
		"--strict-mcp-config",
		"--setting-sources",
		"",
		"--no-session-persistence",
		"--system-prompt",
		(system + system_suffix) or "You are a helpful assistant.",
		"--input-format",
		"stream-json",
		"--output-format",
		"stream-json",
		"--verbose",
	]
	if schema:
		command += ["--json-schema", json.dumps(schema)]

	# Without this the CLI bills the API key instead of the logged-in subscription.
	env = {key: value for key, value in os.environ.items() if key != "ANTHROPIC_API_KEY"}
	completed = subprocess.run(
		command,
		input=json.dumps(user_message),
		capture_output=True,
		text=True,
		timeout=max(timeout, MIN_TIMEOUT),
		cwd=tempfile.gettempdir(),
		env=env,
	)
	events = [parse_event(line) for line in completed.stdout.splitlines()]
	results = [event for event in events if event.get("type") == "result"]
	result = results[-1] if results else None
	if not result or result.get("is_error"):
		detail = (result or {}).get("result") or completed.stderr.strip() or completed.stdout[-500:]
		raise RuntimeError(f"claude CLI failed: {detail}")
	# Sonnet sometimes calls the app's tools natively; the CLI rejects them and the reply asks for none.
	result["native_tool_calls"] = [
		block.get("name")
		for event in events
		if event.get("type") == "assistant"
		for block in event.get("message", {}).get("content") or []
		if block.get("type") == "tool_use" and block.get("name") != "StructuredOutput"
	]
	return result


def parse_event(line: str) -> dict:
	try:
		return json.loads(line)
	except ValueError:
		return {}


def user_content(conversation: list) -> list:
	if len(conversation) == 1 and conversation[0]["role"] == "user":
		return content_blocks(conversation[0]["content"])

	transcript = []
	images = []
	for message in conversation:
		role = message["role"]
		if role == "tool":
			transcript.append(f"TOOL RESULT ({message.get('tool_call_id')}):\n{message['content']}")
			continue
		blocks = content_blocks(message.get("content"))
		images += [block for block in blocks if block["type"] == "image"]
		text = "\n".join(block["text"] for block in blocks if block["type"] == "text")
		if text:
			transcript.append(f"{role.upper()}:\n{text}")
		for call in message.get("tool_calls") or []:
			function = call["function"]
			transcript.append(f"ASSISTANT CALLED {function['name']} ({call['id']}): {function['arguments']}")
	transcript.append("Continue as the ASSISTANT from here.")
	return [*images, {"type": "text", "text": "\n\n".join(transcript)}]


def content_blocks(content) -> list:
	if not content:
		return []
	if isinstance(content, str):
		return [{"type": "text", "text": content}]
	blocks = []
	for part in content:
		if part["type"] == "text":
			blocks.append({"type": "text", "text": part["text"]})
		elif part["type"] == "image_url":
			header, data = part["image_url"]["url"].split(",", 1)
			media_type = header.removeprefix("data:").split(";")[0]
			blocks.append(
				{"type": "image", "source": {"type": "base64", "media_type": media_type, "data": data}}
			)
	return blocks


def text_of(content) -> str:
	return "\n".join(block["text"] for block in content_blocks(content) if block["type"] == "text")


def read_usage(result: dict) -> dict:
	usage = result.get("usage") or {}
	return {
		"prompt_tokens": sum(
			usage.get(key) or 0
			for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
		),
		"completion_tokens": usage.get("output_tokens"),
		"cost": 0,
	}
