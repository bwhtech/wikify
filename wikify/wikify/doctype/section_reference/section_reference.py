# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

from frappe.model.document import Document


class SectionReference(Document):
	# A derived edge (0.5): "see page N" in from_section's markdown, resolved to the
	# smallest-span section covering N. Rows are wholly owned by
	# engine.refs.extract_references — always rebuildable, safe to wipe.
	pass
