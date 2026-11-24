# 0.7 Implementation Plan: Tracer-Bullet Slices

Numbering continues the spine (0.2: 10 to 16, 0.3: 17 to 20, 0.4: 21 to 25, 0.5: 26 to
28, 0.6: 29 to 31). This release starts at **32**. Each slice cuts through every layer
it touches and ends in something you can demo.

> The behavior is defined in [`01-review-queue.md`](01-review-queue.md). This file
> sets only the delivery order.

## Slice map

| # | Slice | Type | Blocked by | Status |
|---|---|---|---|---|
| 32 | Read-only queue: `engine/review.py` detect and reasons, the `Wikify Review Decision` DocType, read APIs, `accept` and `reopen`, the Review tab dashboard, and the item screen without apply | HITL | none | ✅ |
| 33 | Fixes with no model call: the `apply` API, `use_candidate`, `page_image`, `propagate_page` moved into the engine, section lint fixes, set section type, `apply_safe`, boundary follow-up | HITL | 32 | ✅ |
| 34 | Fixes that call a model: `reparse_page(adopt=False)`, the preview job and realtime event, the instruction box, remediation scope `failed`, reclassify | HITL | 33 | ✅ |
| 35 | Hand-off and polish: Ask the agent with a draft message, the approve-tree confirm (D1), landing on Review (D2), mobile layout | HITL | 33 | ⬜ |

Delivery is in order. 32 is valuable on its own: the user finally sees one list with
plain reasons and can mark items fine. 33 makes most items fixable in one click. 34
adds the model paths. 35 can ship in parallel with 34.

---

## Verification

Use the same protocol as 0.2 to 0.6, with one change: this bench uses
**`wikify.localhost`** (Administrator / admin). `bench start` must run for slice 34
(jobs and realtime). `bench --site wikify.localhost run-tests --app wikify` stays green
throughout.

The four imports on the site are all `Completed`, so they test **read-only mode**.
For the active flow, import fresh copies of the same PDFs:

| Fixture | Source | Expect |
|---|---|---|
| Nephrology (`IMP-2026-00004`, doc `vrdorea8gk`) | Published, 398 pages, every vision call failed | Read-only. One `doc_vision_failed` item, 34 `page_low_score` items, one `doc_unclassified` item. No per-page vision items. |
| Obstetrics and Gynaecology (`IMP-2026-00005`, doc `84uhv9pk2f`) | Published, 180 pages | Read-only. One `doc_vision_failed` item, 9 `page_low_score` items. |
| EnglishCoreXII (`IMP-2026-00002`, doc `doeblq8dro`) | Published, 101 pages, healthy run | Empty queue. Clean control. |
| Fresh import of the Obstetrics PDF | New import with a working OpenRouter key | Status `Review`, active queue. |
| Broken section | On the fresh import, break one table in the console by deleting its separator row, add a ragged row, and remove a closing fence. Restore it after the test. | One `section_lint` item with three codes |

Unit fixtures are committed strings, not site data.

After each slice, take screenshots of the dashboard and the item screen, light and
dark, at desktop and phone width. Compare them with the prototype.

---

## 32: Read-only queue

**Demo:** open the fresh Obstetrics import. The Review tab shows the summary, the
count strip, the page map and the grouped queue. Click a low-score page. The item
screen shows the page snapshot, a plain title and reason, the signals, what we
extracted, and the suggestions with previews where the data is already stored. Click
**It is fine as is**. The item moves to Reviewed, and the counts and page map update.
**Reopen** brings it back.

### What to build

- `engine/review.py`:
  - `detect(source_document)`, `REASONS`, `QUESTIONS`, `fingerprint()`.
  - A `summary` and `page_map` builder.
  - Suggestions are listed with stored previews (`use_candidate`, `page_image`, lint
    previews), but apply comes in 33.
- `store.py`: one reader that returns the page fields `detect` needs, one reader for
  section fields, and decision read and write helpers.
- The `Wikify Review Decision` DocType (section 6.1 of the spec). Add the delete line
  to `SourceDocument.on_trash`.
