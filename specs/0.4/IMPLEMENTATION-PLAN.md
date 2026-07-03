# 0.4 Implementation Plan — Tracer-Bullet Slices

Continues the 0.3 spine (slices 17–20, see
[`../0.3/IMPLEMENTATION-PLAN.md`](../0.3/IMPLEMENTATION-PLAN.md)). Numbering
continues at **21**. Each slice cuts through every layer it touches and ends
demoable on its own.

> Source of truth for behavior is [`01-enhancements.md`](01-enhancements.md).
> This file is the *delivery order*.

## Slice map

| # | Slice | Type | Blocked by | Status |
|---|---|---|---|---|
| 21 | Taxonomy dedupe — merge patch, label-uniqueness validation, creation-path dedupe, test-leak hardening | AFK | — | ✅ |
| 22 | Unconditional dual-pass + persisted cost (`llm_cost` on page & document, gate removal in remediate/reparse/jobs) | AFK | — | ✅ |
| 23 | Review UI — split view (page image ∥ preview) + score simplification (audit score + cost, details popover) | HITL | 22 | ✅ |
| 24 | Floating agent chat window (drag/resize/minimize, persisted geometry) | HITL | — | ✅ |
| 25 | End-of-turn batched edit application (loop accumulation, aggregate event, "Applied N changes" card, broader refetch) | HITL | — | ✅ |

**Spine:** 21 → 22 → 23. **Parallelism:** 24 and 25 float — no dependency on the
pipeline slices; 25 touches only `agent/loop.py` + chat/refetch frontend and can be
built alongside 22/23.

Order rationale: 21 first because it's a live data bug users see today and every
later classify run compounds it. 22 before 23 because the cost cell in the new score
strip reads the field 22 creates.

---

## Verification

Same protocol as 0.2/0.3: verify each slice against **`pdf.localhost`**
(Administrator / admin) before starting the next; `bench` from the bench root;
worker + socketio up (`bench start`) for anything touching jobs or realtime.

Test tiers per slice:

1. **Unit** — `bench --site pdf.localhost run-tests --app wikify`; must stay green.
2. **Live-agent evals** (where the agent is involved — slices 21, 25):
   `bench --site pdf.localhost execute wikify.tests.evals.run --kwargs "{'scenario': 'all'}"`.
3. **UI walkthrough** at `/wikify` for the HITL slices.

The standing 0.4 acceptance replay: import a real PDF end-to-end → every page shows
a VLM remediation attempt and a cost; review a flagged page with image and preview
side-by-side; ask the agent to fix a section from the floating chat; the fix and the
"Applied N changes" card land together when the answer completes; sidebar TYPES has
no duplicate labels.

---

## 21 — Taxonomy dedupe

**Demo:** sidebar TYPES shows one "Introduction" (count 9); trying to create a
second one — via Desk, API, or agent — resolves to the existing type.

### What to build

- `normalize_label()` helper (casefold, strip, collapse whitespace) in the
  Section Type controller module.
- Patch `merge_duplicate_section_types`: group by normalized label; canonical =
  non-`t_` `type_name` if present, else oldest `creation`; repoint
  `Source Section.section_type`; delete losers. Idempotent (safe to re-run).
- `SectionType.validate`: reject duplicate normalized label with a message naming
  the existing `type_name`.
- `api/sections.py:create_section_type`: dedupe by normalized label →
  `{"existed": True, "name": <canonical>}`.
- Leak hardening: `addCleanup` in `test_agent.py._make_type`; eval-harness taxonomy
  teardown; suite-level Section Type count guard.

### Acceptance criteria

- Patch on the current dev DB collapses 13 "Introduction" rows to `intro`; no
  `Source Section` left pointing at a deleted type; re-running the patch is a no-op.
- Inserting a Section Type labeled `" INTRODUCTION "` fails validation;
  `create_section_type("Introduction")` returns `existed: True`.
- `run-tests` + full eval run leave `tabSection Type` row count unchanged.
- Sidebar TYPES renders a single Introduction with the merged count.

---

## 22 — Unconditional dual-pass + cost

**Demo:** import a clean, text-heavy PDF — every page still shows a remediation
attempt and a nonzero `llm_cost`; document header total matches the sum.

### What to build

- Delete `_LOW_RECALL` and the routing conditional in `engine/remediate.py`
  (`:35`, `:90`) and `engine/reparse.py` (`:30`, `:71`): VLM always runs; cleanup
  variant still computed; adoption/canonical selection logic untouched.
