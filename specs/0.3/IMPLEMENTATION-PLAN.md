# 0.3 Implementation Plan — Tracer-Bullet Slices

Continues the 0.2 spine (slices 10–16, see
[`../0.2/IMPLEMENTATION-PLAN.md`](../0.2/IMPLEMENTATION-PLAN.md)). Numbering continues
at **17**. Each slice cuts through every layer it touches (engine → tool → prompt →
verify against the live preview/wiki) and ends demoable on its own.

> Source of truth for behavior is [`01-agent-editing-tools.md`](01-agent-editing-tools.md).
> This file is the *delivery order*.

## Slice map

| # | Slice | Type | Blocked by | Status |
|---|---|---|---|---|
| 17 | Content-edit spine (`edit_section_content` + `read_rendered_preview` + honest `reparse_page` strings + prompt routing) **+ eval harness** | HITL | 16 | ✅ |
| 18 | Propagation (`rebuild_section_from_pages` + `reparse_page`/`use_page_image` auto-propagation) | AFK | 17 | ✅ |
| 19 | Wiki sync (`sync_wiki_page` engine seam + tool, `read_wiki_page`) | AFK | 17 | ✅ |
| 20 | Structure surgery (`create_section` · `delete_section` · `split_section` · `merge_sections`) | HITL | 17 | ✅ |

**Spine:** 17 → 18 → 19. **Parallelism:** 20 floats — it only needs 17's verify habit
and can be built alongside 18/19.

