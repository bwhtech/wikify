from __future__ import annotations

import frappe
from frappe import _

from wikify.api.permission import assert_readable
from wikify.engine import figure


@frappe.whitelist(methods=["POST"])
def crop_page_figure(
	source_document: str,
	page_no: int,
	caption: str,
	occurrence: int,
	x0: float,
	y0: float,
	x1: float,
	y1: float,
) -> dict:
	assert_readable(None, source_document)
	try:
		return figure.crop_page_figure(
			source_document,
			int(page_no),
			caption,
			int(occurrence),
			{"x0": float(x0), "y0": float(y0), "x1": float(x1), "y1": float(y1)},
		)
	except (ValueError, RuntimeError) as e:
		frappe.throw(_(str(e)))
