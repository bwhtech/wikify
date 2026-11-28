# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from unittest.mock import MagicMock, patch

import requests
from frappe.tests.utils import FrappeTestCase

from wikify.engine import llm


def response(status, body=None, reason="Not Found"):
	resp = MagicMock(ok=status < 400, status_code=status, reason=reason)
	resp.json.side_effect = (lambda: body) if body is not None else ValueError
	return resp


class TestChatCompletionErrors(FrappeTestCase):
	def call(self, resp):
		with (
			patch("wikify.engine.llm.settings.openrouter_key", return_value="key"),
			patch("wikify.engine.llm.claude_cli.is_enabled", return_value=False),
			patch("wikify.engine.llm.requests.post", return_value=resp),
			self.assertRaises(requests.HTTPError) as caught,
		):
			llm.chat_completion("gone/model", [{"role": "user", "content": "hi"}])
		return str(caught.exception)

	def test_error_names_the_model_and_the_provider_message(self):
		body = {"error": {"message": "No endpoints found for gone/model.", "code": 404}}
		self.assertEqual(
			self.call(response(404, body)), "gone/model failed with 404: No endpoints found for gone/model."
		)

	def test_error_without_a_json_body_uses_the_reason(self):
		self.assertEqual(
			self.call(response(502, reason="Bad Gateway")), "gone/model failed with 502: Bad Gateway"
		)
