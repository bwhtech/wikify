"""Reference edges (0.5 Slice 26) — reify "see page N" cross-references as
`Section Reference` rows, the edge type the POC graph never persisted.

Wiki generation resolves these refs on the fly (pass 2) and throws the result away;
this module extracts the same refs with the same rules into durable, queryable rows —
the data behind the graph view (0.5), and backlinks / agent tools later.

Rules (shared with `loader/wiki.rewrite_page_refs` — one definition, three consumers):
  - detection: `_PAGEREF_RE` + `is_internal_ref` (see/refer cue or "Page No." form,
    and N within the PDF) — external citations never become edges;
  - resolution: `smallest_covering` — the smallest-span section whose page range
    contains N;
  - a ref resolving to its own section is skipped (like a self-route link).

Rows are wholly derived: `extract_references` wipes and rebuilds (whole document, or
one section's *outgoing* rows), so it is idempotent and safe to call after any
content write.
"""

from __future__ import annotations

from wikify.engine.loader.wiki import _PAGEREF_RE, is_internal_ref


def smallest_covering(spans: list[dict], n: int) -> dict | None:
	"""The smallest-span dict whose `page_start`..`page_end` contains n (first wins
	ties), or None. The single page→section resolver behind wiki generation, section
	sync, the preview, and reference extraction."""
	best = None
	for s in spans:
		ps, pe = s.get("page_start"), s.get("page_end")
		if ps and pe and ps <= n <= pe and (best is None or pe - ps < best[1]):
			best = (s, pe - ps)
	return best[0] if best else None


def extract_references(source_document: str, section_names: list[str] | None = None) -> int:
	"""(Re)build a document's `Section Reference` rows. Returns the row count written.

	`section_names=None` replaces the whole document's rows; a list replaces only those
	sections' outgoing rows (their markdown changed — targets are re-resolved against
	the document's *current* spans either way). Duplicate (from, to, page, anchor)
	occurrences collapse into one row with an `occurrences` count.
	"""
	from wikify.engine import store

	page_count = store.get_page_count(source_document) or 10**9
	spans = store.get_section_spans(source_document)
	rows: dict[tuple, dict] = {}
	for sec in store.get_section_bodies(source_document, section_names):
		for m in _PAGEREF_RE.finditer(sec["markdown"] or ""):
			cue, kind, num = m.group(1), m.group(2), int(m.group(3))
			if not is_internal_ref(cue, kind, num, page_count):
				continue
			target = smallest_covering(spans, num)
			if not target or target["name"] == sec["name"]:
				continue
			key = (sec["name"], target["name"], num, m.group(0))
			if key in rows:
				rows[key]["occurrences"] += 1
			else:
				rows[key] = {
					"from_section": sec["name"],
					"to_section": target["name"],
					"source_document": source_document,
					"target_page": num,
					"anchor_text": m.group(0),
					"occurrences": 1,
				}
	store.replace_references(source_document, list(rows.values()), from_sections=section_names)
	return len(rows)