- `jobs/parse.py:73-76`: remediate **all** pages (not just flagged) when
  `llm.has_openrouter()`; without a key, skip with an explicit warning log entry.
- Per-page VLM failure → note in `remediation_notes`, fall back to cleanup,
  continue the job.
- New fields: `Source Page.llm_cost`, `Source Document.llm_cost` (Float) + store
  seams `add_page_cost` / `add_document_cost`; wire per-page `llm.get_metrics()`
  deltas in remediate, reparse, and classify paths.

### Acceptance criteria

- Fixture PDF with all-pass baseline pages: every page gets `remediation_method`
  set and `llm_cost > 0`; canonical still picks baseline where baseline scores
  best (always-run ≠ always-adopt).
- Single-page agent `reparse_page` uses VLM regardless of recall and increments
  both cost fields.
- No key configured → document parses baseline-only with a visible warning log
  entry; nothing crashes.
- Unit tests: gate removal (text page with recall 0.99 still routed to VLM),
  cost accumulation, VLM-failure fallback.

---

## 23 — Review UI: split view + simplified scores

**Demo:** open a flagged page — PDF page image left, rendered preview right, one
audit score and one cost number on top; sub-metrics only in the Details popover.

### What to build

- `PageReview.vue` right pane → nested `Splitpanes`: page image sub-pane
  (fit-width, zoom) ∥ Preview/Markdown tabs + source toggle (both unchanged).
  Persisted split ratio; < ~1100px falls back to the current tab layout.
- Score strip → verdict badge + **Audit score** (canonical composite, fallback
  composite) + **Cost** (`llm_cost`); "Details" popover holds recall/extra/table/
  judge + the remediation before/after strip.
- `ImportDetail` header: document `llm_cost` total beside `canonical_mean`.

### Acceptance criteria

- Image and preview visible simultaneously; drag ratio survives reload; markdown
  tab and Baseline/Remediation/Canonical toggle behave exactly as before.
- Only verdict, audit score, and cost visible by default; every previously shown
  metric reachable in the popover; null cost renders "—".
- UI walkthrough on a real import at `/wikify` (frappe-ui semantic tokens, no
  layout overflow at 1280×800 and 1512×982).

---

## 24 — Floating agent chat window

**Demo:** open chat, drag it over the tree, resize, minimize to a pulsing pill
while a run streams, restore — geometry survives a reload.

### What to build

- Reframe `AgentChatPanel.vue` from a fixed `aside` slide-over into a floating
  window: draggable header, resizable, minimize-to-FAB (pulse while `isRunning`),
  close, geometry in localStorage with viewport clamping; full-screen sheet under
  640px. Internals (`useAgentChat.js`, realtime bindings, cards, chips) untouched.

### Acceptance criteria

- Drag/resize/minimize/restore all work; window never restores off-screen; stream,
  tool, confirm, and clarify cards render identically to 0.3.
- Confirm card interaction works while the window floats over the page it's about
  to mutate (the whole point).
- No regression in `AppShell` trigger open/close.

---

## 25 — End-of-turn batched edit application

**Demo:** "fix the broken tables in sections 3 and 5" → agent streams its answer
with zero UI churn; the instant the answer completes, both sections, the tree, and
the wiki preview refresh together and the chat shows "Applied 2 changes".

### What to build

- `agent/loop.py`: replace per-tool `_emit_mutation` (`:254-258`, `:281-293`) with
  turn-local accumulation; emit one aggregated `wikify_agent_mutation`
  (`{source_documents, layers, mutations, count}`) at turn completion; flush the
  partial batch when a confirm pause begins and on `wikify_agent_error`;
  `wikify_agent_complete` payload gains `mutation_count`.
- `ImportDetail.vue:128-136`: handle the aggregate payload; extend refetch to the
  wiki-preview / explore views when their layers are listed.
- Chat panel: "Applied N changes" summary card (tools listed) rendered from the
  complete event.

### Acceptance criteria

- Multi-edit turn produces exactly **one** mutation event (assert in unit test with
  mocked realtime); DB commits per tool call are unchanged.
- Confirm-gated turn: batch-so-far flushes at pause; post-approval edits arrive in
  the completion batch; error turn flushes partial work.
- Live evals: existing 0.3 scenarios still pass (they assert DB state, so batching
  must not break them); add `batched_mutation_event` scenario asserting a single
  aggregate event per turn.
- UI walkthrough: preview visibly updates at answer-completion moment, not before;
  summary card lists the tools that ran.
