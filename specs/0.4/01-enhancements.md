# 0.4 Enhancements — Behavior Spec

Five changes. Each section states current behavior (with code anchors), target
behavior, and edge rules. Delivery order lives in
[`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md).

---

## 1. Split view — PDF page vs rendered preview

### Current

`PageReview.vue` right pane (`:size=70`) shows a scores strip, then a **3-way tab
bar** — Page / Preview / Markdown (`PageReview.vue:122-127`, `381-393`) — so the
reviewer can see the page image *or* the rendered result, never both. Reviewing a
page means tab-flipping to compare.

### Target

The right pane becomes a **nested horizontal split** (Splitpanes, already a
dependency):

```
┌────────────┬──────────────────────────────────────────────┐
│            │  header: verdict · audit score · cost        │
│  page      ├──────────────────────┬───────────────────────┤
│  list      │   PDF page image     │  [Preview | Markdown] │
│  (30%)     │   (Source Page.image)│  + source toggle      │
│            │   zoom / fit-width   │  (Baseline/Remed./    │
│            │                      │   Canonical)          │
└────────────┴──────────────────────┴───────────────────────┘
```

- Left sub-pane: the page image (`Source Page.image`), fit-to-width by default,
  click-to-zoom (or simple zoom buttons). This replaces the "Page" tab.
- Right sub-pane: the existing Preview/Markdown tabs (`MarkdownPreview` /
  read-only `CodeEditor`) and the Baseline/Remediation/Canonical source toggle
  (`PageReview.vue:133-158`) — both unchanged in behavior.
- Split ratio user-draggable; default 50/50; persisted (localStorage) like other
  pane sizes.
- Both sub-panes scroll independently. Scroll-sync is explicitly **out of scope**
  for 0.4 (page image and markdown rarely align linearly).
- Narrow viewports (< ~1100px): the sub-split collapses back to tabs
  (Page / Preview / Markdown) — the current behavior is the fallback.

---

## 2. Unconditional dual-pass (always VLM)

### Current

Pass 2 (remediation) routes per page at `wikify/engine/remediate.py:90`:

```python
method = "vlm" if (kind == "visual" or det.text_recall(gt, base_md) < _LOW_RECALL) else "cleanup"
```

with `_LOW_RECALL = 0.85` (`remediate.py:35`; mirrored in `reparse.py:30,71`).
Worse: remediation only runs at all for **flagged** pages
(`jobs/parse.py:73-76` — `flagged and llm.has_openrouter()`). A page whose baseline
recall is 0.86 with a subtly mangled table never sees the VLM.

### Target

- **Every page** goes through both passes: baseline extraction (pass 1, unchanged —
  it also produces the GT reference and page image) and **VLM remediation**
  (`vlm.parse_page_image`) — no recall gate, no flagged-only gate, no visual-only
  gate. Delete `_LOW_RECALL` from `remediate.py` and `reparse.py`; the single-page
  re-parse path always uses VLM too.
- The cheap `clean_markdown` **cleanup variant is still computed** (it's nearly
  free) so canonical selection can pick from {baseline, cleanup, VLM} — **adoption
  stays evidence-based**: `store.set_remediation` / `store.set_canonical` keep
  choosing the best composite. Always-run ≠ always-adopt.
- Judge gating (`judge_all or kind == "visual"`, `remediate.py:91`,
  `__init__.py:95`) is **unchanged** — text pages still score primarily on
  recall/extra/table vs GT.
- No `OPENROUTER_KEY` → remediation is skipped for the whole document with an
  explicit warning log entry ("VLM pass skipped: no OPENROUTER_KEY"), and the doc
  is marked accordingly. Silent cheap-mode is not allowed.
- Failure isolation: a VLM error on one page records the error in that page's
  `remediation_notes` and falls back to cleanup for that page; the job continues.

### Cost accounting (new)

Cost today is ephemeral — computed from `llm.get_metrics()` and stuffed into
Import Log Entry meta (`jobs/parse.py:42-49,87-94`); **no DocType field stores it**.
Since every page now costs real money, persist it:

- `Source Page.llm_cost` (Float, USD) — VLM + judge spend attributable to that page,
  from per-page `llm.get_metrics()` deltas. Updated additively by re-parse.
- `Source Document.llm_cost` (Float, USD) — running total for the document across
  parse, remediation, classify, and single-page re-parses (agent-chat spend is
  session-scoped and **excluded**).
- Set through new store seams (`store.add_page_cost`, `store.add_document_cost`)
  so the engine stays ORM-clean.

---

## 3. Agent edits applied at end of response (realtime, batched)

### Current

The loop emits `wikify_agent_mutation` **per mutating tool call**, mid-turn, right
after a `frappe.db.commit()` (`agent/loop.py:254-258`, `281-293`). `ImportDetail.vue`
listens (`ImportDetail.vue:128-136`) and reloads `PageReview` + `SectionTree` on
each event. Consequences: N tool calls → N refetch storms while the agent is still
talking; views the handler doesn't know about (wiki preview, explore) can end the
turn stale; the user watches the UI churn before the agent has explained anything.

### Target

**DB writes are unchanged** (tools still commit as they run — crash-safety and
confirm-gating depend on it). What changes is *when the frontend is told*:

- The loop **accumulates** mutation payloads per turn instead of emitting each one:
  `{tool, doctype(s), source_document, layer}` appended to a turn-local list at the
  point `_emit_mutation` fires today.
- At **turn completion** — the same place `wikify_agent_complete` is emitted — the
  loop emits **one** `wikify_agent_mutation` event whose payload aggregates the
  batch: `{source_documents: [...], layers: [...], mutations: [...], count}`. The
  complete event itself also carries `mutation_count` so the chat UI can render
  without waiting for the second event.
- **Confirm-gated pauses:** a turn that pauses on a confirm card flushes the batch
  accumulated *so far* when the pause begins (the user is about to look at the
  screen to decide), then continues accumulating after approval. An abandoned
  confirm therefore still leaves the UI consistent.
- **Error turns:** on `wikify_agent_error`, flush whatever accumulated — partial
  work is committed, so the UI must reflect it.

### Frontend

- `ImportDetail.vue` handler updated for the aggregate payload; still calls
  `pageReview.reload()` / `sectionTree.reload()`, and additionally refetches the
  wiki-preview and explore views when their layers appear in `payload.layers`.
- The chat panel renders an **"Applied N changes"** summary card at the end of the
  assistant message (from `mutation_count` on the complete event), listing the
  mutating tools that ran — the user sees *that* and *what* changed exactly when
  the answer lands, and the views behind the panel are already fresh.
- Views not currently mounted need nothing — frappe-ui `useList`/`useDoc` refetch
  on mount.

---

## 4. UI cleanup

### 4a. Floating agent chat window

Current: `AgentChatPanel.vue` is a fixed right slide-over `aside` (26rem,
`AgentChatPanel.vue:109-111`) mounted globally in `AppShell.vue:95`. It occupies a
third of the viewport and covers the content the agent is editing.

Target: a **floating window**:

- Default: bottom-right, ~26rem × 65vh, `shadow-2xl`, above content.
- **Draggable** by its header bar; **resizable** from edges/corner (CSS `resize` or
  pointer handlers — no new dependency).
- **Minimize** to a floating pill/FAB (bottom-right) showing a subtle pulse while a
  run is in progress; click restores. Close (×) hides entirely — reopened from the
  existing shell trigger.
- Position + size persisted in localStorage; clamped to the viewport on restore.
- All internals unchanged: message list, tool/confirm/clarify cards, context chips,
  session dropdown, model picker, realtime bindings (`useAgentChat.js` untouched by
  the reframe).
- Small screens (< 640px): full-screen sheet instead of floating (drag/resize
  disabled).

### 4b. Score simplification

Current: the detail header shows a strip of cells — Composite, Judge, Table, Recall,
Extra (`PageReview.vue:171-188`) — plus a remediation before/after strip
(`192-205`, `343-378`). Users don't act on any of it except the headline verdict.

Target — the user-facing surface shows exactly **three things**:

- **Verdict badge** (pass / escalate / review) — unchanged.
- **Audit score** — the canonical composite (falls back to `composite` when no
  remediation), rendered once, prominently.
- **Cost** — `Source Page.llm_cost` from change #2 (`$0.0000` format; "—" until
  cost lands).

Everything else — recall, extra, table, judge, baseline vs remediation composites,
remediation method/notes — moves into a **"Details" popover** (info icon next to
the audit score). Nothing is deleted from the DocType; this is purely display.
List rows keep thumbnail + page no + kind + verdict + audit score (they already do;
the remediated badge stays). Document header (`ImportDetail`) gains total
`Source Document.llm_cost` next to `canonical_mean`.

---

## 5. Duplicate "Introduction" taxonomy fix

### Diagnosis

See [`README.md`](README.md#root-cause-of-5-diagnosed-2026-07-03). Two defects:
test-fixture rows (`t_<hash>`, from `test_agent.py:225-234`) leaked into the site
DB, and `Section Type.label` has no uniqueness — creation paths dedupe only on the
scrubbed `type_name` (`api/sections.py:140-143`), so distinct keys can share a
label and the classifier taxonomy (`store.py:225-227`) offers both.

### Target

**Normalization rule** (single helper, used everywhere):
`normalize_label(label) = casefold + strip + collapse internal whitespace`.
Two Section Types are duplicates iff their normalized labels match.

1. **Patch** (`wikify/patches/…merge_duplicate_section_types.py`): group existing
   types by normalized label; canonical = the seeded/oldest row (prefer a
   non-`t_`-prefixed `type_name`, else oldest `creation`); repoint every
   `Source Section.section_type` to the canonical; delete the rest. Idempotent.
2. **Validation**: `SectionType.validate` rejects insert/rename when another row
   has the same normalized label ("Introduction already exists as `intro`").
3. **Creation-path dedupe**: `api/sections.py:create_section_type` checks the
   normalized label (not just the `type_name` key) and returns
   `{"existed": True, "name": <canonical>}` on a hit — the agent tool
   (`tools/taxonomy.py`) inherits this for free. Seed stays idempotent.
4. **Leak hardening**: `test_agent.py._make_type` registers cleanup deletion
   (`addCleanup`); the live-eval harness (`tests/evals/harness.py`) tears down any
   taxonomy rows its scenarios created (it commits, so rollback can't save it);
   a guard assertion in the test base verifies the global `Section Type` count is
   unchanged after the suite.

Frontend needs no change — once the data is merged and constrained, the sidebar
TYPES list collapses to a single "Introduction" with the summed count.
