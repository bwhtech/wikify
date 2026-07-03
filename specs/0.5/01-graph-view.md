# 0.5 — Graph View: model, reference edges, API, frontend

## 1. Lineage — the graph we already have

The POC persisted a literal graph (`scratch/pdf_lab/loader/graph.py`):

```
nodes:  type ∈ {document, section}, page_start/end as evidence, section_type
edges:  rel  ∈ {HAS_SECTION (doc → section), PART_OF (section → parent)}
```

The Frappe port mapped it 1:1 onto DocTypes:

| POC | Port | Edge it carries |
|---|---|---|
| `nodes.type = document` | `Source Document` | — |
| `nodes.type = section` | `Source Section` | — |
| `edges.rel = HAS_SECTION` | `Source Section.source_document` | document → section |
| `edges.rel = PART_OF` | `Source Section.parent_source_section` (NestedSet) | section → parent |
| `nodes.section_type` | `Source Section.section_type` → `Section Type` | color channel |
| *(not persisted — computed at wiki generation)* | **`Section Reference` (new, this spec)** | section → section |

So the graph view is a *projection*, not a new model. The one genuinely new persisted
piece is the reference edge (§3).

### Node types

| Kind | Backed by | Label | Size driver | Color |
|---|---|---|---|---|
| `document` | Source Document | title | page_count | neutral (ink) |
| `section` | Source Section | title | span (`page_end - page_start + 1`), or degree in "links" mode | Section Type palette (§6); untyped = muted gray |

No `project` node: at project scope every node would connect to it — pure noise.
Document clusters emerge from the physics.

### Edge types

| Rel | Meaning | Source of truth | Render |
|---|---|---|---|
| `HAS_SECTION` | document → its root sections | `source_document` where `parent_source_section` is empty | faint, thin |
| `PART_OF` | section → parent section | `parent_source_section` | faint, thin |
| `REFERENCES` | section body says "see page N" → section covering N | `Section Reference` rows (§3) | accented, weight = occurrence count |

Hierarchy edges give the skeleton (a tree — what NestedSet already draws as an
outline); `REFERENCES` edges are what make the view *worth having* — they're the
cross-cutting structure no existing screen shows.

## 2. Scopes

- **Document graph** (`/imports/:id/graph`): one Source Document — its document
  node, all sections, all three edge types.
- **Project graph** (`/projects/:id/graph`): all Source Documents in the
  project, each with its full section subgraph. Cross-document edges don't exist yet
  (references are document-internal, README principle 4); the cross-document signal is
  **shared Section Type colors** across clusters, reinforced by the type filter (§6).

## 3. `Section Reference` — reifying the page-ref edge

New DocType (child of nothing; plain document, no track_changes, no naming series —
`autoname: hash`):

| Field | Type | Notes |
|---|---|---|
| `from_section` | Link → Source Section, reqd | section whose markdown contains the ref |
| `to_section` | Link → Source Section, reqd | smallest-span section covering the target page |
| `source_document` | Link → Source Document, reqd | denormalized for one-hop project/document queries |
| `target_page` | Int | the N in "page N" (evidence) |
| `anchor_text` | Small Text | the matched text, e.g. "see page 12" (tooltip / later backlinks UI) |
| `occurrences` | Int, default 1 | same (from, to, target_page, anchor) collapsed into one row; drives edge weight |

Self-references (`from == to`) are **not stored** — same rule as
`rewrite_page_refs` skipping links to the current route.

### Extraction (`engine/refs.py`)

One function, same resolver as generation/preview (README principle 2):

```
extract_references(source_document, section_names=None) -> int
```

- Detect with `_PAGEREF_RE` + the same *internal-ref* rule as
  `engine/loader/wiki.py::rewrite_page_refs` (see/refer cue or "Page No." form, and
  `1 <= N <= page_count`) — external citations ("Williams p820") never become edges.
- Resolve N → section via smallest covering span over
  `store.get_section_spans(source_document)` — identical logic to
  `generate.py::_route_for_page` / `api/wiki.py::route_for_page`. Fold all three
  callers onto one shared span-resolver helper on the store seam while here.
- Scoped rebuild: `section_names=None` wipes + re-extracts the whole document;
  a list re-extracts only those sections' *outgoing* rows (delete theirs, re-insert).
  Idempotent either way.

### Triggers (references follow content — README principle 3)

| Event | Call |
|---|---|
| Sectionize / re-sectionize (`store.replace_sections`) | full-document extract |
| Tree approval | full-document extract (cheap idempotence guard) |
| `edit_section_content`, `rebuild_section_from_pages` (agent tools) | scoped extract for the touched section |
| `split_section` / `merge_sections` / `delete_section` | full-document extract (spans changed → *incoming* edges may re-target) |
| Wiki generation | none needed (extraction is upstream) — but generation's Pass 2 keeps working exactly as today; it does **not** read `Section Reference` in 0.5 (candidate consolidation later) |

Deleting a Source Section cascades: `on_trash` deletes rows where it's `from` **or**
`to`. A migration patch backfills all existing approved documents.

## 4. Graph API (`api/graph.py`)

Two thin whitelisted reads (permission = read on the Project / Source Document):

```
get_document_graph(source_document) -> {nodes, edges, meta}
get_project_graph(project)          -> {nodes, edges, meta}
```

Payload — flat, render-ready, no DocType leakage:

