# 0.3 / 01 — Agent Wiki Editing Tools

The user must be able to fix **anything they see** in the wiki preview
(`WikiPreview.vue` over `api/wiki.render_section_preview`) or in the generated wiki
(`Wiki Document` pages) by asking the agent. This spec audits what can look broken,
maps each to the layer it lives at, and defines the tools that close every gap.

## The layer model

```
┌───────────────────────┐  sectionize   ┌────────────────────────┐  generate/sync  ┌───────────────┐
│ Source Page           │──(rebuild)──▶ │ Source Section         │──────────────▶  │ Wiki Document │
│ .canonical_markdown   │               │ .markdown  .title      │                 │ .content      │
│                       │               │ .section_type .lft/rgt │                 │ .route .title │
└───────────────────────┘               └────────────────────────┘                 └───────────────┘
     Page Review UI                        wiki PREVIEW renders this                  the REAL wiki
```

- `engine/sectionize.py:sectionize_document` rebuilds **all** sections from canonical
  pages — new rows, new names. It runs at the end of parse and remediate only. There is
  **no incremental path** from a page edit to its section today.
- `api/wiki.render_section_preview` renders `Source Section.markdown` through the wiki
  app's own `render_markdown` — the preview *is* the section, styled like the wiki.
- `engine/generate.py:_WikiGenerator` projects included sections into `Wiki Document`
  rows 1:1 (idempotent; each section tracks its `wiki_document`). Content =
  `_content_for(section)` (the section markdown, or `# Title` for empty leaves, or a
  Contents rollup for empty groups), plus pass-2 page-ref link rewriting.

**Consequence:** the 0.2 headline tool `reparse_page` writes the *leftmost* layer. The
user looks at the *middle or right* layer. Nothing connects them short of
`reparse_document` (full re-parse: expensive, confirm-gated, and — because the rebuild
assigns new section names — it **destroys manual tree curation**). This is the bug
class behind session `AGT-2026-00167`.

## Audit — everything visible, and its fix path

| User sees (preview / wiki) | Lives at | Fix path today | 0.3 |
|---|---|---|---|
| Broken table, typo, junk text, bad mermaid, ToC bleed | `Source Section.markdown` | ❌ none (only page-layer reparse, doesn't propagate) | `edit_section_content` |
| Page fixed via re-parse but preview stale | page → section seam | ❌ `reparse_document` only | `rebuild_section_from_pages` + auto-propagation |
| Preview right but generated wiki stale | section → wiki seam | ⚠️ `regenerate_wiki` (doc-wide, confirm) | `sync_wiki_page` (one page, cheap) |
| Wrong title / breadcrumb | `Source Section.title` + tree | ✅ `rename_section`, `move_section` | — |
| Page shouldn't be in wiki | `include_in_wiki` | ✅ `toggle_include_in_wiki` | — |
| Junk section should not exist at all | tree | ❌ API exists (`api/sections.py:delete_section`), no tool | `delete_section` tool |
| Two topics crammed into one page | tree + markdown | ❌ nothing | `split_section` |
| One topic shredded across several stub pages | tree + markdown | ❌ nothing | `merge_sections` |
| A page is missing ("add a glossary") | tree | ❌ sections only born via sectionize | `create_section` |
| "refer page N" link resolves to wrong page | markdown (refs re-resolve at render) | via markdown edit | covered by `edit_section_content` |
| Wrong type chip / tag | `section_type` | ✅ `set_section_type`, `create_section_type` | — |
| Scan garbled at the source | `Source Page.canonical_markdown` | ✅ `reparse_page`, `use_page_image` | + propagation & honest result strings |
| Empty group's Contents list wrong/missing | derived (rollup) | via include/move/sync | covered by structure tools + sync |

Two systemic gaps on top of the missing tools:

- **Honesty:** `reparse_page` returns `"… Canonical updated."` — technically true,
  practically misleading; the model claims the user-visible bug is fixed. The loop's
  no-op guard (`loop.py:claims_unbacked_action`) can't catch this: a tool *did* run, it
  just doesn't reach the layer the user sees.
- **Verification:** the agent verified its "fix" with `read_page` — re-reading the layer
  it had just written. It had no tool that reads what the user's screen renders.

## Tool catalogue (new + changed)

All tools follow the 0.2 pattern (`agent/registry.py` `Tool` dataclass, module-level
`TOOLS` lists, handlers call existing `api/`/`engine/` functions). New modules:
`tools/content.py` (content editing + propagation + sync) and additions to
`tools/read.py` and `tools/tree.py`.

### Content editing — `tools/content.py`

| Tool | Args | Does | Flags |
|---|---|---|---|
| `edit_section_content` | `name`, `mode` (`replace` \| `find_replace`), `content?`, `find?`, `replace?` | Write `Source Section.markdown` directly. `replace` swaps the whole body with `content`; `find_replace` swaps one **exact, unique** occurrence of `find` with `replace` (fails with a count if 0 or >1 matches — same contract as an editor find/replace, cheap and surgical). Result string reports chars changed **and reminds:** wiki preview updates immediately; generated wiki needs `sync_wiki_page`. | `mutates` |
| `rebuild_section_from_pages` | `name` | Re-derive one section's markdown from its pages' `canonical_markdown` (`page_start`–`page_end`) via `engine.loader.cleanup.clean_pages`, **without touching the tree**. If a boundary page is shared with a neighboring section (overlapping page range), the result string names the overlap and recommends `edit_section_content` for a precise fix instead — v1 adopts the whole page range and says so. | `mutates` |
| `sync_wiki_page` | `name` | Push one section's current content into its existing `wiki_document` (content-only: `_content_for` + group rollup + page-ref rewrite against the sections' recorded wiki routes). If the section has no `wiki_document` yet, or the fix needs structural changes (route/title/parent), the result string says to run `regenerate_wiki`. New engine seam `engine/generate.py:sync_section` extracted from `_WikiGenerator` (`_content_for`, `_rollup_empty_groups` for that node, `rewrite_page_refs`). | `mutates` |

