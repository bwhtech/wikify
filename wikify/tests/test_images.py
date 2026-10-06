# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

import tempfile
from pathlib import Path

import fitz
from frappe.tests.utils import FrappeTestCase

from wikify.engine import images

CHART_RECT = fitz.Rect(100, 300, 400, 500)
TEXT_BEFORE = "Admissions rose steadily over the decade."
TEXT_AFTER = "Discharge planning starts on the first day."


def solid_pixmap(width: int, height: int, colour: tuple[int, int, int]) -> fitz.Pixmap:
	pixmap = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, width, height), False)
	pixmap.set_rect(pixmap.irect, colour)
	return pixmap


def make_report_pdf(path: str) -> None:
	logo = solid_pixmap(80, 80, (20, 40, 160))
	chart = solid_pixmap(600, 400, (200, 60, 30))
	document = fitz.open()
	for page_no in range(1, 4):
		page = document.new_page(width=612, height=792)
		page.insert_image(fitz.Rect(72, 100, 152, 180), pixmap=logo)
		page.insert_text((72, 230), f"Annual report section {page_no}", fontsize=14)
		if page_no == 2:
			page.insert_text((100, 280), TEXT_BEFORE, fontsize=11)
			page.insert_image(CHART_RECT, pixmap=chart)
			page.insert_text((100, 540), TEXT_AFTER, fontsize=11)
		else:
			page.insert_text((72, 280), "Plain text page with no pictures at all.", fontsize=11)
	document.save(path)
	document.close()


def chart_figure(**values) -> images.Figure:
	return images.Figure(
		bbox=tuple(CHART_RECT),
		page_size=(612.0, 792.0),
		text_before=TEXT_BEFORE,
		text_after=TEXT_AFTER,
		url="/private/files/chart.png",
		**values,
	)


class TestFigureDetection(FrappeTestCase):
	def test_finds_the_body_chart_and_skips_the_logo_repeated_on_every_page(self):
		path = Path(tempfile.mkdtemp()) / "report.pdf"
		make_report_pdf(str(path))
		with fitz.open(str(path)) as document:
			repeated = images.repeated_images(document)
			found = [images.find_figures(page, repeated) for page in document]

		self.assertEqual([len(figures) for figures in found], [0, 1, 0])
		chart = found[1][0]
		self.assertEqual(fitz.Rect(chart.bbox), CHART_RECT)
		self.assertEqual(chart.text_before, TEXT_BEFORE)
		self.assertEqual(chart.text_after, TEXT_AFTER)


class TestFigurePlacement(FrappeTestCase):
	def test_places_the_figure_after_the_text_that_precedes_it(self):
		markdown = f"# Annual report\n\n{TEXT_BEFORE}\n\n## Discharge\n\n{TEXT_AFTER}"

		placed = images.place_figures(markdown, [chart_figure()], 2)

		self.assertEqual(
			placed,
			f"# Annual report\n\n{TEXT_BEFORE}\n\n![Figure 2.1](/private/files/chart.png)\n\n"
			f"## Discharge\n\n{TEXT_AFTER}",
		)

	def test_a_figure_after_a_table_row_lands_below_the_whole_table(self):
		markdown = f"<table>\n<tr><td>{TEXT_BEFORE}</td></tr>\n<tr><td>Second row</td></tr>\n</table>\n\nClosing note."

		placed = images.place_figures(markdown, [chart_figure()], 2)

		self.assertTrue(placed.index("![Figure 2.1]") > placed.index("</table>"))
		self.assertTrue(placed.index("![Figure 2.1]") < placed.index("Closing note."))

	def test_swaps_the_vlm_token_for_the_crop_titled_by_the_vlm(self):
		markdown = f"{TEXT_BEFORE}\n\n[[FIGURE 1: Admissions per year]]\n\n[[FIGURE 2]]\n\n{TEXT_AFTER}"

		placed = images.place_figures(markdown, [chart_figure()], 2)

		self.assertEqual(
			placed, f"{TEXT_BEFORE}\n\n![Admissions per year](/private/files/chart.png)\n\n{TEXT_AFTER}"
		)

	def test_a_printed_caption_wins_over_the_vlm_title(self):
		placed = images.place_figures(
			"[[FIGURE 1: Bar chart]]", [chart_figure(caption="Figure 3: Admissions")], 2
		)

		self.assertEqual(placed, "![Figure 3: Admissions](/private/files/chart.png)")

	def test_inserts_a_figure_the_vlm_left_out_after_its_preceding_text(self):
		markdown = f"## Admissions\n\n{TEXT_BEFORE}\n\n{TEXT_AFTER}"

		placed = images.place_figures(markdown, [chart_figure()], 2)

		self.assertEqual(
			placed,
			f"## Admissions\n\n{TEXT_BEFORE}\n\n![Figure 2.1](/private/files/chart.png)\n\n{TEXT_AFTER}",
		)

	def test_a_figure_already_in_the_markdown_is_not_added_twice(self):
		markdown = f"{TEXT_BEFORE}\n\n![Admissions per year](/private/files/chart.png)\n\n{TEXT_AFTER}"

		self.assertEqual(images.place_figures(markdown, [chart_figure()], 2), markdown)