```json
{
  "nodes": [
    {"id": "SEC-0042", "kind": "section", "label": "Casualty Evacuation",
     "section_type": "procedure", "doc": "SD-0007", "span": 6, "degree": 4,
     "page_start": 41, "page_end": 46},
    {"id": "SD-0007", "kind": "document", "label": "Field Manual", "pages": 128}
  ],
  "edges": [
    {"src": "SEC-0042", "dst": "SEC-0051", "rel": "REFERENCES", "weight": 2},
    {"src": "SEC-0042", "dst": "SEC-0038", "rel": "PART_OF", "weight": 1}
  ],
  "meta": {"types": [{"name": "procedure", "label": "Procedure", "count": 12}],
           "documents": [{"id": "SD-0007", "label": "Field Manual"}]}
}
```

- `degree` is precomputed server-side (REFERENCES only) so "size by links" costs
  nothing on the client.
- Project payload = union of per-document payloads; `meta.documents` feeds the legend
  and per-document filter.
- Bulk queries only (`frappe.get_all` + one pass over `Section Reference`) — no
  per-node `get_doc`. A 20-doc project with ~2k sections must return in well under a
  second.

## 5. Frontend — `GraphView.vue`

New deps: **`d3-force`, `d3-zoom`, `d3-drag`, `d3-selection`** (micro-packages, ~30 kB
total). Rendering is a plain 2D `<canvas>`, devicePixelRatio-aware. No SVG — canvas
holds 60 fps at thousands of nodes, which is what Obsidian does.

One component, two scopes:

```
<GraphView :fetch="'wikify.api.graph.get_document_graph'" :params="{source_document}" />
<GraphView :fetch="'wikify.api.graph.get_project_graph'"  :params="{project}" />
```

Data via `useCall` (v3). Mounted on **dedicated routes** — two thin page wrappers
(`ImportGraph.vue`, `ProjectGraph.vue`) registered in `router.js` as
`/imports/:id/graph` and `/projects/:id/graph`, following the existing page pattern:
48 px sticky header with `Breadcrumbs` (Project → Import → Graph), canvas filling the
rest of the viewport (`h-[calc(100vh-48px)]`). Entry points: a Graph `Button`
(`variant="subtle"`, lucide `waypoints` icon) in the `ImportDetail` and
`ProjectDetail` headers. The simulation starts on mount and is torn down on route
leave (no background RAF).

### Simulation

`forceSimulation` with: `forceLink` (distance shorter for `PART_OF` than
`REFERENCES`, strength ∝ weight), `forceManyBody` (repulsion), `forceCollide`
(radius = node radius + label pad), `forceX/forceY` (gentle centering — better than
`forceCenter` for disconnected components, which project graphs always are). Let alpha
decay to rest; node drag re-heats locally (`alphaTarget` bump). Positions are not
persisted — layout is cheap and deterministic-enough.

### Interactions (Obsidian parity)

| Gesture | Behavior |
|---|---|
| hover node | node + neighbors + incident edges at full opacity, everything else fades to ~15%; tooltip = title, type, pages `page_start–page_end` |
| hover REFERENCES edge | tooltip = `anchor_text` (×occurrences) |
| click section node | navigate: `ImportDetail` with the section selected in the tree/preview (route query `?section=`) |
| click document node (project scope) | navigate to that `ImportDetail` |
| drag node | reposition + re-heat |
| wheel / pinch, drag background | zoom (0.1×–8×) / pan via `d3-zoom` on the canvas |
| zoom threshold | labels hidden when zoomed out; always shown for hover neighborhood |

### Controls (compact toolbar over the canvas)

- **Search** — as-you-type; matching nodes highlighted, rest faded (same fade path as
  hover).
- **Type filter** — `TypeChip` row from `meta.types`; toggling a type dims (not
  removes) its nodes, so layout stays stable.
- **Edge toggles** — Hierarchy on/off, References on/off (hierarchy-off gives the pure
  Obsidian "links only" view).
- **Size by** — Pages | Links.
- **Document filter** (project scope only) — isolate one document's cluster.
- Legend: type → color swatches (from `meta.types`).

### Theming

Canvas can't consume Tailwind classes — resolve the semantic tokens
(`--surface-*`, `--ink-*`, `--outline-*`) via `getComputedStyle` at mount and on theme
change, so dark mode Just Works. Section Type palette: deterministic assignment from a
fixed ~12-hue list (hash of type name → hue index), consistent between document and
project scope and across sessions.

### States & performance

- Empty (no approved sections): `EmptyState`-style prompt pointing at Tree Review.
- No REFERENCES edges: render hierarchy-only, with a subtle hint that reference links
  appear when sections cite pages.
- Budget: smooth at 2 000 nodes / 5 000 edges. Above that, skip labels + collide force
  while alpha is high. (Realistic ceiling: POC docs produced 50–300 sections each.)

## 6. Explicitly out of scope (future hooks)

- **Cross-document references** — needs title/mention extraction (likely LLM-assisted);
  schema already permits it (`to_section` in another document + a `rel` variant).
- **Backlinks panel** ("what references this section?") in tree review / preview —
  `Section Reference` makes it a one-query feature; UI deferred.
- **Agent graph tools** (`get_backlinks`, `get_references`) — natural 0.6 additions on
  the same data.
- **Wiki-generation consolidation** — Pass 2 could consume `Section Reference` instead
  of re-running the regex; deferred until refs have soaked.
- Persisted node positions, graph screenshots/export, timeline animation.
