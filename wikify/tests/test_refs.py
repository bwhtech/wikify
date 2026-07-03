# Copyright (c) 2026, BWH and contributors
# For license information, please see license.txt

"""0.5 Slice 26 — reference extraction (`engine.refs`) + the triggers that keep
`Section Reference` rows tracking section content and shape.

Tree fixture (page_count 6):
  Alpha (1-2)  — two "see page 5" refs (collapse to one row), an external citation,
                 a self-ref, a bare "page 3", and an out-of-range "see page 99"
  Beta  (3-4)  — one "Page No. 5" ref
  Gamma (5-6)  — group
    Gamma Detail (5-5) — the smallest span covering page 5, so both refs target it
"""

import frappe
from frappe.tests.utils import FrappeTestCase

from wikify.api import sections as api
from wikify.engine import store
from wikify.engine.loader.sectionizer import Section
from wikify.engine.refs import extract_references, smallest_covering

ALPHA_MD = (
	"Intro. see page 5 for details, and again see page 5.\n"
	"External: Williams p. 820. Bare mention of page 3 stays text.\n"
	"Self: see page 1. Out of range: see page 99.\n"
)
BETA_MD = "Procedure. Refer Page No. 5 for the escalation flow.\n"


def _sec(title, level, path, p_start, p_end, markdown=""):
	return Section(
		title=title,
		level=level,
		hierarchy_path=path,
		page_start=p_start,
		page_end=p_end,
		markdown=markdown,
	)


