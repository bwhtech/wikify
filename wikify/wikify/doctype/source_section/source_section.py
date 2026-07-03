# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

from frappe.utils.nestedset import NestedSet


class SourceSection(NestedSet):
	# The section tree is rebuilt wholesale by the parse/remediate pipeline
	# (engine.sectionize), so there is no per-row lifecycle logic here — NestedSet
	# manages lft/rgt on insert. Drag-reparent/reorder lands in Slice 5.
	nsm_parent_field = "parent_source_section"

	def validate(self):
		# Document-path half of the 0.6 lint funnel (insert/save); raw db.set_value
		# markdown writes go through store.set_section_markdown instead.
		from wikify.engine.store import lint_json

		self.lint_issues = lint_json(self.markdown or "")

	def on_trash(self, allow_root_deletion: bool = False):
		# 0.5: reference edges die with their endpoints. Raw-delete paths
		# (api.sections.delete_section, store.replace_sections) don't come through
		# here — they run a full extract_references afterwards instead.
		import frappe

		frappe.db.delete("Section Reference", {"from_section": self.name})
		frappe.db.delete("Section Reference", {"to_section": self.name})
		super().on_trash(allow_root_deletion)
