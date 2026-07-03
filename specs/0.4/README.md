# Wikify 0.4 — Review UX & Pipeline Trust

0.3 closed the agent's content chain. 0.4 is about **trusting what you see and paying
for what matters**: the review screen becomes a true side-by-side audit surface (PDF
page vs rendered result), the pipeline stops gambling on cheap extraction (every page
gets the VLM pass), the agent's edits land on screen the moment its answer finishes,
and the UI sheds noise — one audit score, one cost number, a chat window that floats
instead of stealing a third of the screen.

Plus one real bug found in the field (screenshot, 2026-07-03): the sidebar TYPES list
shows **"Introduction" twice**.

## The five changes

| # | Change | Kind |
|---|---|---|
| 1 | **Split view** — PDF page image and rendered preview side-by-side in Page Review | UI |
| 2 | **Unconditional dual-pass** — every page runs baseline *and* VLM remediation; no recall gate | Pipeline |
| 3 | **End-of-turn edit application** — agent edits surface on the frontend in realtime when the response completes, as one batch | Agent UX |
| 4 | **UI cleanup** — floating agent chat window; score strip reduced to audit score + cost (detail metrics demoted to a popover) | UI |
| 5 | **Duplicate "Introduction" fix** — taxonomy dedupe by label + test-leak hardening | Bug |

## Root cause of #5 (diagnosed 2026-07-03)

The dev DB holds **13 Section Types labeled "Introduction"**: the legit `intro` plus
twelve `t_xxxxxx` rows (creation timestamps match test runs on 2026-06-22 and
2026-07-02). Two independent defects:

1. **Test leakage.** `wikify/tests/test_agent.py` `_make_type()` inserts
   `Section Type` rows named `t_<hash>` — one test passes `label="Introduction"` —
   and those rows survived into the site DB (agent loop / eval paths commit
   mid-transaction, defeating test rollback).
2. **Schema allows duplicate labels.** `Section Type` autonames on `type_name`
   (unique); `label` has **no** uniqueness or normalization
   (`section_type.json:31-37`). `api/sections.py:132-151` dedups only on the
   scrubbed `type_name` key, so `intro` and `t_649a55` can both carry the label
   "Introduction". The classifier taxonomy read (`store.py:225-227`) then offers
   both to the model, and both accrue pages — hence *two* Introductions with
   non-zero counts in the sidebar.

Fix both: merge-and-constrain (patch + label-uniqueness validation) and stop the leak
(test/eval fixture cleanup). See slice 21.

## Spec index

| Doc | Covers |
|---|---|
| [`01-enhancements.md`](01-enhancements.md) | Behavior spec for all five changes: layouts, gating removal, cost accounting, event batching, dedupe rules |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | Tracer-bullet slices **21–25** (continuing 0.3's numbering), delivery order, per-slice Verify steps against `pdf.localhost` |

## Principles locked (2026-07-03)

1. **Quality over cost at parse time; cost is visible, not hidden.** Every page gets
   the VLM pass unconditionally. In exchange, cost becomes a first-class, persisted,
   user-visible number (per page and per document) — not a log-line footnote.
2. **The audit score is the product; the sub-metrics are diagnostics.** Users see
   composite (audit score) + cost. Recall/extra/table/judge and remediation
   before/after remain stored and inspectable, but live behind a details popover.
3. **Adoption stays evidence-based.** Always running VLM doesn't mean always adopting
   it — canonical selection still picks the best-scoring variant per page.
4. **One batch, one refresh.** Agent mutations apply to the DB as they happen (commits
   unchanged), but the *frontend* learns about them once — at turn completion — via a
   single aggregated event. No mid-turn refetch storms, no stale views after the
   answer lands.
5. **Labels are identity.** Two taxonomy entries with the same normalized label are
   the same type. Enforced at insert, repaired by patch, respected by every creation
   path (seed, API, agent tool).

## Conventions (unchanged)

Same as [`../0.3/README.md`](../0.3/README.md): backend per the `frappe-app-dev`
skill, frontend frappe-ui v1 with semantic tokens, verify every slice against
`pdf.localhost` before the next, work directly on `main`.
