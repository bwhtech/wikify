"""Classification pass (Slice 6) — tag a doc's Source Sections with a `section_type`.

Ported from the POC `pipeline.classify_document` + `_store_sections`'s inline classify.
Two boundary changes from the POC:

  - the taxonomy is read from the `Section Type` master (not a hard-coded list), so a
    corpus can extend it without touching code;
  - **classification runs sequentially**, not in a `ThreadPoolExecutor`. The LLM calls
    are read-only, but each one resolves the key/model through Frappe (`engine.settings`
    → `frappe.local`), which isn't safe across worker threads — the same reason
    remediation (Slice 3) runs serially. The classifier model is cheap/fast, so a few
    serial calls per doc is fine for a dev-tool pass, and it streams progress cleanly.

Run eagerly at the end of both parse and remediate (after the tree is rebuilt), and
on demand via the reclassify job after manual tree edits.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable

from wikify.engine import store
from wikify.engine.loader.classifier import classify_section


def classify_document(
	source_document: str,
	progress_cb: Callable[[int, int, str, str], None] | None = None,
) -> dict:
	"""Classify every section of a doc against the Section Type taxonomy.

	Writes `section_type` on each Source Section and returns
	`{"sections": n, "by_type": {type: count}}`. `progress_cb(done, total, title, type)`
	streams per-section progress for the reclassify job's live log.
	"""
	taxonomy = store.get_section_taxonomy()
	sections = store.get_sections_to_classify(source_document)
	total = len(sections)
	counts: Counter[str] = Counter()
	for i, sec in enumerate(sections):
		section_type = classify_section(sec["title"] or "", sec["markdown"] or "", taxonomy)
		store.set_section_type(sec["name"], section_type)
		counts[section_type] += 1
		if progress_cb:
			progress_cb(i + 1, total, sec["title"] or "", section_type)
	return {"sections": total, "by_type": dict(counts)}
