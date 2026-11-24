# 0.7: Review Queue

> This is the behavior spec. The delivery order is in
> [`IMPLEMENTATION-PLAN.md`](IMPLEMENTATION-PLAN.md) (slices 32 to 35).

## 1. Where it lives

- The import gets a new first tab, **Review** (route key `review`), next to PDF,
  Pages, Wiki and Logs. The route is `/import/:name/review`.
- One item opens at `/import/:name/review?item=<issue id>`. This matches how the Pages
  tab keeps `?page=` in the query, so a refresh or a shared link opens the same item.
- The tab shows once the import has a `source_document`. While a parse or remediation
  job runs (status `Queued`, `Parsing` or `Remediating`), it shows "Review opens when
  processing finishes" and the live progress bar the Logs tab already uses.
- After the wiki is published (status `Completed`), the tab is read-only. All actions
  are disabled with the note "Published. Edit pages in the Wiki app." This reuses
  `assert_not_published`.
- (D2) Opening `/import/:name` with no tab lands on Review when the import has open
  issues, and on PDF otherwise.

## 2. The issue model

An issue is a plain dict that `engine/review.py` builds at read time. It is never
stored.

```python
{
    "id": "page_low_score:7f3k2abc9d",   # f"{code}:{target_name}", stable across reads
    "code": "page_low_score",
    "category": "text",                  # tables | text | visuals | structure | document
    "severity": "blocks",                # blocks | review
    "target": {
        "doctype": "Source Page",        # Source Page | Source Section | Source Document
        "name": "7f3k2abc9d",
        "page_no": 37, "page_end": None,
        "section": "4.1 Daily inspection",
    },
    "title": "Some text on this page is missing",
    "reason": "The page has more text than we extracted ...",
    "question": "Which version of this page should go to the wiki?",
    "signals": [{"label": "Text match", "value": "58%"}, ...],
    "suggestions": [Suggestion, ...],    # first recommended one is preselected
    "fingerprint": "a1b2c3d4e5f6",       # sha1(target markdown)[:12]
    "history": [Decision, ...],          # what people already tried on this issue
}
```

A suggestion:

```python
{
    "id": "use_candidate",
    "label": "Use the vision model version",
    "detail": "Scores 0.94 against the page. Already run during import.",
    "recommended": True,
    "safe": False,              # True only for content-preserving fixes with no model call
    "confidence": 0.94,         # the score the fix is expected to reach, or None
    "preview": {"type": "markdown", "markdown": "..."}  # or None
    "preview_on_request": False # True: preview needs a model call (section 6.2)
}
```

`severity`:

- **blocks**: the wiki would render broken or lose content. Examples are broken table
  markup or a dropped part of a page.
- **review**: the wiki renders, but a person should check the quality or a choice.

## 3. Detection rules (v1)

`review.detect(source_document) -> list[dict]` reads pages and sections once, through
`store`, and applies the rules below. It makes no model calls and no writes. The cost
is two queries per document.

| Code | Target | Fires when | Severity | Category |
|---|---|---|---|---|
| `doc_vision_failed` | Source Document | 10 or more pages, or 20% of pages, have `vlm failed:` in `remediation_notes` and `canonical_source = baseline`. The pages still render their first parse, so this item does not mark pages on the page map. | review | document |
| `doc_unclassified` | Source Document | At least 3, and 80% or more, of included non-group sections have `section_type` empty or `other`. | review | document |
| `page_vision_failed` | Source Page | Same page condition as `doc_vision_failed`, when the document-level rule did not fire. | review | text |
| `page_low_score` | Source Page | `canonical_composite < escalate_threshold` (0.70 by default, from `Wikify Settings`). | blocks if the canonical notes contain "low text recall", else review | text, or tables when the notes contain "table" |
| `page_image_only` | Source Page | `kind = visual` and the canonical markdown has fewer than `MIN_CANONICAL_CHARS` (40) content characters besides the image. `with_page_crop` already embeds the page photo in this case. | review | visuals |
| `section_lint` | Source Section | `lint_issues` is not empty. One issue per section lists every code. | blocks | tables, or formatting for `unclosed_fence` |
| `section_other_type` | Source Section | `include_in_wiki = 1`, `section_type` is empty or `other`, and `doc_unclassified` did not fire. | review | structure |

