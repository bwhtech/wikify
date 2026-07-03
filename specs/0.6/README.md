# Wikify 0.6 — Wiki-Tab Agent Context & Markdown Lint

Two trust gaps, one release.

**Gap 1 — the agent is blind on the Wiki tab.** The Wiki tab's page preview
(`WikiGenerate.vue` → `WikiPreview`) shows one projected wiki page — which *is* one
`Source Section` — but never writes to the `agentContext` store the way
`SectionTree.vue` and `PageReview.vue` do. So a user reading "REVISION HISTORY" as a
wiki page and asking the agent to fix its table gets an agent that only knows the
*document*, not the page on screen. All the tools it needs (`create_section`,
`move_section`, `edit_section_content`, split/merge — 0.3 Slice 20) already exist; it
just doesn't know what "this page" means.

**Gap 2 — broken markdown ships silently.** Measured on the live dev site
(2026-07-03): **27 of 1477 Source Sections** carry structurally broken markdown, all
of it deterministic to detect — tables missing their `|---|` separator row (renders as
plain text), ragged rows (column count ≠ header), and lone orphaned `|…|` rows.
Nothing in the pipeline or the UI flags these; the first time anyone notices is on the
generated wiki page.

**0.6 closes both**: the Wiki tab feeds the agent the section on screen (wiki-framed),
and a deterministic `engine/lint.py` detects broken markdown at every write, stores it
on the section, auto-fixes the one mechanically-safe case in the pipeline, and
surfaces the rest to the user (badges, banner) and the agent (context block) to fix
explicitly.

## Spec index

| Doc | Covers |
|---|---|
| [`01-wiki-context-and-lint.md`](01-wiki-context-and-lint.md) | The Wiki-tab context wiring (chip, `view` flag, context-block framing), the lint rule set + issue schema, the single markdown-write funnel, the auto-fix boundary, page-level `parser_artifacts` additions, and the UI/agent surfacing. |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | Tracer-bullet slices **29–31** (continuing 0.5's numbering), delivery order, per-slice Verify steps against `pdf.localhost`. |

## Principles locked (2026-07-03)

1. **Lint is deterministic and derived.** No LLM in detection; `lint_issues` is always
   recomputable from `markdown` (idempotent, safe to wipe + backfill). Detection and
   storage never block a save — a lint crash degrades to "no issues", never to a
   failed write.
2. **One write funnel.** Every write to `Source Section.markdown` goes through
   `store.set_section_markdown` (or `doc.insert`/`doc.save`, covered by the
   controller). Lint recompute rides that funnel — the same consolidation move 0.5
   makes for reference extraction ("references follow content"; here, *lint follows
   content*). A markdown write that skips lint is a bug, not a refresh button.
3. **Auto-fix only below the trust line.** The pipeline may repair markdown *before*
   a human has reviewed it (section assembly during sectionize/rebuild). After that,
   lint only *flags*; fixes go through the user or the agent, visibly. Mirrors 0.4's
   pipeline-trust stance. Pages are evidence and are never auto-repaired — only the
   assembled section product is.
4. **The agent sees what the user sees.** Whatever surface the user is on (Pages,
   Tree, Explore, Wiki), the most specific thing on screen is attached as context —
   and lint state travels with it, so "fix this page" needs no diagnosis round-trip.

## Decisions (confirmed 2026-07-03)

- **Placement:** new `specs/0.6/`; 0.5 (graph view, slices 26–28) stays a pure
  graph-view release. Implementation order between 0.5 and 0.6 is open — 0.6 has no
  dependency on 0.5.
- **Lint storage:** stored `lint_issues` JSON field on `Source Section`, recomputed at
  the write funnel + controller; patch backfills existing rows. (Not on-demand API —
  tree badges and agent context read it for free.)
- **Auto-fix policy:** missing `|---|` separator is inserted automatically during
  pipeline section assembly (new parses / explicit rebuilds — pre-review, mechanically
  safe). Existing and reviewed content is flag-only; no patch rewrites user-visible
  markdown.
- **Wiki-tab context:** reuse the existing `section` attachment chip with a
  `Wiki: <title>` label and a `view: "wiki"` flag; `context.py` adds one framing line.
  No new attachment type.

## Conventions (unchanged)

Same as [`../0.3/README.md`](../0.3/README.md): backend per the `frappe-app-dev`
skill, engine work behind the `store.py` seam, thin whitelisted APIs, frontend
frappe-ui v1 + semantic tokens, verify every slice against `pdf.localhost` before the
next, work directly on `main`.

0.5 was specced first but is unimplemented; 0.6 starts at **29** and touches none of
0.5's surface (no graph, no `Section Reference`).
