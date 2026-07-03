# 0.5 Implementation Plan — Tracer-Bullet Slices

Continues the spine (0.2: 10–16 · 0.3: 17–20 · 0.4: 21–25). Numbering starts at **26**.
Each slice cuts through every layer it touches and ends demoable on its own.

> Source of truth for behavior is [`01-graph-view.md`](01-graph-view.md).
> This file is the *delivery order*.

## Slice map

| # | Slice | Type | Blocked by | Status |
|---|---|---|---|---|
| 26 | Reference spine — `Section Reference` DocType, `engine/refs.py` extraction, shared span-resolver on the store seam, content-mutation triggers, backfill patch | AFK | — | ⬜ |
| 27 | Document graph — `api/graph.get_document_graph` + `GraphView.vue` (d3-force canvas: physics, hover fade, click-nav, zoom/pan/drag, theming) + `/imports/:id/graph` route & entry button | HITL | 26 | ⬜ |
| 28 | Project graph + lenses — `get_project_graph`, `/projects/:id/graph` route & entry button, search, type filter, edge toggles, size-by, document filter, legend, perf pass | HITL | 27 | ⬜ |

**Spine:** 26 → 27 → 28, strictly sequential — 27 renders what 26 persists; 28 scales
what 27 renders.

The guiding move: **Slice 26 is pure backend and independently valuable** (queryable
reference edges enable backlinks + agent tools later, regardless of the view). 27 is
where the Obsidian feel gets tuned by hand — budget review time for physics constants.
28 is mostly composition + filtering on an already-working canvas.

---

## Verification

Same protocol as 0.2/0.3: verify each slice against **`pdf.localhost`**
(Administrator / admin) before starting the next; `bench` from the bench root;
`bench start` running for anything that touches jobs/realtime.

The standing acceptance fixture: a document whose sections contain **known "see page
N" refs** (the eval-harness fixture markdown already has some; extend it if the ref
count is < 5) plus at least one **external citation** ("Williams p820") that must NOT
become an edge, and one **self-reference** that must be skipped.

Tiers per slice:

1. **Unit** — `bench --site pdf.localhost run-tests --app wikify`; must stay green.
2. **Headless** — `bench --site pdf.localhost execute` the extraction / graph API
   directly; assert counts + shapes.
3. **UI walkthrough** — at `/wikify`, drive the graph routes by hand (physics feel,
   hover, navigation, dark mode).

---

## 26 — Reference spine

**Demo:** run extraction on a parsed document headlessly; `Section Reference` rows
appear with correct from/to/anchor; edit a section's markdown through the agent and the
rows follow.

### What to build

- `Section Reference` DocType per §3 (hash autoname, 6 fields, no UI).
- Shared smallest-covering-span resolver on the store seam; refactor
  `generate.py::_route_for_page` and `api/wiki.py::route_for_page` onto it (behavior
  identical — this is the "same resolver everywhere" principle made literal).
- `engine/refs.py::extract_references(source_document, section_names=None)` — detect
  via `_PAGEREF_RE` + the internal-ref rule, resolve, upsert with `occurrences`
  collapsing, skip self-refs.
- Triggers: `store.replace_sections`, tree approval, `edit_section_content`,
  `rebuild_section_from_pages`, split/merge/delete (full-doc for shape changes, scoped
  for content edits). `Source Section.on_trash` cascade.
- Patch: backfill every existing Source Document with sections.

### Acceptance criteria

- Fixture document extracts the expected edge set exactly: internal refs → rows,
  external citation → no row, self-ref → no row, duplicate ref text → one row with
  `occurrences = 2`.
- Re-running extraction is a no-op (row count + names stable).
- Agent `edit_section_content` adding "see page 3" to a section produces the new edge
  in the same operation; removing it deletes the edge.
- `merge_sections` re-targets incoming edges to the survivor (via the full-doc
  re-extract); no dangling `to_section` after any tree surgery.
- Wiki generation output is byte-identical before/after the span-resolver refactor on
  the fixture doc.
- Unit tests cover: extraction rules, scoped vs full rebuild, trash cascade, resolver
  parity (old vs new implementation on recorded spans).

---

## 27 — Document graph

**Demo:** open an approved import → Graph button → the document's sections settle into an
Obsidian-style web; hovering a section fades everything but its neighborhood; clicking
it lands in the tree with that section selected; reference edges show their anchor text.

### What to build

- `api/graph.py::get_document_graph` per §4 (bulk queries, precomputed degree, `meta`).
- Frontend deps: `d3-force`, `d3-zoom`, `d3-drag`, `d3-selection`.
- `GraphView.vue`: canvas renderer (DPR-aware), simulation per §5 (forceX/Y not
  forceCenter), hover fade + tooltips, click navigation (`?section=` route query —
  `ImportDetail` must select + scroll the tree to it), node drag re-heat, zoom/pan,
  zoom-threshold labels, token-resolved colors + theme-change re-read, type palette.
- `ImportGraph.vue` page + `/imports/:id/graph` route in `router.js` (sticky
  breadcrumb header, full-viewport canvas); Graph entry button in the `ImportDetail`
  header. Simulation torn down on route leave.
- Empty / no-references states.

### Acceptance criteria

- Fixture doc: node count = section count + 1 (document node); edge counts match the
  API payload; REFERENCES edges visually distinct from hierarchy.
- Hover / search fade and tooltips correct; labels appear/disappear at the zoom
  threshold.
- Click-through selects the right section in tree review (deep-link `?section=` works
  cold, too).
- Dark mode: colors re-resolve without reload.
- Navigating away stops the RAF loop and disposes the simulation (no background CPU
  burn — assert via performance profile spot-check).
- Unit tests: API payload shape, degree math, permission denial for a foreign user.

---

## 28 — Project graph + lenses

**Demo:** open a project with 3+ imports → Graph button → per-document clusters with
consistent type colors; toggle Hierarchy off to see the pure reference web; filter to
one Section Type to see it light up across all documents.

### What to build

- `api/graph.py::get_project_graph` (union payload + `meta.documents`).
- `ProjectGraph.vue` page + `/projects/:id/graph` route, reusing `GraphView` with the
  project fetch; Graph entry button in the `ProjectDetail` header.
- Toolbar per §5: search, `TypeChip` type filter (dim, don't remove), edge toggles,
  size-by (Pages | Links), document filter (project scope), legend.
- Perf pass: label + collide suppression while alpha is high; verify the 2k-node
  budget with a synthetic fixture (script that fabricates N sections headlessly is
  fine — don't hand-import 20 PDFs).

### Acceptance criteria

- Disconnected components (multiple documents) stay on-canvas (forceX/Y verified).
- Type colors identical for the same type across document and project scope.
- Every toolbar control composes with hover fade (e.g. type-filtered + hover shows the
  intersection).
- Document filter isolates one cluster; clicking a document node navigates to its
  import.
- Synthetic 2k-node payload stays interactive (pan/zoom responsive, settles < ~5 s on
  the dev machine).
- Unit tests: project payload shape (multi-doc union, no cross-doc edges), meta
  counts, empty-project payload.