Rules for grouping:

- Each page and each section gets at most one issue. For pages, `page_low_score`
  wins over `page_vision_failed`, which wins over `page_image_only`. The reason text
  still lists every matching note.
- When a document-level rule fires, the matching page or section rule is suppressed
  for that document. One item per cause (README principle 5).
- "Canonical notes" means `notes` when `canonical_source = baseline`, and
  `remediation_notes` otherwise. Those are the notes of the markdown the user will
  actually publish.

On the measured site, this gives:

- One `doc_vision_failed` item each for Nephrology and Obstetrics.
- One `doc_unclassified` item each for the documents that were never classified.
- 43 `page_low_score` items.
- 0 items for EnglishCoreXII.

## 4. Plain-language mapping

Harness notes and lint codes are turned into sentences in one table,
`review.REASONS`. The match is on a substring of the note or on the lint code. The
first match picks the issue title. Every match adds a line to the reason.

| Note substring or code | Title | Reason sentence |
|---|---|---|
| `low text recall` | Some text on this page is missing | The page has more text than we extracted, so part of it would not reach the wiki. |
| `high extra ratio` | This page has text that is not on the PDF | We extracted text that does not appear on the page. It may be a repeated header or made-up text. |
| `table present but not reproduced` | A table on this page did not come through | The page has a table, but the extracted text has no table. |
| `flattened <br> blob` | The layout of this page was flattened | Lines were merged into one block, so lists and tables lost their shape. |
| `picture-text wrapper` | Text inside a picture was mixed into the page | Text read from an image was dropped into the page as raw text. |
| `picture omitted` | A picture on this page was left out | The parser skipped an image on this page. |
| `every candidate scored below` | We could not get a good read of this page | We tried several ways to read this page, and none of them scored well. |
| `vlm failed` | The vision model could not read this page | The call to the vision model failed: `<first error line>`. The page uses the first parse. |
| `missing_separator` | A table lost its header line | The wiki will show this table as plain text with pipe characters. |
| `ragged_row` | Table rows have the wrong number of cells | Some rows have more or fewer cells than the header, so columns do not line up. |
| `lone_pipe_row` | A table row is cut off from its table | One table row is on its own, so the wiki will show it as text with pipes. |
| `unclosed_fence` | A code block is never closed | Everything after it on the wiki page would show as code. |

The question is fixed per code:

| Code | Question |
|---|---|
| `page_low_score`, `page_vision_failed` | Which version of this page should go to the wiki? |
| `page_image_only` | Is the picture enough, or do you need the text from it? |
| `section_lint` | How should we fix this table? (or "Where should the code block end?" for `unclosed_fence`) |
| `section_other_type` | What kind of section is this? |
| `doc_vision_failed` | The vision pass failed on N pages. Try it again? |
| `doc_unclassified` | Sections have no type yet. Classify them now? |

Signals show the raw numbers with plain labels: Text match (`text_recall`), Extra text
(`extra_ratio`), Table score, Judge score, Overall score (canonical composite), Used
version (`canonical_source`), and Lint (code names, in monospace).

## 5. Suggestions and how each applies

Every apply goes through `review.actions.apply(queue, issue, suggestion_id, value)`.
The API builds a fresh queue first. If the issue is gone, it throws "This item
changed. Reload to see the latest." If the suggestion is no longer offered for the
current content, it throws "That fix is not offered for this item any more." It then
calls one existing engine function.

### 5.1 Page issues

| Suggestion | Offered when | Preview | Apply calls |
|---|---|---|---|
| `use_candidate` | The page has a stored alternative that is not the canonical one: `remediation_markdown` when the canonical is baseline, or `baseline_markdown` when the canonical is a remediation. Recommended when its score beats the canonical score. | The stored markdown. No model call. | `store.set_canonical(page, md, composite, source)`, then `propagate_page` |
| `reparse` | `llm.has_openrouter()` | On request. See section 6.2. | Writes a candidate (`adopt=False`), then becomes `use_candidate` |
| `page_image` | The page has an `image` | The canonical markdown plus `![Page N](image)` | The `use_page_image` section mode, moved from the agent tool into `engine/reparse.py` |
| `exclude` (only for `page_image_only`) | The page has exactly one owner section, and that section covers only this page | "This page is left out of the wiki." | `api.sections.toggle_include(owner, 0)` logic |