- `api/review.py`: `get_review`, `get_issue`, `accept`, `reopen`.
- Frontend:
  - `ImportDetail` gets the Review tab.
  - `components/ReviewDashboard.vue` and `components/ReviewItem.vue`, with the item
    selected by `?item=` like `PageReview`.
  - Data loads through `useCall`. `MarkdownPreview` renders previews and extracted
    text.
  - The read-only state follows `Completed`.
- Tests: `test_review_detect.py` covers every rule, grouping, reason selection, and
  how `accept` and the fingerprint interact.

### Verify

- The counts match SQL on the fixture documents. Write the SQL next to the test.
- The published imports show read-only mode, and their actions are disabled.
- `get_review` on Nephrology returns in under 300 ms. Measure it in the console.

## 33: Fixes with no model call

**Demo:** on the fresh import, open the broken section. Pick **Close the code block**
and click **Apply and next**. The lint code disappears and the next item opens. Open
a low-score page that has a stored vision candidate. Its preview shows the candidate.
Apply it, and the owner section's wiki preview shows the new text. Apply a candidate
on a boundary page, and the screen shows the follow-up alert with the two section
names.

### What to build

- Move `_propagate_page` from `agent/tools/reparse.py` to
  `engine/sectionize.propagate_page`. It returns data (`owners`, `rebuilt`), and the
  agent tool keeps its own wording on top. Move the `use_page_image` section mode into
  `engine/reparse.py` the same way. The agent tests must stay green without changes.
- `lint.close_fences`, `lint.pad_ragged_rows`, and the
  `rebuild_section_markdown(dry_run=True)` flag.
- `review.apply` dispatch with the fingerprint check. Every action writes a decision
  with an `outcome`.
- APIs: `apply`, `apply_safe`. Every POST is permission-checked and calls
  `assert_not_published`.
- UI: Apply and next, the safe fixes card and dialog, the `section_other_type` select,
  the follow-up alert, and history lines.
- Tests: `test_review_apply.py`, plus new cases in `test_lint.py`.

### Verify

- Each deterministic fix removes its issue on the fresh import.
- The broken section is lint-clean after its fixes. Restore the original markdown
  afterwards.
- `bench run-tests` passes, including `test_page_propagation.py` and the agent tests.

## 34: Fixes that call a model

**Demo:** open a low-score page and click **Re-read now**. The card shows a spinner,
the realtime event arrives, and the card shows the new candidate with its score.
Apply it. Type "the second column is a footnote, drop it" in the card's instruction
box and re-read, and the preview reflects the instruction. On an import whose vision pass
failed, the document item **Retry the vision pass** queues remediation with scope
`failed`, and progress streams on the import.

### What to build

- Add an `adopt` flag to `reparse_page`. It defaults to `True`, so agent behavior does
  not change.
- `jobs/review.py: preview`, with a job id per page and the
  `wikify_review_preview` realtime event.
- `api/review.preview_reparse`.
- Add the remediation scope `failed` to `trigger_remediation` and to the
  `remediate_pdf` target filter.
- Wire `doc_unclassified` to the existing `reclassify`.
- UI: the Re-read card states, the instruction textarea, and the document items.
- Tests: `adopt=False` leaves canonical alone (patch `vlm.parse_page_image`). The
  scope `failed` picks only failed pages.

### Verify

- With `bench start` running, a re-read on the fresh import arrives through realtime,
  and the cost is added to the page `llm_cost`.
- A preview with a bad key shows the error on the item and leaves the page unchanged.

## 35: Hand-off and polish

**Demo:** on a section issue, **Ask the agent** opens the agent panel with the section
chip and a draft message. Send it. The agent fixes the section, the issue disappears
from the queue, and the agent did not ask which section. Click **Approve** with
blockers open, and a confirm dialog lists them. Open a fresh import with issues. It
lands on Review.

### What to build

- The draft message builder from the issue (lint lines included), using
  `useAgentChat`.
- The confirm dialog on the action that calls `build_graph`.
- The D2 default-tab rule in `ImportDetail`.
- The phone layout: stacked panes, a smaller snapshot, a wrapping footer. Check it
  against the prototype screenshots.

### Verify

- An agent fix clears the issue with no reload. The queue refetches after the
  agent's mutation batch.
- Phone and desktop screenshots, light and dark, show no overlap and no clipped
  text.
