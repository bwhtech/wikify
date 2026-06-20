# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

from frappe.utils.nestedset import NestedSet


class SourceSection(NestedSet):
	# The section tree is rebuilt wholesale by the parse/remediate pipeline
	# (engine.sectionize), so there is no per-row lifecycle logic here — NestedSet
	# manages lft/rgt on insert. Drag-reparent/reorder lands in Slice 5.
	nsm_parent_field = "parent_source_section"