`propagate_page(source_document, page_no)` is the existing `_propagate_page` from
`agent/tools/reparse.py`, moved to `engine/sectionize.py` so the agent and the review
share it:

- One owner section: `rebuild_section_markdown` runs and the apply finishes.
- Boundary page, with more than one owner: nothing is rebuilt. The apply result
  carries `follow_up: [{name, title}]`. The UI shows "Page 37 is shared by '4.1 Daily
  inspection' and '4.2 Lubrication'. Ask the agent to place the new text." with an
  **Ask the agent** button (section 7.3).

### 5.2 Section issues

| Suggestion | Offered for | Safe | Preview | Apply calls |
|---|---|---|---|---|
| `fix_separators` | `missing_separator` | yes | `lint.fix_table_separators(md)` | `store.set_section_markdown` |
| `close_fence` | `unclosed_fence` | yes | `lint.close_fences(md)`, a new function: append one closing fence at the end. It is idempotent and only adds text. | `store.set_section_markdown` |
| `pad_rows` | `ragged_row` | no | `lint.pad_ragged_rows(md)`, a new function: pad short rows with empty cells up to the header count. Long rows are left alone. | `store.set_section_markdown` |
| `rebuild_from_pages` | any lint issue, when the rebuilt markdown is lint-clean and no other section shares a page with this one | no | `rebuild_section_markdown(section, dry_run=True)`, a new flag that returns the markdown without writing it | `rebuild_section_markdown(section)` |
| `reparse_pages` | `ragged_row`, `lone_pipe_row`, when the section spans 3 pages or fewer | no | On request: `reparse` for each page | Adopt each page candidate, then `rebuild_section_markdown` |
| `ask_agent` | every section issue | no | none | Opens the agent panel (section 7.3) |

A fix is **safe** only when it needs no model, keeps every character of the original,
and only adds markup. `fix_separators` and `close_fence` are safe. `pad_rows` is not,
because it can line up the wrong cells.

### 5.3 Other issues

| Issue | Suggestions |
|---|---|
| `section_other_type` | One suggestion per `Section Type`, rendered as a select box and not as cards. Apply calls `api.sections.set_section_type`. |
| `doc_vision_failed` | `retry_remediation`: `trigger_remediation(import, scope="failed")`. The new scope `failed` targets pages whose `remediation_notes` contain `vlm failed:`. Recommended. |
| `doc_unclassified` | `reclassify`: the existing `reclassify(import)` job. |

### 5.4 Accept as is

Every issue has **It is fine as is**. It writes a decision with action `accepted`. The
issue stays hidden while its fingerprint matches. If the markdown changes later, the
issue comes back, because the user accepted a different text.

### 5.5 Apply safe fixes

`apply_safe(import)` runs every `safe` and `recommended` suggestion on open issues in
one request and writes one decision for each. The dashboard shows this card only when
the count is greater than 0. On a fresh import the count is usually 0, because the
pipeline already applies these fixes (0.6, `repair_markdown`). It exists for markdown
that edits broke later.

## 6. Storage and jobs

### 6.1 `Wikify Review Decision` (new DocType)

| Field | Type | Notes |
|---|---|---|
| `source_document` | Link Source Document, required, indexed | |
| `issue_id` | Data, required, indexed | For example `page_low_score:7f3k2abc9d` |
| `issue_code` | Data | For reporting |
| `target_doctype` | Link DocType | Source Page, Source Section or Source Document |
| `target_name` | Dynamic Link | |
| `fingerprint` | Data | The issue fingerprint when the decision was made |
| `action` | Select | `applied`, `accepted` |
| `suggestion` | Data | The suggestion id, empty for `accepted` |
| `instruction` | Small Text | The user's own instruction for `reparse`, if any |
| `outcome` | Small Text | For example "score 0.62 to 0.91" or "boundary page, needs placement" |

`owner` and `creation` say who decided and when. There are no other states. "Skip for
now" is not stored: it only moves the user to the next item.

Reading rules:

- An issue is **open** when no decision matches its `issue_id` and `fingerprint`.
  An `accepted` decision stores the fingerprint the user saw. An `applied` decision
  stores the fingerprint of the content *after* the fix, so a fix that leaves the
  signal in place (a page image does not raise the score) still closes the item until
  the content changes again. Background fixes (`retry_remediation`, `reclassify`)
  store no fingerprint, so their items stay open until the job removes the signal.
