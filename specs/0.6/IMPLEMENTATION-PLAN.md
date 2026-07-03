# 0.6 Implementation Plan — Tracer-Bullet Slices

Continues the spine (0.2: 10–16 · 0.3: 17–20 · 0.4: 21–25 · 0.5: 26–28). Numbering
starts at **29**. Each slice cuts through every layer it touches and ends demoable on
its own.

> Source of truth for behavior is
> [`01-wiki-context-and-lint.md`](01-wiki-context-and-lint.md). This file is the
> *delivery order*.

## Slice map

| # | Slice | Type | Blocked by | Status |
|---|---|---|---|---|
| 29 | Wiki-tab agent context — `WikiGenerate` → `setSection` (wiki label + `view` flag), clear-on-close, `context.py` framing line | HITL | — | ⬜ |
| 30 | Lint spine — `engine/lint.py`, `lint_issues` field, write-funnel consolidation, pipeline auto-fix, `parser_artifacts` patterns, backfill patch | AFK | — | ⬜ |
| 31 | Lint surfacing — `lint_count` in tree payloads, tree/preview badges, `WikiPreview` banner, agent context lint line | HITL | 30 | ⬜ |

**Spine:** 29 is independent (ship any time). 30 → 31 strictly sequential — 31 renders
what 30 persists. 29 before 31 makes 31's walkthrough richer (agent fixes what the
banner shows) but is not a hard block.

The guiding move: **Slice 30 is pure backend and independently valuable** (every
future write is linted + new parses stop producing separator-less tables, regardless
of UI). 29 is the smallest slice in the release — land it first for an early win. 31
is composition on data 30 already computed.

---

## Verification

Same protocol as 0.2–0.5: verify each slice against **`pdf.localhost`**
(Administrator / admin) before starting the next; `bench` from the bench root;
`bench start` running for anything touching jobs/realtime; `run-tests --app wikify`
stays green throughout.

Standing acceptance fixtures — the live breakage is the test bed:

- `tcgg3miigh` "REVISION HISTORY" (doc `tntn4so6eo`, obs-gyn reference) —
  `missing_separator` ×2.
- `4pp40bn1kc` "2.3.2. Nursing Staff" (doc `1svt8pm07l`, nephrology) — `ragged_row`
  (6-col header, 1–5-col rows).
- `4s9m00u5qa` "Patients on Peritoneal Dialysis" (same doc) — `lone_pipe_row`.
- Unit fixtures: synthetic markdown per rule + a clean control (committed strings, not
  site data — fixture-leak rule from f79eb48 applies).

---

## 29 — Wiki-tab agent context

**Demo:** open an approved import → Wiki tab → click "REVISION HISTORY" in the
projected tree → chat panel chip reads `Wiki: REVISION HISTORY` → ask *"fix the table
on this page"* → agent edits **that** section without asking which one; preview
re-renders via the mutation batch. Close the preview → chip falls back to the
document.

### What to build

- `WikiGenerate.vue`: `watch(previewSection)` → `setSection({ name, label:
  "Wiki: <title>", view: "wiki" })`; title from the already-loaded preview rows; null →
  `setSection(null)`. The `@navigate` re-point comes free (same ref).
- `agentContext.js` / `useAgentChat.js`: pass the optional `view` key through the
  attachment shape (`{type, name, label, view?}`) untouched.
- `context.py::_render_section`: accept the attachment dict (it already gets it in
  `resolve_attachments`); when `view == "wiki"`, prepend the one framing line per
  §1.3. No other backend change.

### Acceptance criteria

- Chip appears/updates/clears exactly with the preview lifecycle (open, in-preview
  navigate, close); Tree/Pages tabs unaffected.
- Turn context block for a wiki-attached section contains the framing line; a
  tree-attached section does not (assert in a unit test on `resolve_attachments`).
- Live: "split this page into two" on a wiki preview produces `create_section` +
  content moves scoped to the right document with no id round-trip.
- The `view` key survives session persistence (`Wikify Agent Session` attachments
  round-trip).

---

