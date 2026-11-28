# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from frappe.tests.utils import FrappeTestCase

from wikify.engine.verify.deterministic import extra_ratio, text_recall

PAGE_TEXT = "Date Remarks\nPD training given\nStart date\nEnd date"
HTML_TABLE = (
	"<table>\n  <tr><th>Date</th><th>Remarks</th></tr>\n"
	"  <tr><td><b>PD training given</b><br>• Start date<br>• End date</td></tr>\n</table>"
)


class TestTokens(FrappeTestCase):
	def test_html_table_tags_are_not_extra_words(self):
		self.assertEqual(extra_ratio(PAGE_TEXT, HTML_TABLE), 0.0)
		self.assertEqual(text_recall(PAGE_TEXT, HTML_TABLE), 1.0)

	def test_words_in_angle_brackets_are_still_counted(self):
		self.assertGreater(extra_ratio("dose", "dose < 5 mg and > 2 mg"), 0.0)