- `history` is every decision for that `issue_id`, newest first. When a fix did not
  remove the signal, for example a re-parse that still scores 0.62, the issue stays
  open and shows "Tried: re-read with the vision model (score 0.62)". The user sees
  that the fix ran and did not help.
- **Reviewed** means issues with a decision whose signal is now gone, or that are
  accepted. The Reviewed tab lists the decisions, one row per `issue_id`, latest
  first.
- Deleting a Source Document deletes its decisions: add one `frappe.db.delete` line
  to `SourceDocument.on_trash`, next to the existing Section Reference delete.

Permissions are the same as on `Wikify Import`: System Manager only.

### 6.2 Model previews (`reparse`)

`reparse` costs about $0.01 and takes 10 to 30 seconds, so it runs as a job:

1. The UI calls `api.review.preview_reparse(import_name, page_no, instruction="")`.
2. This enqueues `wikify.jobs.review.preview` on the `long` queue, with
   `job_id=f"review-preview:{page_name}"`, so the same page is not queued twice.
3. The job calls `reparse_page(..., adopt=False)`. This is a new flag. When it is
   false, the function stores the candidate with `store.set_remediation(...,
   adopted=False)` and skips `set_canonical`, `_recompute_canonical_mean` and section
   propagation. The default stays `True`, so the agent tool behaves as before.
4. The job publishes the `wikify_review_preview` realtime event with
   `{import, page_no, composite, ok, error}` to the import's room.
5. The UI reloads the issue. The candidate is now the stored alternative, so the
   `use_candidate` suggestion shows it with its score. Apply works as in section 5.1.

A failed preview publishes `ok: false` with the error. The issue shows "Could not
re-read the page: <error>" and keeps its other suggestions.

No new field is needed: `remediation_markdown` and `remediation_composite` already hold
"the candidate that is not canonical". A preview replaces the previous candidate.
This is fine, because the old candidate was not adopted either.

## 7. API and UI

### 7.1 API (`wikify/api/review.py`)

| Method | Verb | Returns |
|---|---|---|
| `get_review(import_name)` | GET | `{summary, page_map, issues, reviewed}`. Issues come without previews, to keep the payload small. |
| `get_issue(import_name, issue_id)` | GET | One issue with previews, history, the page image URL and the extracted markdown |
| `apply(import_name, issue_id, suggestion, value="")` | POST | `{follow_up: [...], follow_up_kind}`. `value` carries the chosen section type. `follow_up_kind` is `shared_page` (nothing was rebuilt) or `overlap` (the rebuilt section shares a page with these neighbours). |
| `accept(import_name, issue_id)` | POST | `{ok: true}` |
| `reopen(import_name, issue_id)` | POST | Deletes `accepted` decisions for that issue and clears the fingerprint on its `applied` ones, which stay as history |
| `apply_safe(import_name)` | POST | `{applied: n}` |
| `preview_reparse(import_name, page_no, instruction="")` | POST | `{queued: true}` |

`summary`:

```python
{
    "pages": 142,
    "ready": 129,         # pages with no open issue on them or on their sections
    "improved": 101,      # pages with canonical_source in ("cleanup", "vlm")
    "open": 12,
    "blocks": 5,
    "reviewed": 3,
    "safe": 0,
}
```

`page_map` is `[{page_no, state, issue_id}]`. `state` is one of `clean`, `improved`,
`review`, `blocks` or `reviewed`. A section issue marks every page in its range.

Every POST checks write permission on the import and calls `assert_not_published`.
GETs check read permission with `api.permission.assert_readable`.

### 7.2 Dashboard (Review tab)

The layout follows the prototype:

1. **Summary line and progress.** "We parsed all 142 pages. 129 are ready for the
   wiki. 12 spots need a decision from you." Then a progress bar with "3 of 15
   reviewed".
2. **Count strip.** Pages parsed, Ready for wiki, Improved automatically, Needs
   review, Blocks publish (in red when it is greater than 0).
3. **Alert.** Shown when `blocks > 0`: "N spots block publishing. They would render
   broken on the wiki."
