# Wikify 0.5 — Graph View

The POC's persistence layer *was a graph* (`scratch/pdf_lab/loader/graph.py` —
beagle-style typed nodes + edges, `rel ∈ {HAS_SECTION, PART_OF}`, page-span evidence on
every node). The Frappe port kept the semantics — nodes became `Source Document` /
`Source Section`, edges became Link fields + the NestedSet tree — but the graph stopped
being *visible*, and one edge type was never persisted at all: the **page-reference
links** ("see page 12") that wiki generation resolves and rewrites on the fly.

**0.5 gives the graph a face**: an Obsidian-style interactive graph view at two scopes —

- **Document graph** — one Source Document's sections: hierarchy edges + reference
  edges, colored by Section Type. *"What does this document look like as a web, and
  which sections point at which?"*
- **Project graph** — every document in a Wikify Project with its sections: clusters
  emerge per document, Section Type colors line up across clusters. *"Where do my
  documents overlap in shape?"*

And it **reifies the missing edge**: a persisted `Section Reference` DocType, extracted
with the same regex + smallest-span resolution wiki generation already uses — so
references become queryable data (graph API today; backlinks, agent tools, cross-doc
links later) instead of a side effect of generation.

## Spec index

| Doc | Covers |
|---|---|
| [`01-graph-view.md`](01-graph-view.md) | The graph model (node/edge types, POC lineage), the `Section Reference` DocType + extraction seam, the graph API, and the `GraphView` frontend (d3-force canvas, interactions, filters, theming). |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | Tracer-bullet slices **26–28** (continuing 0.4's numbering), delivery order, per-slice Verify steps against `pdf.localhost`. |

## Principles locked (2026-07-03)

1. **The graph is derived, never authored.** Nodes and edges are projections of
   existing DocTypes; the view writes nothing. The only new persisted data is
   `Section Reference`, and it is always rebuildable from section markdown
   (idempotent re-extract, safe to wipe).
2. **Same resolver everywhere.** Reference extraction reuses the `_PAGEREF_RE` +
   smallest-covering-span logic that wiki generation and the preview already use —
   one definition of "what is a reference", three consumers.
3. **References follow content.** Any write to `Source Section.markdown` (agent edit,
   rebuild-from-pages, split/merge, re-sectionize) re-extracts that section's outgoing
   references in the same operation. Stale edges are a bug, not a refresh button.
4. **Document-internal only, for now.** "page N" can only mean *this* document. The
   schema doesn't assume that (`from_section` / `to_section` are plain links), so
   cross-document reference extraction is a future slice, not a migration.

## Decisions (confirmed 2026-07-03)

- **Renderer:** hand-rolled canvas + `d3-force` (+ `d3-zoom`/`d3-drag`) — the Obsidian
  approach. Full control of feel, ~30 kB, scales past 1k nodes. Not cytoscape/vis/sigma.
- **Placement:** **dedicated full-screen routes** — `/imports/:id/graph` (document
  scope) and `/projects/:id/graph` (project scope) — entered via a Graph button on the
  respective detail pages. Max canvas room; not a tab, not an overlay.
- **Project-graph shape:** document nodes + all their section nodes; no artificial
  project root node; Section Type is a *color channel*, not a node type. Cross-document
  signal = per-document clusters + shared type colors (cross-doc reference extraction
  stays future work).

## Conventions (unchanged)

Same as [`../0.3/README.md`](../0.3/README.md): backend per the `frappe-app-dev` skill,
engine work behind the `store.py` seam, thin whitelisted APIs, frontend frappe-ui v1 +
semantic tokens, verify every slice against `pdf.localhost` before the next, work
directly on `main`.

0.4 (Review UX & Pipeline Trust, slices 21–25) shipped before this spec was finalized;
0.5 starts at **26** and touches the detail pages only to add the Graph entry buttons.
