# 0.6 — Wiki-Tab Agent Context & Markdown Lint

> Behavior spec. Delivery order lives in
> [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) (slices 29–31).

## 1. Wiki-tab agent context

### 1.1 The gap, precisely

`agentContext.js` is the "what you're looking at" store; `AgentChatPanel` seeds its
chips from it. Today's writers:

| Surface | Writer | Chip |
|---|---|---|
| Project detail | `ProjectDetail.vue` → `setProject` | project |
| Import detail (any tab) | `ImportDetail.vue:78` → `setDocument` | document |
| Pages tab | `PageReview.vue:72` → `setPage` | page |
| Tree tab | `SectionTree.vue:87` → `setSection` | section |
| **Wiki tab** | **nobody** | document only |

`WikiGenerate.vue` tracks `previewSection` (the projected wiki page open in
`WikiPreview`) but never calls `setSection`. A wiki page **is** a `Source Section`
(one included section → one generated `Wiki Document`; `render_section_preview` is
already section-keyed), so the fix is wiring, not modeling.

### 1.2 Behavior

- Opening a wiki page preview on the Wiki tab (clicking a projected tree row, or
  navigating via an in-preview "page N" link — the `@navigate` emit) sets the section
  slot: `setSection({ name, label: "Wiki: <title>", view: "wiki" })`.
- Closing the preview (`previewOpen = false`) clears the section slot back to the
  document chip (`setSection(null)`).
- In-preview navigation re-points the chip to the new section (same call, new name).
- Existing semantics preserved: section ⇄ page slots stay mutually exclusive;
  switching to the Tree/Pages tab overwrites the slot as today.

### 1.3 The `view` flag

Attachments gain one optional key: `view: "wiki"`, carried through
`useAgentChat.run()` → `api.agent.run` → `resolve_attachments` untouched. In
`context.py::_render_section`, when `view == "wiki"` the block header gains one
framing line:

> The user is reading this section as a rendered wiki page (Wiki tab preview) — they
> see the final formatted output, not raw markdown. Formatting and structure problems
> are what they can see.

Nothing else changes server-side: scoping defaults, `_truncate`, tool behavior are
identical. No new attachment type, no schema.

### 1.4 What this unlocks (no new tools)

With the section attached, the 0.3/0.4 toolset already covers the asks that motivated
this: *"split this wiki page"* → `create_section` + `edit_section_content` (or
`split_section` at a heading); *"this table is broken"* → `edit_section_content`
find/replace; *"pull this out into its own page under X"* → `create_section` +
`move_section`. Splitting/moving reshapes the future wiki without touching page-level
canonical markdown — sections are the product layer, pages stay evidence.

## 2. Markdown lint

### 2.1 Rule set (v1)

Measured breakage on the live site (2026-07-03, 27/1477 sections) clusters into
exactly these, all deterministic:

| Code | Detects | Live example |
|---|---|---|
| `missing_separator` | Header row directly followed by data rows, no `\|---\|` line — GFM renders the whole block as plain text | `tcgg3miigh` "REVISION HISTORY" (obs-gyn reference doc) |
| `ragged_row` | Data row whose unescaped-pipe cell count ≠ header's | `4pp40bn1kc` "Nursing Staff" (6-col header, 1–5-col rows) |
| `lone_pipe_row` | A single `\|…\|` line with no adjacent table rows — an orphaned fragment | 12+ nephrology sections |
| `unclosed_fence` | Odd number of ``` ``` ``` fence markers — everything after renders as code | none live today; cheap insurance |

Parsing rules: a table block = consecutive lines that start with `|`; cell count
splits on unescaped pipes (`(?<!\\)\|`) after trimming outer pipes; the separator row
matches optional-colon dashes. Issues are capped (~8 per section) — the count matters
for badges, not an exhaustive listing.

### 2.2 `engine/lint.py`

Pure functions, no frappe imports (mirrors `verify/deterministic.py`):

```python
def lint_markdown(markdown: str) -> list[dict]:
    # [{"code": "missing_separator", "line": 16, "message": "table missing separator row"}, ...]

def fix_table_separators(markdown: str) -> str:
    # Insert the |---| row after a table header that lacks one. Column count from the
    # header. Idempotent; touches nothing else. The ONLY auto-fix in v1.