## 30 — Lint spine

**Demo:** headless — `bench --site pdf.localhost execute` `lint_markdown` on the
fixture sections returns the expected codes; re-parse a PDF and separator-less tables
come out fixed in section markdown while page canonical stays untouched; every
existing section has `lint_issues` populated after the patch.

### What to build

- `engine/lint.py` per §2.2: `lint_markdown` (four codes, line numbers, ~8-issue cap)
  + `fix_table_separators` (idempotent, separator-insert only). Pure functions, no
  frappe imports.
- `Source Section.lint_issues` (JSON, hidden, read-only) +
  `SourceSection.validate` recompute (wrapped — lint failure logs and degrades to
  empty, never blocks the save).
- Funnel consolidation per §2.3: `store.set_section_markdown` computes + writes
  `lint_issues` alongside `markdown`; refactor the three raw `db.set_value` markdown
  writes (`_edit_section_content`, `split_section` head, `merge_sections`) onto it.
- Auto-fix per §2.4: `fix_table_separators` in `sectionize_document` assembly and
  `rebuild_section_markdown`. Pages never rewritten.
- `verify/deterministic.py`: `_ARTIFACT_PATTERNS` extended with the three table checks,
  implemented by importing `engine.lint` (single implementation).
- Patch `v0_6/backfill_section_lint.py`: compute `lint_issues` for all sections;
  markdown untouched.

### Acceptance criteria

- Unit: each rule fires on its synthetic fixture and stays silent on the clean
  control; `fix_table_separators` fixes the missing-separator fixture, is a no-op on
  already-valid tables, and `lint_markdown(fix_table_separators(md))` has no
  `missing_separator`.
- Every write path lands lint: agent edit, split, merge, rebuild-from-pages, pipeline
  rebuild, desk save — one test per path asserting `lint_issues` reflects the new
  markdown (this is the funnel test — a new write path that skips lint should be hard
  to add without failing something).
- Re-parsing the obs-gyn reference PDF yields zero `missing_separator` across its
  sections; the source pages' canonical markdown is byte-identical to a control parse
  with auto-fix disabled except for nothing (i.e. pages untouched).
- A page whose markdown has a separator-less table scores lower composite than the
  same page fixed (harness-level test), note includes the artifact name.
- Patch is idempotent; after it, live counts match the measured baseline (27 sections
  flagged, ±whatever re-parse fixed).
- Lint crash injection (malformed input / forced exception) → save succeeds,
  `lint_issues` empty, error logged.

---

## 31 — Lint surfacing

**Demo:** Wiki tab of the nephrology import → projected tree shows amber `⚠` badges on
the broken sections → open "Nursing Staff" → banner names the ragged rows → ask the
agent to fix → it already knows the issues, edits once, banner and badge clear live
(mutation batch → refetch).

### What to build

- `api/sections.get_tree` + `api/imports.preview_wiki`: `lint_count` per node.
- `SectionTree.vue` rows + `WikiGenerate.vue` projected rows: amber badge (`⚠ n`),
  tooltip with the first message; nothing rendered when 0.
- `WikiPreview.vue`: amber banner (same chrome as the excluded banner) listing
  issues with line numbers; `render_section_preview` payload gains `lint_issues`.
- `context.py::_render_section`: append the lint line per §2.6 when issues exist
  (independent of `view`).
- Refetch: badges/banner update on the existing `wikify_agent_mutation` +
  tree-refetch paths — verify, don't rebuild.

### Acceptance criteria

- Badge counts in both trees match `lint_issues` exactly; clean sections render
  zero extra DOM.
- Banner lists every stored issue with line numbers; absent when clean; coexists
  correctly with the excluded banner.
- Agent turn on a flagged section includes the lint line; on a clean section it
  doesn't (unit test on `resolve_attachments`).
- End-to-end on live data: agent fixes `tcgg3miigh` via chat; after the turn the
  banner is gone, the badge is gone, the wiki preview renders a real table — without
  a manual refresh.
- Dark mode: badge + banner legible in both themes (semantic tokens only).