### Rendered-layer reads — `tools/read.py` additions

| Tool | Args | Does |
|---|---|---|
| `read_rendered_preview` | `name` | What the user's preview shows: reuses `api.wiki.render_section_preview`, returns the **markdown the preview renders** (i.e. the section-or-rollup content after `_content_for`), `page_refs_resolved`, `include_in_wiki`, breadcrumb. This is the agent's post-fix verification tool. |
| `read_wiki_page` | `name` | The generated `Wiki Document.content` + route + modified timestamp for a section's wiki page (via `Source Section.wiki_document`). Lets the agent detect section ⇄ wiki drift and offer `sync_wiki_page`. Returns "no wiki page generated yet" when the link is empty. |

### Structure surgery — `tools/tree.py` additions

All four reuse `api/sections.py:_rebuild_tree` to keep `lft/rgt`/`level`/
`hierarchy_path` consistent, exactly like the existing tree tools.

| Tool | Args | Does | Flags |
|---|---|---|---|
| `create_section` | `title`, `parent?`, `is_group?`, `content?`, `section_type?`, `index?` | Insert a new `Source Section` under `parent` (default: document root). No page range (`page_start/page_end` empty — page refs simply never resolve to it). Unlocks "add a glossary page". New API `api.sections.create_section` shared with a future UI affordance. | `mutates` |
| `delete_section` | `name` | Wrap existing `api.sections.delete_section` (reparents children to the deleted node's parent). Result string notes the wiki page (if any) is swept on next `regenerate_wiki`. | `mutates`, `confirm` |
| `split_section` | `name`, `at_heading`, `new_title?` | Split one section into two siblings at the first line exactly matching markdown heading `at_heading`. Original keeps content above; new sibling (title = `new_title` or the heading text) gets the heading and everything below, inserted immediately after. Both keep the original page range (smallest-span ref resolution tolerates the overlap; noted in result string). | `mutates` |
| `merge_sections` | `names` (2+, same parent) | Merge siblings into the **first** listed: concatenate markdown in tree order separated by blank lines, reparent the others' children to the survivor, delete the husks (their `wiki_document`s swept on next regenerate; survivor syncs via `sync_wiki_page`). | `mutates` |

### Changed tools (honesty fixes, no signature changes)

| Tool | Change |
|---|---|
| `reparse_page` | **Auto-propagation:** after adopting a new canonical, if exactly one section's page range covers the page, call the `rebuild_section_from_pages` path for it and report both layers updated. If zero or multiple sections cover it, result string becomes: `"Page N canonical updated. NOT yet visible in the wiki preview — sections {…} span this page; run rebuild_section_from_pages or edit_section_content on the right one, then sync_wiki_page."` |
| `use_page_image` | Same propagation + result-string contract as `reparse_page`. |
| `reparse_document` | Result string already accurate (full rebuild). Add: "manual tree edits and section-level content edits will be lost" so the confirm card states the real cost. |

### System prompt additions (`agent/prompts.py`)

- The layer model, verbatim enough that the model routes edits correctly: *preview and
  wiki render Source Section markdown; pages are parse artifacts upstream of it; wiki
  pages are generated downstream of it.*
- Routing rule: *user pointing at the preview/wiki → prefer `edit_section_content`
  (surgical) over `reparse_page` (upstream, only when the parse itself is wrong).*
- Verification rule: *after any content mutation, verify with `read_rendered_preview`
  (and `read_wiki_page` when the user was looking at the generated wiki) — never
  `read_page` alone.*
- Sync rule: *if the document has a generated wiki (`read_wiki_page` non-empty), finish
  content fixes with `sync_wiki_page` and tell the user both layers are updated.*

## Guardrails

- Every new write tool sets `mutates=True` → the existing `wikify_agent_mutation`
  realtime event fires (with `frappe.db.commit()` first, per `loop.py:_emit_mutation`)
  so an open preview refetches mid-conversation.
- `delete_section` is `confirm=True` (destructive). `split_section` / `merge_sections`
  / `edit_section_content` apply directly — reversible by a follow-up edit, same rule
  as 0.2's page-scoped edits.
- `edit_section_content` `find_replace` mode must fail loudly on 0 or >1 matches (return
  the match count) — never guess. The model retries with more context, exactly like a
  human using find/replace.
- `merge_sections` validates all names share a parent and rejects groups mixed with
  leaves only if the survivor would lose children (children always reparent to the
  survivor, so mixing is fine — validate existence + common parent only).
- `sync_wiki_page` never creates or moves `Wiki Document` rows — structure stays owned
  by `regenerate_wiki`'s sweep-and-rebuild. Content-only writes use
  `frappe.db.set_value(..., update_modified=False)` consistent with
  `_rollup_empty_groups` / `_rewrite_links`.

## Out of scope (deliberate)

- **Direct `Wiki Document` editing tools.** Would fork the source of truth; the next
  regenerate wipes them. If a user edits the wiki by hand in the wiki app, that is
  their call — the agent never does.
- **Page-range re-assignment UI/tooling** (`page_start`/`page_end` edits). Split/merge
  v1 tolerates overlapping ranges; revisit if ref resolution misfires in practice.
- **Undo/history for section content.** Frappe's document versioning already records
  `Source Section` changes; a restore tool can come later if asked for.
- **`reclassify` changes.** Unaffected by this spec (tags only).

## Live-agent evals

Mocked-litellm unit tests prove the tools work when called; they cannot prove the
*model* routes to the right tool, propagates, verifies at the rendered layer, or
reports honestly — the exact failure modes that motivated 0.3. So 0.3 adds a small
**live eval harness** that runs the real `AgentRunner` loop against the real model
(per-project `agent_model` / `OPENROUTER_KEY`) and asserts on **database outcomes**,
not transcripts.

- `wikify/tests/evals/` — `harness.py` (seed fixture → run `AgentRunner.run()`
  synchronously in-process → assert layer state → report) + one module per scenario.
  Run headlessly: `bench --site pdf.localhost execute wikify.tests.evals.run --kwargs
  "{'scenario': 'fix_broken_table'}"` (or `'all'`). **Not** part of `run-tests` — costs
  real tokens; run manually per slice.
- Fixture: a small seeded `Source Document` (a handful of pages, one section with ToC
  bleed + a broken table, one clean section, optionally a generated wiki space) built
  by the harness from checked-in markdown — no LLM needed for seeding, deterministic.
- Each scenario = user prompt + **outcome assertions** (e.g. `Source Section.markdown`
  contains a well-formed table and no ToC lines; tree names/`lft`/`rgt` unchanged;
  `Wiki Document.content` updated when a wiki exists) + **honesty assertions** checked
  mechanically where possible: if the final assistant message matches the mutating-verb
  pattern (`loop.py`'s `_UNBACKED_CLAIM_RE` family), the claimed layer must actually
  differ from its pre-run snapshot.
- Auto-approve confirm-gated tools inside evals via `approved_tools` (the harness passes
  the scenario's expected confirms), so runs don't hang on the UI card.

Scenario set (grows with the slices):

| Scenario | Slice | Prompt sketch | Key assertions |
|---|---|---|---|
| `fix_broken_table` | 17 | "revision history page has the ToC too and a broken table — fix it" (the AGT-2026-00167 replay) | section markdown fixed; preview content fixed; no `reparse_*` needed; tree intact |
| `reparse_propagates` | 18 | "page 3 is garbled, re-parse it" | page canonical AND owning section both updated in one turn |
| `boundary_no_guess` | 18 | re-parse a page shared by two sections | neither section silently rewritten; final message names the ambiguity |
| `sync_generated_wiki` | 19 | content fix on a wiki-generated document | `Wiki Document.content` updated; route/title/siblings untouched |
| `split_and_delete` | 20 | "drop the ToC page entirely and split PROCEDURES in two" | delete held for confirm; split produced sibling; tree invariants hold |
| `honest_failure` | any | ask for a fix on a section the tools can't reach (e.g. wiki not generated, asks for wiki-layer fix) | no unbacked success claim; agent states the limitation / asks |

## Acceptance

Replaying session `AGT-2026-00167` ("revision history page has the ToC and a broken
markdown table — fix it") end-to-end must produce:

1. Agent reads the **section** (`read_section` / `read_rendered_preview`), not just pages.
2. Fixes land via `edit_section_content` (or `reparse_page` whose auto-propagation
   updates the owning section) — the **wiki preview shows the fix** on refetch, no
   document-wide re-parse, manual tree intact.
3. If a wiki was generated, agent runs `sync_wiki_page` and the **generated wiki page
   shows the fix**.
4. Agent's final message claims exactly what changed at which layer — verified via
   `read_rendered_preview` — and nothing more.
5. `bench --site pdf.localhost run-tests --app wikify` green, including new tests for:
   find/replace uniqueness failure, boundary-page overlap warning, sync with and
   without an existing `wiki_document`, split at missing heading (loud failure), merge
   reparenting, delete confirm gating.
6. The live-agent eval scenarios for each landed slice pass against the real model
   (see **Live-agent evals**) — run manually, not in CI.
