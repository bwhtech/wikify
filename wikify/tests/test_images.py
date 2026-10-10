# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

import tempfile
from pathlib import Path
from unittest.mock import patch

import fitz
import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.engine import images, parse_pdf, remediate_pdf
from wikify.engine import settings as engine_settings
from wikify.engine.loader.cleanup import drop_transcribed_figures
from wikify.tests import _cleanup
from wikify.tests.test_remediate_pipeline import _fake_chat

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

	def test_the_crop_leaves_out_a_title_line_its_padding_would_cut_in_half(self):
		document = fitz.open()
		page = document.new_page(width=612, height=792)
		page.insert_text(
			(100, CHART_RECT.y0 - 2), "DOSES OF IP ANTIBIOTICS (ISPD 2016 GUIDELINES)", fontsize=11
		)
		page.insert_image(CHART_RECT, pixmap=solid_pixmap(600, 400, (200, 60, 30)))
		title_bottom = images.text_line_boxes(page)[0][3]

		clip = images.figure_clip(page, images.find_figures(page, set())[0])

		self.assertGreater(title_bottom, CHART_RECT.y0 - images.CROP_PADDING)
		self.assertEqual(clip.y0, title_bottom)
		self.assertEqual(clip.y1, CHART_RECT.y1 + images.CROP_PADDING)


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

	def test_an_image_tag_the_vlm_made_up_becomes_the_crop(self):
		markdown = f"{TEXT_BEFORE}\n\n![Figure 2. Admissions per year](image2.png)\n\n{TEXT_AFTER}"

		placed = images.place_figures(markdown, [chart_figure()], 2)

		self.assertEqual(
			placed,
			f"{TEXT_BEFORE}\n\n![Figure 2. Admissions per year](/private/files/chart.png)\n\n{TEXT_AFTER}",
		)

	def test_a_made_up_image_tag_beside_a_token_is_dropped(self):
		markdown = (
			f"{TEXT_BEFORE}\n\n[[FIGURE 1]]\n\n![chart](attachment:chart.png)\n\n![Logo](/files/logo.png)"
		)

		placed = images.place_figures(markdown, [chart_figure()], 2)

		self.assertNotIn("attachment:chart.png", placed)
		self.assertIn("![Logo](/files/logo.png)", placed)
		self.assertEqual(placed.count("/private/files/chart.png"), 1)

	def test_a_figure_already_in_the_markdown_is_not_added_twice(self):
		markdown = f"{TEXT_BEFORE}\n\n![Admissions per year](/private/files/chart.png)\n\n{TEXT_AFTER}"

		self.assertEqual(images.place_figures(markdown, [chart_figure()], 2), markdown)


class TestTranscribedFigures(FrappeTestCase):
	def test_keeps_the_crop_of_a_redrawn_chart_and_the_typed_copy_of_a_table_image(self):
		org_chart = (
			"![Figure 28.1](/private/files/page-0028-crop.png)\n\n"
			'```mermaid\nflowchart TD\nA["Head of the Department"]\nB["Clinical"]\nA --> B\n```\n\n28'
		)
		table_image = (
			"## DOSES OF IP ANTIBIOTICS\n\n![Figure 251.1](/private/files/page-0251-crop.png)\n\n"
			"**TABLE 5**\nIntraperitoneal Antibiotic Dosing Recommendations\n\n"
			"| | Intermittent | Continuous |\n|---|---|---|\n"
			"| Amikacin | 2 mg/kg daily | LD 25 mg/L |\n| Gentamicin | 0.6 mg/kg daily | LD 8 mg/L |\n"
			"| Cefazolin | 15 mg/kg daily | LD 500 mg/L |"
		)

		self.assertEqual(
			drop_transcribed_figures(org_chart), "![Figure 28.1](/private/files/page-0028-crop.png)\n\n\n28"
		)
		self.assertEqual(
			drop_transcribed_figures(table_image),
			"## DOSES OF IP ANTIBIOTICS\n\n\n**TABLE 5**\nIntraperitoneal Antibiotic Dosing Recommendations\n\n"
			"| | Intermittent | Continuous |\n|---|---|---|\n"
			"| Amikacin | 2 mg/kg daily | LD 25 mg/L |\n| Gentamicin | 0.6 mg/kg daily | LD 8 mg/L |\n"
			"| Cefazolin | 15 mg/kg daily | LD 500 mg/L |",
		)

	def test_keeps_a_chart_followed_by_a_paragraph_and_a_table(self):
		markdown = (
			"![Figure 3: Admissions](/private/files/chart.png)\n\n"
			f"{TEXT_BEFORE} {TEXT_AFTER} {TEXT_BEFORE} {TEXT_AFTER}\n\n"
			"| Year | Admissions |\n|---|---|\n| 2019 | 40 |\n| 2020 | 52 |\n| 2021 | 61 |"
		)

		self.assertEqual(drop_transcribed_figures(markdown), markdown)

	def test_keeps_a_chart_when_the_table_after_it_is_typed_on_the_page(self):
		markdown = (
			"![Figure 1](/private/files/chart.png)\n\nFigure 2. eGFR trajectory\n\n"
			"| Outcome | HR |\n|---|---|\n| eGFR decline | 0.61 |\n| Death | 0.69 |"
		)
		page_text = "Results\nOutcome HR\neGFR decline 0.61\nDeath 0.69\nTable 1. Hazard ratios."

		self.assertEqual(drop_transcribed_figures(markdown, page_text), markdown)
		self.assertNotIn("chart.png", drop_transcribed_figures(markdown, "Results"))


