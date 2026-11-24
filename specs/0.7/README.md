# Wikify 0.7: Review Queue

After an import finishes, show the user one place that lists every spot we did not
convert cleanly, says why in plain words, asks one question, and offers fixes they can
preview and apply.

Prototype: https://sketch.netchamp.dev/u/gajjug004/wikify-review-queue

## The gap

The pipeline already knows which parts of a PDF went wrong. It writes that knowledge
onto rows, and then leaves it there:

| Signal | Where it lives today | Who sees it |
|---|---|---|
| Page score and verdict (`canonical_composite`, `verdict`) | `Source Page` | Pages tab, one page at a time |
| Why a page scored low (`notes`, for example "table present but not reproduced") | `Source Page.notes` | Pages tab, raw harness text |
| Remediation failed or every candidate scored low (`remediation_notes`) | `Source Page` | Pages tab, raw error text |
| Broken markdown (`lint_issues`, 0.6) | `Source Section` | Tree badge and Wiki preview banner |
| Section never classified (`section_type` = `other`) | `Source Section` | Nobody |

So the user has to open pages one by one, read harness notes, and guess what to do.
Nothing records that they looked at a page and decided it is fine.

Measured on `wikify.localhost` (2026-11-24, 4 documents, 686 pages, 345 sections):

- 43 pages have a canonical score under 0.70, and remediation marked each as "this page
  needs a human". 34 are in one document (Nephrology).
- 578 pages carry `vlm failed: 400 Client Error`. Every page of the two documents
  parsed during a bad-key window failed the vision pass. That is one problem, not 578.
- 343 of 345 sections are typed `other`. Classification never ran for those documents.
- 0 sections carry lint issues. The 0.6 pipeline auto-fix removes the common case.
- A healthy run (`EnglishCoreXII`, 101 pages) has 0 low pages, and remediation
  improved 98 of them.

This shows what the queue must do. On a healthy import it should be short or empty.
On a broken import it must group a document-wide failure into one item, not
hundreds.

## What 0.7 adds

A **Review** tab on the import, with a review dashboard and an item screen. The details
are in [`01-review-queue.md`](01-review-queue.md).

## Principles

1. **Issues are derived, decisions are stored.** Every issue is computed at read time
   from signals that already exist on `Source Page` and `Source Section`. We store only
   what a person decided. When a fix removes the signal, the issue disappears on its
   own. No sync job, no stale queue.
2. **Every fix is an existing engine path.** Re-parse a page, adopt the other
   candidate, embed the page image, rebuild a section from its pages, fix table
   markup, set a section type, retry remediation, ask the agent. 0.7 adds no new
   pipeline stage and no new model call type.
3. **Preview before apply.** Fixes that need no model show their result straight
   away. Fixes that call a model run only when the user asks, store the result as a
   candidate, and change nothing until the user applies it.
4. **Plain words.** Every issue has a title, a reason, and a question written for
   someone who has never seen a harness note. One mapping table turns codes into
   sentences (section 4 of the spec).
5. **One item per cause.** When the same failure hits many pages, the user sees one
   document-level item with one fix.

## Decisions to confirm

| # | Question | Proposed default |
|---|---|---|
| D1 | Should open "Blocks publish" issues stop the user from approving the tree (`build_graph`)? | No. Show a confirm dialog that lists them. 0.6 set the rule "lint informs, never gates". |
| D2 | Should a finished import open on the Review tab? | Yes, when it has open issues. Otherwise keep the PDF tab. |
| D3 | Rules with no signal today (unclear heading parent, duplicate sections, garbled formulas) | Not in 0.7. Each needs a new detector first. Listed in section 9. |

## Spec index

| Doc | Covers |
|---|---|
| [`01-review-queue.md`](01-review-queue.md) | Issue model, detection rules, plain-language mapping, suggestions and how each one applies, the decision DocType, APIs, and UI. |
| [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) | Slices 32 to 35, delivery order, and how to verify each slice. |

## Conventions

These are unchanged from [`../0.6/README.md`](../0.6/README.md). The backend follows
the `frappe-app-dev` skill, engine work goes behind the `store.py` seam, and APIs stay
thin and whitelisted. The frontend uses frappe-ui v1 and semantic tokens. Verify each
slice on a local site before starting the next. Numbering continues from 0.6, so this
release starts at slice 32.
