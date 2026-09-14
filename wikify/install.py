"""App install hooks."""

from __future__ import annotations

import frappe
from frappe.custom.doctype.property_setter.property_setter import make_property_setter

from wikify.seed import seed_section_types, seed_uncategorized_project

WIKI_TITLE_DOCTYPES = ("Wiki Document", "Wiki Revision Item")
WIDE_TEXT_FIELDTYPES = ("Small Text", "Text", "Long Text")
WIDE_TEXT_COLUMNS = ("text", "mediumtext", "longtext")


def after_install() -> None:
	seed_section_types()
	seed_uncategorized_project()
	add_wiki_title_customizations()


def after_app_install(app_name: str) -> None:
	if app_name == "wiki":
		add_wiki_title_customizations()


def add_wiki_title_customizations() -> None:
	if "wiki" not in frappe.get_installed_apps():
		return
	for doctype in WIKI_TITLE_DOCTYPES:
		if frappe.get_meta(doctype).get_field("title").fieldtype not in WIDE_TEXT_FIELDTYPES:
			make_property_setter(
				doctype, "title", "fieldtype", "Small Text", "Select", validate_fields_for_doctype=False
			)
		# A property setter only changes the meta; updatedb is what widens the MariaDB column.
		if frappe.db.get_column_type(doctype, "title") not in WIDE_TEXT_COLUMNS:
			frappe.db.updatedb(doctype)