class TestFigureEchoes(FrappeTestCase):
	def test_drops_labels_read_off_the_picture_and_keeps_the_printed_text(self):
		body = [
			"Figure 2 . Anatomy of lumbar spine",
			"Prep and drape the area after identifying landmarks. Use lidocaine 1% with or without",
			"epinephrine to anesthetize the skin and the deeper tissues under the insertion site",
			"Assemble needle and manometer. Attach the 3-way stopcock to manometer",
		]
		path = Path(tempfile.mkdtemp()) / "procedure.pdf"
		document = fitz.open()
		page = document.new_page(width=612, height=792)
		page.insert_image(CHART_RECT, pixmap=solid_pixmap(600, 400, (200, 60, 30)))
		for offset, line in enumerate(body):
			page.insert_text((72, 530 + 16 * offset), line, fontsize=10)
		document.save(str(path))
		markdown = (
			"![Figure 2 . Anatomy of lumbar spine](/private/files/page-0348-crop.png)\n\n"
			"Puncture site (L4-5 interspace)\n\nLevel of posterior superior iliac crests\n\nL5 L4 L3\n\n"
			"Figure 2 . Anatomy of lumbar spine\n\n"
			"Prep and drape the area after identifying landmarks. Use lidocaine 1% with or without epinephrine "
			"to anesthetize the skin and the deeper tissues under the insertion site\n\n"
			"Assemble needle and manometer. Attach the 3-way stopcock to manometer"
		)

		[(_, cleaned)] = images.drop_figure_echoes([(1, markdown)], images.text_layers(str(path)))

		self.assertEqual(
			cleaned,
			"![Figure 2 . Anatomy of lumbar spine](/private/files/page-0348-crop.png)\n\n"
			"Figure 2 . Anatomy of lumbar spine\n\n"
			"Prep and drape the area after identifying landmarks. Use lidocaine 1% with or without epinephrine "
			"to anesthetize the skin and the deeper tissues under the insertion site\n\n"
			"Assemble needle and manometer. Attach the 3-way stopcock to manometer",
		)


class TestFigureRemediation(FrappeTestCase):
	def setUp(self):
		real_get = engine_settings.get
		patcher = patch(
			"wikify.engine.settings.get",
			side_effect=lambda field: 0 if field == "judge_all_pages" else real_get(field),
		)
		patcher.start()
		self.addCleanup(patcher.stop)

	def test_remediation_keeps_the_chart_as_its_crop_instead_of_a_transcription(self):
		path = Path(tempfile.mkdtemp()) / "report.pdf"
		make_report_pdf(str(path))
		vlm_markdown = f"{TEXT_BEFORE}\n\n[[FIGURE 1: Admissions per year]]\n\n{TEXT_AFTER}"
		with (
			patch("wikify.engine.llm.has_openrouter", return_value=True),
			patch("wikify.engine.llm.chat_completion", side_effect=_fake_chat),
		):
			source_document = parse_pdf(str(path), title="Figure Remediation Test")
			self.addCleanup(_cleanup.delete_document, source_document)
			with (
				patch("wikify.engine.remediate.clean_markdown", side_effect=RuntimeError("offline")),
				patch(
					"wikify.engine.remediate.vlm.parse_page_image", return_value=vlm_markdown
				) as parse_page_image,
			):
				remediate_pdf(source_document, str(path), scope="all")

		hints = [call.kwargs["figure_hint"] for call in parse_page_image.call_args_list]
		self.assertEqual([bool(hint) for hint in hints], [False, True, False])
		self.assertIn("this page has 1 picture(s)", hints[1])
		self.assertIn("[[FIGURE 1]]", hints[1])
		self.assertIn("Never turn its bars, lines, axis ticks, legend or data labels into a table", hints[1])

		page = frappe.db.get_value(
			"Source Page",
			{"source_document": source_document, "page_no": 2},
			["name", "image", "remediation_method", "remediation_markdown", "canonical_markdown"],
			as_dict=True,
		)
		crop_urls = frappe.get_all(
			"File",
			filters={
				"attached_to_doctype": "Source Page",
				"attached_to_name": page.name,
				"file_url": ["!=", page.image],
			},
			pluck="file_url",
		)
		self.assertEqual(len(crop_urls), 1)
		self.assertEqual(page.remediation_method, "vlm")
		self.assertEqual(
			page.remediation_markdown,
			f"{TEXT_BEFORE}\n\n![Admissions per year]({crop_urls[0]})\n\n{TEXT_AFTER}",
		)
		self.assertEqual(page.canonical_markdown.count(crop_urls[0]), 1)