```

`ragged_row` / `lone_pipe_row` / `unclosed_fence` have no safe mechanical fix
(intent is ambiguous — merge cells? drop the row? pad?) — they are flag-only, fixed by
the agent or a VLM re-parse of the underlying pages.

### 2.3 Storage: `lint_issues` on Source Section

- New field `lint_issues` (JSON, hidden, read-only) — the `lint_markdown` output;
  empty/null when clean.
- Recompute rides **every** markdown write. Current writers and how they're covered:

| Writer | Path today | Covered by |
|---|---|---|
| Pipeline tree build | `store.replace_sections` → `doc.insert()` | controller |
| Section rebuild | `sectionize.rebuild_section_markdown` → `store.set_section_markdown` | funnel |
| Agent content edit | `tools/content.py::_edit_section_content` → raw `db.set_value` | **move onto funnel** |
| Split | `api/sections.py::split_section` → raw `db.set_value` (head) + `doc.insert` (tail) | **move onto funnel** / controller |
| Merge | `api/sections.py::merge_sections` → raw `db.set_value` | **move onto funnel** |
| Manual create | `api/sections.py::create_section` → `doc.insert` | controller |
| Desk edit | `doc.save` | controller |

  The consolidation: `store.set_section_markdown(name, markdown)` computes
  `lint_issues` and writes both fields in one `db.set_value`; the three raw
  `db.set_value` markdown writes above are refactored onto it (continues b0730a4's
  "markdown-write helpers on the store seam"). `SourceSection.validate` recomputes on
  the document path (insert/save). Lint failure is swallowed to "no issues" + logged —
  never blocks a write (README principle 1).
- Patch (`v0_6`): backfill `lint_issues` for all existing sections. Read-compute-write
  only; **does not modify markdown** (auto-fix decision).

### 2.4 Auto-fix boundary (pipeline only)

`fix_table_separators` is applied to **assembled section markdown** in exactly two
places, both pre-review:

1. `sectionize.sectionize_document` — section assembly before `replace_sections`.
2. `sectionize.rebuild_section_markdown` — the re-derive path (explicitly invoked, so
   still user-initiated repair, not silent mutation of reviewed content).

Page-level `canonical_markdown` / `baseline_markdown` are never rewritten — pages are
the evidence trail; if a page's table is broken enough to matter there, the existing
remediate/VLM loop is the fix, not string surgery.

### 2.5 Page-level detection (`verify/deterministic.py`)

Add the same three table patterns to `_ARTIFACT_PATTERNS` (via `lint.py` — one
implementation, imported): `parser_artifacts` already feeds the harness, which
penalizes composite ×0.7 and appends a `notes` entry — so broken tables now push pages
toward `review`/`escalate` at parse time, *before* sectionize. Existing pages are not
re-scored (scores are frozen until re-parse/remediate); this changes future verdicts
only.

### 2.6 Surfacing

**Tree badges.** `api/sections.get_tree` and `api/imports.preview_wiki` node payloads
gain `lint_count` (len of issues, 0 when clean). `SectionTree` rows and the Wiki tab's
projected rows show a small amber badge (`⚠ n`) when non-zero — semantic tokens, same
scale as existing row affordances, no layout shift when absent.

**WikiPreview banner.** When the previewed section has issues, an amber banner (same
pattern as the existing "excluded from wiki" banner) lists them:
`Broken markdown: table missing separator row (L16), ragged table rows (L18–25)`.
This is the moment the user is *looking at* the mangled render — the banner explains
why it looks wrong and hands the agent an obvious ask.

**Agent context.** `context.py::_render_section` appends, when `lint_issues` is
non-empty:

> Markdown lint: table missing separator row (line 16); ragged table rows (lines
> 18–25). These render broken on the wiki page — fix with `edit_section_content`
> unless the user asks otherwise.

Combined with §1, the Wiki-tab flow becomes: user sees broken table → opens chat →
agent already knows the section *and* its lint state → one confirm-free
`edit_section_content` fixes it → mutation batch (0.4 slice 25) re-renders the
preview live.

### 2.7 Non-goals (v1)

- No LLM-based lint (style, prose quality, heading depth) — deterministic structure
  only.
- No lint on `Source Page` markdown fields beyond the `parser_artifacts` signal — the
  review verdict pipeline already owns page quality.
- No auto-fix for ragged/lone-pipe/fence — revisit only with evidence that agent-fix
  round-trips are too slow in practice.
- No blocking: generation proceeds with lint warnings present (the wiki is still
  better than the PDF); lint informs, never gates.