The live-agent **eval harness ships inside Slice 17** (it's how 17 itself is accepted);
every later slice adds its scenarios to the same harness.

The guiding move: **Slice 17 is the honesty + surgical-edit skeleton** — after it, the
agent can genuinely fix what the user sees in the preview and never again claims a fix
it didn't make. 18–19 extend the same fix forward and backward along the layer chain;
20 adds shape-changing power.

---

## Verification

Same protocol as 0.1/0.2: verify each slice against **`pdf.localhost`**
(Administrator / admin) before starting the next; `bench` from the bench root. The
standing acceptance replay for every slice is the **`AGT-2026-00167` scenario**: a
document whose REVISION HISTORY section contains ToC bleed + a broken markdown table,
user says "fix it", and the fix must be *visible in the wiki preview* (and, after 19,
in the generated wiki) with the manual tree intact.

Three test tiers per slice:

1. **Unit** (mocked litellm) — tool handlers, engine seams, invariants. Part of
   `bench --site pdf.localhost run-tests --app wikify`; must stay green.
2. **Live-agent evals** (real model, real `AgentRunner.run()`, real tokens) — the
   scenarios in [`01-agent-editing-tools.md#live-agent-evals`](01-agent-editing-tools.md):
   `bench --site pdf.localhost execute wikify.tests.evals.run --kwargs "{'scenario': 'all'}"`.
   Run manually before calling a slice done — these are the only tests that catch bad
   tool routing, missing propagation, and dishonest success claims. Assertions are on
   **DB state**, not transcript wording, so they tolerate model nondeterminism; flaky
   scenario = prompt/tool-description bug, treat as a real finding.
3. **UI walkthrough** — at `/wikify`, drive the same scenario through the chat panel;
   confirm the preview refetches on `wikify_agent_mutation` and confirm cards gate
   correctly. A worker + socketio must be running (`bench start`).

---

## 17 — Content-edit spine

**Demo:** "This page's table is broken — fix it" → agent edits the section, the open
preview refetches and shows the fix, and its final message matches reality.

### What to build

- `agent/tools/content.py` with `edit_section_content` (`replace` + `find_replace`
  modes; find/replace fails loudly on 0/>1 matches with the count).
- `read_rendered_preview` in `tools/read.py`, reusing `api.wiki.render_section_preview`.
- Result-string rewrite in `tools/reparse.py`: `reparse_page` / `use_page_image` state
  that sections + preview are **not** updated (until Slice 18 lands the propagation)
  and name the covering section(s); `reparse_document` confirm text warns about losing
  manual tree + section edits.
- `agent/prompts.py`: layer model, routing rule (preview complaint → section edit, not
  page re-parse), verification rule (`read_rendered_preview` after content mutations).
- Register the new tools; `mutates=True` so `wikify_agent_mutation` refetches the preview.
- **Eval harness**: `wikify/tests/evals/` — `harness.py` (deterministic fixture seeding
  from checked-in markdown, synchronous in-process `AgentRunner.run()`, pre/post layer
  snapshots, `approved_tools` pass-through for confirm-gated scenarios, outcome +
  honesty assertions) and the `run(scenario=...)` entrypoint. Scenarios landing here:
  `fix_broken_table`, `honest_failure`.

### Acceptance criteria

- `edit_section_content` find/replace with a non-unique string returns the match count
  and changes nothing.
- After an edit, `render_section_preview` returns the new content; open `WikiPreview`
  refetches via the mutation event.
- Replay scenario: agent fixes the broken table **without** any `reparse_*` call,
  verifies via `read_rendered_preview`, and reports only what changed.
- Unit tests: both modes, uniqueness failure, mutation event emission (mocked loop).
- Live evals `fix_broken_table` + `honest_failure` pass against the real model.

---

## 18 — Propagation (page → section)

**Demo:** "Re-parse page 3, the scan is garbled" → page canonical updates **and** the
owning section + preview show it, in one turn, tree untouched.

### What to build

- `engine`: `rebuild_section_markdown(section_name)` — slice
  `Source Page.canonical_markdown` over `page_start`–`page_end` through
  `engine.loader.cleanup.clean_pages`; detect boundary-page overlap with sibling
  sections and return the overlap set.
- `rebuild_section_from_pages` tool in `tools/content.py` (result string names overlaps
  and recommends `edit_section_content` when ambiguous).
- Auto-propagation in `reparse_page` / `use_page_image`: exactly one covering section →
  rebuild it and report both layers; zero/multiple → the Slice 17 honest string.

### Acceptance criteria

- `reparse_page` on a page owned by one section updates `Source Section.markdown` and
  the preview in the same turn.
- On a boundary page shared by two sections, no section is silently rewritten; the
  result string lists both and the agent routes accordingly.
- Replay scenario variant (user explicitly asks for a re-parse) now ends with the
  preview actually fixed.
- Unit tests: single-owner propagation, overlap detection, no-tree-mutation invariant
  (section names/`lft`/`rgt` unchanged).
- Live evals `reparse_propagates` + `boundary_no_guess` added to the harness and pass.

---

## 19 — Wiki sync (section → generated wiki)

**Demo:** document already has a generated wiki; agent fixes a section, runs
`sync_wiki_page`, and the live `Wiki Document` page shows the fix — no doc-wide
regenerate, no confirm card.

### What to build

- `engine/generate.py`: extract `sync_section(section_name)` from `_WikiGenerator` —
  `_content_for` + group Contents rollup for that node + `rewrite_page_refs` against
  the recorded wiki routes; content-only `db.set_value(update_modified=False)`; returns
  a "no wiki_document / needs regenerate" signal for structural cases.
- `sync_wiki_page` + `read_wiki_page` tools; prompt sync rule (finish content fixes on
  wiki-generated documents with a sync).

### Acceptance criteria

- Sync updates only `Wiki Document.content` — route, title, parent, sort order, and
  sibling pages untouched (assert before/after).
- Section without a `wiki_document` → tool returns the regenerate guidance, writes
  nothing.
- Page refs in synced content resolve to the same routes full generation produces.
- Replay scenario on a wiki-generated document ends with the **real wiki page** fixed.
- Unit tests: content-only invariant, missing-link case, ref rewrite parity with
  `_rewrite_links`.
- Live eval `sync_generated_wiki` added to the harness and passes (fixture seeds a
  generated wiki space).

---

## 20 — Structure surgery

**Demo:** "This ToC section shouldn't be a wiki page at all — and split PROCEDURES
into one page per procedure" → agent deletes (after confirm card) and splits, preview
tree updates live.

### What to build

- `api/sections.py`: `create_section` (new); reuse existing `delete_section`,
  `_rebuild_tree`.
- `tools/tree.py`: `create_section`, `delete_section` (`confirm=True`),
  `split_section` (at exact heading match; loud failure when heading absent),
  `merge_sections` (common-parent validation, tree-order concat, child reparenting,
  husk deletion).
- Prompt: when to reshape vs. edit content.

### Acceptance criteria

- Split at a missing heading fails with the heading echoed back; nothing changes.
- Merge of non-siblings is rejected; merge survivor holds concatenated content + all
  children; husks gone; next `regenerate_wiki` sweeps their wiki pages.
- Delete holds for the confirm card (existing Slice 14 flow) and only runs on approval.
- Tree invariants (`lft/rgt`/`level`/`hierarchy_path`) hold after every operation
  (reuse `_rebuild_tree` assertions).
- UI walkthrough: tree panel + preview refetch after each operation via the mutation
  event.
- Live eval `split_and_delete` added to the harness and passes (confirm auto-approved
  via `approved_tools`).
