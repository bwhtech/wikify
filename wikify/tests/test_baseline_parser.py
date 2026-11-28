# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt
from frappe.tests.utils import FrappeTestCase

from wikify.engine.parsers.pymupdf import restore_cell_spaces

PAGE_TEXT = "angry with a person, at a thing\ndiscuss the plan (no about)\nBolder bold ital"


class TestRestoreCellSpaces(FrappeTestCase):
	def test_spaces_return_around_styled_spans(self):
		row = "|angry**with**a person,**at**a thing|discuss the plan (no_about_)|"
		self.assertEqual(
			restore_cell_spaces(row, PAGE_TEXT),
			"|angry **with** a person, **at** a thing|discuss the plan (no _about_)|",
		)

	def test_a_closing_and_opening_run_splits_between_them(self):
		self.assertEqual(restore_cell_spaces("|**bold**_ital_|", PAGE_TEXT), "|**bold** _ital_|")

	def test_style_change_inside_a_word_stays_joined(self):
		self.assertEqual(restore_cell_spaces("|**Bold**er|", PAGE_TEXT), "|**Bold**er|")

	def test_text_outside_tables_is_left_alone(self):
		self.assertEqual(restore_cell_spaces("angry**with**a", PAGE_TEXT), "angry**with**a")