4. **Page map.** One tile per page, colored by `state`. A flagged tile opens its item.
   A clean tile opens the Pages tab at `?page=N`. There is a tooltip per tile.
5. **Safe fixes card.** Shown only when `safe > 0`, with a confirm dialog that lists
   the fixes.
6. **Queue.** Grouped by category. Open and Reviewed tabs. Columns are page, title
   and section, suggested fix, and a severity badge. Document-level items sit on top.
   Within a group, blocking items come first, then items in page order.
7. **Empty state.** "Nothing left to review. All pages are ready." Show this
   whenever the Open tab is empty, including on a clean import.

The primary action in the tab header is **Start review** (or **Continue review**). It
opens the first open item.

### 7.3 Item screen

The layout is a split pane, with `Splitpanes` on wide screens and stacked panes on
narrow ones, like `PageReview`.

- **Left pane.** The real page snapshot (`Source Page.image`, the image the models
  saw), with an "Open in PDF tab" link. A section issue shows a page switcher across
  the section's range. There is no highlighted region in v1, because we store no
  boxes for problems.
- **Right pane.**
  - Category badge, severity badge, section title.
  - The issue title, the reason, and the signals row.
  - **What we extracted**, collapsed by default: the current markdown, shown in
    `MarkdownPreview` with a raw toggle.
  - History lines, when there are any.
  - The question, then the suggestions as radio cards. The recommended one is
    preselected. The selected card expands to show its preview in `MarkdownPreview`,
    under the label "How it will look on the wiki". A `preview_on_request` card shows
    a **Re-read now** button, its cost and its wait time instead of a preview.
  - **Something else.** For a page issue, it shows a textarea "Tell the model what to
    change", which runs `preview_reparse` with that instruction. For a section issue,
    it shows **Ask the agent**.
  - A pinned footer with **Skip for now** (go to the next item), **It is fine as is**
    and **Apply and next** (the primary action). When the selected card has no
    preview yet, the primary action is disabled.
- **Prev / next** buttons and "N of M" sit in the header.
- The screen calls `setPage` or `setSection` on `agentContext`, so the agent panel
  already knows the target. **Ask the agent** opens the panel with a draft message
  built from the issue, for example "Fix the broken table in this section. Rows 18 to
  25 have 2 cells and the header has 3." The user sends it.

After an apply, the screen moves to the next open item and shows a toast. If the apply
returned `follow_up`, it stays on the item and shows the boundary-page alert
(section 5.1).

### 7.4 Approve the tree (D1)

`build_graph` stays as it is. In the UI, the action that calls it (`SectionTree.vue`) checks
`summary.blocks`. When it is greater than 0, it shows a confirm dialog: "5 spots
still block publishing. They will render broken on the wiki." with **Review them**
and **Approve anyway** buttons.

## 8. Tests

- `test_review_detect.py`:
  - One synthetic Source Document per rule, with a clean control that has no issues.
  - Grouping: 12 `vlm failed` pages give one `doc_vision_failed` item and 0 page
    items.
  - The first matching reason picks the title.
- `test_review_apply.py`:
  - Each deterministic suggestion changes the markdown and makes the issue disappear.
  - A stale fingerprint throws.
  - `use_candidate` on a boundary page returns `follow_up` and leaves sections alone.
  - `accept` hides the issue until the markdown changes.
- `test_lint.py`: `close_fences` and `pad_ragged_rows` are idempotent and only add
  text.
- `reparse_page(adopt=False)` leaves `canonical_markdown` alone. Patch
  `vlm.parse_page_image`, as `test_remediate_pipeline.py` does.
- API: a user without write permission on the import cannot apply.
- All fixtures are committed strings, not site data.

## 9. Non-goals in 0.7

- **New detectors**: unclear heading parent, duplicate sections, garbled formulas,
  per-page heading level. Each needs a signal the pipeline does not produce yet.
  Cross-page tables and running headers are already fixed automatically
  (`table_stitch`, `finalize`), so they are not review items.
- **Problem regions on the page image.** We store no boxes. Add them when a detector
  can produce them.
- **A side-by-side diff view.** The preview is the rendered result. Add a diff when
  users ask what changed.
- **Gating generation.** See D1.
- **Review across documents** (one queue for a whole project). The API is per import.
  Add a project view once single-import review has proven useful.