class TestRefs(FrappeTestCase):
	def setUp(self):
		self.sd = frappe.get_doc({"doctype": "Source Document", "title": "Refs Test"}).insert(
			ignore_permissions=True
		)
		store.set_page_count(self.sd.name, 6)
		# replace_sections is itself an extraction trigger — no explicit call here.
		store.replace_sections(
			self.sd.name,
			[
				_sec("Alpha", 1, ["Alpha"], 1, 2, ALPHA_MD),
				_sec("Beta", 1, ["Beta"], 3, 4, BETA_MD),
				_sec("Gamma", 1, ["Gamma"], 5, 6),
				_sec("Gamma Detail", 2, ["Gamma", "Gamma Detail"], 5, 5),
			],
		)
		self.by_title = {
			r.title: r.name
			for r in frappe.get_all(
				"Source Section", filters={"source_document": self.sd.name}, fields=["name", "title"]
			)
		}

	def _rows(self):
		return frappe.get_all(
			"Section Reference",
			filters={"source_document": self.sd.name},
			fields=["from_section", "to_section", "target_page", "anchor_text", "occurrences"],
			order_by="from_section asc, target_page asc",
		)

	def test_initial_extraction(self):
		rows = self._rows()
		self.assertEqual(len(rows), 2)
		by_from = {r.from_section: r for r in rows}

		alpha = by_from[self.by_title["Alpha"]]
		# Duplicate "see page 5" collapses; smallest span (Gamma Detail, not Gamma) wins.
		self.assertEqual(alpha.to_section, self.by_title["Gamma Detail"])
		self.assertEqual(alpha.occurrences, 2)
		self.assertEqual(alpha.target_page, 5)

		beta = by_from[self.by_title["Beta"]]
		self.assertEqual(beta.to_section, self.by_title["Gamma Detail"])
		self.assertEqual(beta.occurrences, 1)
		# External citation / bare mention / self-ref / out-of-range produced nothing:
		# the two rows above are the whole set.

	def test_reextract_is_idempotent(self):
		before = self._rows()
		count = extract_references(self.sd.name)
		self.assertEqual(count, 2)
		self.assertEqual(self._rows(), before)

	def test_references_follow_content_writes(self):
		alpha = self.by_title["Alpha"]
		# Refs removed from the body → outgoing rows die; others untouched.
		store.set_section_markdown(alpha, "No references anymore.")
		self.assertEqual([r.from_section for r in self._rows()], [self.by_title["Beta"]])
		# A new ref appears in the same write funnel.
		store.set_section_markdown(alpha, "Now see page 3 instead.")
		rows = {r.from_section: r for r in self._rows()}
		self.assertEqual(rows[alpha].to_section, self.by_title["Beta"])
		self.assertEqual(rows[alpha].target_page, 3)

	def test_delete_retargets_incoming(self):
		# Deleting the smallest cover re-resolves page 5 to the surviving Gamma (5-6).
		api.delete_section(self.by_title["Gamma Detail"])
		rows = self._rows()
		self.assertEqual(len(rows), 2)
		self.assertTrue(all(r.to_section == self.by_title["Gamma"] for r in rows))

	def test_merge_sweeps_husk_rows(self):
		# Merge Beta into Alpha: the husk's outgoing row dies with it, no dangling refs.
		api.merge_sections([self.by_title["Alpha"], self.by_title["Beta"]])
		rows = self._rows()
		froms = {r.from_section for r in rows}
		self.assertNotIn(self.by_title["Beta"], froms)
		self.assertEqual(froms, {self.by_title["Alpha"]})
		self.assertTrue(all(r.to_section == self.by_title["Gamma Detail"] for r in rows))

	def test_document_graph_payload(self):
		from wikify.api.graph import get_document_graph

		g = get_document_graph(self.sd.name)
		by_id = {n["id"]: n for n in g["nodes"]}
		# 4 sections + 1 document node.
		self.assertEqual(len(g["nodes"]), 5)
		self.assertEqual(by_id[self.sd.name]["kind"], "document")
		self.assertEqual(by_id[self.sd.name]["pages"], 6)

		rels = {}
		for e in g["edges"]:
			rels.setdefault(e["rel"], []).append(e)
		# 3 roots hang off the document; Gamma Detail hangs off Gamma.
		self.assertEqual(len(rels["HAS_SECTION"]), 3)
		self.assertEqual(len(rels["PART_OF"]), 1)
		self.assertEqual(
			rels["PART_OF"][0],
			{
				"src": self.by_title["Gamma Detail"],
				"dst": self.by_title["Gamma"],
				"rel": "PART_OF",
				"weight": 1,
			},
		)
		# Two REFERENCES edges (Alpha→Detail weight 2, Beta→Detail weight 1)…
		refs = {e["src"]: e for e in rels["REFERENCES"]}
		self.assertEqual(refs[self.by_title["Alpha"]]["weight"], 2)
		self.assertEqual(refs[self.by_title["Beta"]]["weight"], 1)
		# …and degree counts REFERENCES weight only: Detail 3 in, Alpha 2 out, Beta 1 out.
		self.assertEqual(by_id[self.by_title["Gamma Detail"]]["degree"], 3)
		self.assertEqual(by_id[self.by_title["Alpha"]]["degree"], 2)
		self.assertEqual(by_id[self.by_title["Gamma"]]["degree"], 0)
		# Span drives "size by pages"; meta carries the document for the legend.
		self.assertEqual(by_id[self.by_title["Alpha"]]["span"], 2)
		self.assertEqual(g["meta"]["documents"], [{"id": self.sd.name, "label": "Refs Test"}])

	def test_project_graph_payload(self):
		from wikify.api.graph import get_project_graph

		project = frappe.get_doc({"doctype": "Wikify Project", "project_name": "Graph Test"}).insert(
			ignore_permissions=True
		)
		frappe.db.set_value("Source Document", self.sd.name, "project", project.name)
		sd2 = frappe.get_doc(
			{"doctype": "Source Document", "title": "Second Doc", "project": project.name}
		).insert(ignore_permissions=True)
		store.replace_sections(sd2.name, [_sec("Solo", 1, ["Solo"], 1, 2)])

		g = get_project_graph(project.name)
		# Union of both documents: (1 doc + 4 sections) + (1 doc + 1 section).
		self.assertEqual(len(g["nodes"]), 7)
		self.assertEqual([d["id"] for d in g["meta"]["documents"]], [self.sd.name, sd2.name])
		# No cross-document edges: every edge's endpoints live in one document.
		doc_of = {n["id"]: n.get("doc") or n["id"] for n in g["nodes"]}
		self.assertTrue(all(doc_of[e["src"]] == doc_of[e["dst"]] for e in g["edges"]))

		empty = frappe.get_doc({"doctype": "Wikify Project", "project_name": "Empty Graph"}).insert(
			ignore_permissions=True
		)
		self.assertEqual(
			get_project_graph(empty.name),
			{"nodes": [], "edges": [], "meta": {"types": [], "documents": []}},
		)

	def test_smallest_covering(self):
		spans = [
			{"name": "wide", "page_start": 1, "page_end": 10},
			{"name": "tight", "page_start": 5, "page_end": 6},
			{"name": "tie", "page_start": 6, "page_end": 7},
			{"name": "open", "page_start": None, "page_end": None},
		]
		self.assertEqual(smallest_covering(spans, 5)["name"], "tight")
		# Equal spans: first in list order wins (parity with the pre-refactor loops).
		self.assertEqual(smallest_covering(spans, 6)["name"], "tight")
		self.assertEqual(smallest_covering(spans, 3)["name"], "wide")
		self.assertIsNone(smallest_covering(spans, 11))
